"""Request installation updates through the host launcher."""
import contextlib
import fcntl
import json
import os
from pathlib import Path
import re
import time
import uuid

from app import accounts, core

VERSION_PATH = Path(__file__).resolve().parent.parent / 'VERSION'
STATUS_PATH = Path('/update-status/status.json')
REQUEST_NAME = 'kindle-update-request.json'
VERSION = re.compile(r'(0|[1-9][0-9]{0,8})\.(0|[1-9][0-9]{0,8})\.(0|[1-9][0-9]{0,8})')
PHASES = {'idle', 'checking', 'ready', 'installing', 'installed', 'check_failed', 'install_failed'}


def can_manage(account_id):
    with contextlib.closing(accounts.connect()) as db:
        first = db.execute('SELECT id FROM users ORDER BY created,id LIMIT 1').fetchone()
    return bool(first and first['id'] == account_id)


def read_json(path):
    try:
        with path.open('rb') as stream:
            data = json.loads(stream.read(1024))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def snapshot():
    current = VERSION_PATH.read_text().strip()
    progress = read_json(STATUS_PATH)
    heartbeat = progress.get('heartbeat')
    phase = progress.get('phase')
    online = isinstance(heartbeat, int) and abs(time.time() - heartbeat) <= 45 and phase in PHASES
    latest = progress.get('latest')
    if not (online and isinstance(latest, str) and VERSION.fullmatch(latest)
            and VERSION.fullmatch(current) and version_parts(latest) > version_parts(current)):
        latest = ''
    return {'current': current, 'latest': latest, 'phase': phase if online else 'offline'}


def version_parts(version):
    return tuple(map(int, version.split('.')))


def status(account_id):
    progress = snapshot()
    if progress['phase'] not in ('offline', 'checking', 'installing'):
        pending = read_json(core.DATA / REQUEST_NAME)
        issued = pending.get('issued')
        if isinstance(issued, int) and abs(time.time() - issued) <= 600:
            progress['phase'] = 'installing' if pending.get('action') == 'install' else 'checking'
    return dict(progress, can_manage=can_manage(account_id))


def enqueue(body):
    action = body.get('action')
    if action not in ('check', 'install'):
        raise ValueError('Richiesta non valida.')

    target = core.DATA / REQUEST_NAME
    with (core.DATA / 'kindle-update-request.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        progress = snapshot()
        if progress['phase'] == 'offline':
            raise ValueError('Avvia Kindle Sync con il launcher sul computer che lo ospita.')
        previous = read_json(target)
        previous_time = previous.get('issued')
        if progress['phase'] in ('installing', 'checking') or (
                isinstance(previous_time, int) and abs(time.time() - previous_time) <= 600):
            raise ValueError('È già in corso un’operazione di aggiornamento.')
        version = body.get('version', '')
        if action == 'install' and (not progress['latest'] or version != progress['latest']):
            raise ValueError('La versione disponibile è cambiata. Controlla gli aggiornamenti.')

        temp = target.with_name(f'.kindle-update-{uuid.uuid4().hex}')
        try:
            temp.write_text(json.dumps({'action': action, 'version': version if action == 'install' else '',
                                       'issued': int(time.time())}))
            os.replace(temp, target)
        finally:
            temp.unlink(missing_ok=True)
    return {'phase': 'installing' if action == 'install' else 'checking'}
