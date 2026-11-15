#!/usr/bin/env python3
"""Prepare a portable local installation. Import an old installation only when requested."""
import argparse
import configparser
import os
from pathlib import Path
import sqlite3


def main():
    parser=argparse.ArgumentParser(description='Prepare Kindle Sync local storage')
    parser.add_argument('--migrate-from',type=Path)
    parser.add_argument('--lan',action='store_true',help='Allow other devices on the local network')
    args=parser.parse_args()
    root=Path(__file__).resolve().parent.parent
    os.umask(0o077)
    data=root/'data';data.mkdir(exist_ok=True)
    (root/'library').mkdir(exist_ok=True)
    created_ids=None
    if not (root/'.env').exists():
        uid=getattr(os,'getuid',lambda:1000)() or 1000
        gid=getattr(os,'getgid',lambda:1000)() or 1000
        created_ids=(uid,gid)
        if getattr(os,'getuid',lambda:1000)()==0:
            os.chown(data,uid,gid);os.chown(root/'library',uid,gid)
        (root/'.env').write_text(f'APP_BIND_IP={"0.0.0.0" if args.lan else "127.0.0.1"}\nAPP_PORT=1997\nAPP_UID={uid}\nAPP_GID={gid}\nLIBRARY_HOST_PATH=./library\nCOOKIE_SECURE=false\n')
        print('Created .env and private data/. Place books in library/ or upload them from the app.')
    if args.migrate_from:
        if (data/'state.sqlite3').exists() or (data/'kindle-sync.conf').exists():raise SystemExit('Refusing to overwrite existing data.')
        source=args.migrate_from.resolve()
        if not (source/'state.sqlite3').is_file() or not (source/'kindle-sync.conf').is_file():raise SystemExit('Source configuration and database were not found.')
        with sqlite3.connect('file:'+str(source/'state.sqlite3')+'?mode=ro',uri=True) as src:
            with sqlite3.connect(data/'state.sqlite3') as dest:src.backup(dest)
        config=configparser.ConfigParser(interpolation=None);config.read(source/'kindle-sync.conf')
        config['kindle']['source_dir']='/library'
        with (data/'kindle-sync.conf').open('w') as out:config.write(out)
        print('Imported configuration and history. The first account must claim the existing library.')
    for path in [root/'.env',data/'kindle-sync.conf',data/'state.sqlite3']:
        if path.exists():
            path.chmod(0o600)
            if path.parent==data and created_ids and getattr(os,'getuid',lambda:1000)()==0:os.chown(path,*created_ids)
    print('Run: docker compose up -d --build --wait')
    print('Then open http://localhost:1997 and create your first account.')


if __name__=='__main__':main()
