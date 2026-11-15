"""Local administrator recovery. Run inside the app container; no web endpoint."""
import argparse
import contextlib
import getpass
from werkzeug.security import generate_password_hash
from app import accounts,core as c


def reset_password(name,password):
    accounts.password_valid(password)
    _,normalized=accounts.normalize_name(name)
    encoded=generate_password_hash(password)
    with accounts.transaction() as db:
        user=db.execute('SELECT id FROM users WHERE normalized=?',(normalized,)).fetchone()
        if not user:raise ValueError('Account not found.')
        db.execute('UPDATE users SET password=?,password_required=? WHERE id=?',(encoded,bool(password),user['id']))
        db.execute('DELETE FROM browser_sessions WHERE user_id=?',(user['id'],))


def main():
    parser=argparse.ArgumentParser(description='Local Kindle Sync account recovery')
    parser.add_argument('action',choices=['list','reset','owner-code'])
    parser.add_argument('name',nargs='?')
    args=parser.parse_args();c.init();accounts.init()
    if args.action=='list':
        for user in accounts.profiles():print(user['id'],user['name'])
    elif args.action=='owner-code':
        path=c.DATA/'account-setup-code'
        if path.exists():print('http://localhost:1997/#activate='+path.read_text().strip())
        else:print('No activation code is required. The first account may already exist.')
    else:
        if not args.name:parser.error('Provide the account name to reset.')
        password=getpass.getpass('New account password: ')
        if password!=getpass.getpass('Repeat password: '):raise SystemExit('Passwords do not match.')
        try:reset_password(args.name,password)
        except ValueError as error:raise SystemExit(str(error))
        print('Password updated. Previous sessions for this account were signed out.')


if __name__=='__main__':main()
