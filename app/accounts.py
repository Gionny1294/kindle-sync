"""Local profiles and authenticated sessions. Each profile owns a separate data directory."""
import contextlib
import fcntl
import hashlib
import hmac
import os
from pathlib import Path
import secrets
import shutil
import sqlite3
import time
import unicodedata
from urllib.parse import parse_qs,urlsplit
from werkzeug.security import generate_password_hash,check_password_hash
from app import core as c

SESSION_SECONDS=12*3600
REMEMBER_SECONDS=30*86400

def connect():
    db=sqlite3.connect(c.DATA/'accounts.sqlite3',timeout=30)
    db.row_factory=sqlite3.Row
    db.execute('PRAGMA foreign_keys=ON')
    db.execute('PRAGMA busy_timeout=30000')
    return db


@contextlib.contextmanager
def transaction():
    with contextlib.closing(connect()) as db:
        db.execute('BEGIN IMMEDIATE')
        try:yield db;db.commit()
        except BaseException:db.rollback();raise


def digest(value):return hashlib.sha256(value.encode()).hexdigest()


def init():
    c.DATA.mkdir(parents=True,exist_ok=True)
    with contextlib.closing(connect()) as db:
        db.execute('PRAGMA journal_mode=WAL')
        db.executescript('''
        CREATE TABLE IF NOT EXISTS users(id TEXT PRIMARY KEY,name TEXT NOT NULL,normalized TEXT UNIQUE NOT NULL,
          password TEXT NOT NULL,storage TEXT NOT NULL,created REAL NOT NULL,language TEXT NOT NULL DEFAULT 'it');
        CREATE TABLE IF NOT EXISTS browser_sessions(token TEXT PRIMARY KEY,csrf TEXT NOT NULL,
          user_id TEXT REFERENCES users(id),expires REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS auth_attempts(key TEXT PRIMARY KEY,count INTEGER NOT NULL,until REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS installation(key TEXT PRIMARY KEY,value TEXT NOT NULL);
        ''')
        db.commit()

    with transaction() as db:
        if 'language' not in {r['name'] for r in db.execute('PRAGMA table_info(users)')}:db.execute("ALTER TABLE users ADD COLUMN language TEXT NOT NULL DEFAULT 'it'")
        if 'password_required' not in {r['name'] for r in db.execute('PRAGMA table_info(users)')}:db.execute('ALTER TABLE users ADD COLUMN password_required INTEGER NOT NULL DEFAULT 1')
        count=db.execute('SELECT count(*) FROM users').fetchone()[0]
        if count:db.execute("INSERT OR IGNORE INTO installation VALUES ('legacy_claimed','1')")
        # Existing books/SMTP settings belong only to the person holding this local claim code.
        if not count and not db.execute("SELECT 1 FROM installation WHERE key IN ('owner_code','legacy_claimed')").fetchone():
            legacy=c.CONFIG.exists()
            if (c.DATA/'state.sqlite3').exists():
                with c.use_account(None),contextlib.closing(c.connect()) as old:
                    legacy=legacy or bool(old.execute('SELECT 1 FROM files LIMIT 1').fetchone() or old.execute('SELECT 1 FROM sent LIMIT 1').fetchone())
            if legacy:
                code=secrets.token_urlsafe(18)
                (c.DATA/'account-setup-code').write_text(code+'\n')
                (c.DATA/'account-setup-code').chmod(0o600)
                db.execute("INSERT INTO installation VALUES ('owner_code',?)",(digest(code),))


def profiles():
    with contextlib.closing(connect()) as db:return [dict(r) for r in db.execute('SELECT id,name,storage,language,password_required FROM users ORDER BY created,id')]


def user(user_id):
    if not user_id:return None
    with contextlib.closing(connect()) as db:
        row=db.execute('SELECT id,name,storage,language,password_required FROM users WHERE id=?',(user_id,)).fetchone()
    return dict(row) if row else None


@contextlib.contextmanager
def access(account,exclusive=False):
    """Serialize deletion with requests and worker activity, across processes."""
    if account is None:
        with c.use_account(None):yield True
        return
    folder=c.DATA/'account-locks';folder.mkdir(exist_ok=True)
    with (folder/(account['id']+'.lock')).open('a') as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB if exclusive else fcntl.LOCK_SH)
        except BlockingIOError:raise ValueError('Account occupato. Attendi la fine delle operazioni e riprova.')
        try:
            with c.use_account(account):yield user(account['id']) is not None
        finally:fcntl.flock(lock,fcntl.LOCK_UN)


def session(token):
    if not token:return None
    with contextlib.closing(connect()) as db:
        row=db.execute('SELECT * FROM browser_sessions WHERE token=? AND expires>?',(digest(token),time.time())).fetchone()
    return dict(row) if row else None


def new_session(user_id=None,old_token=None,remember=False):
    token=secrets.token_urlsafe(40);csrf=secrets.token_urlsafe(32)
    with transaction() as db:
        db.execute('DELETE FROM browser_sessions WHERE expires<?',(time.time(),))
        if old_token:db.execute('DELETE FROM browser_sessions WHERE token=?',(digest(old_token),))
        lifetime=REMEMBER_SECONDS if user_id and remember else SESSION_SECONDS
        db.execute('INSERT INTO browser_sessions VALUES (?,?,?,?)',(digest(token),csrf,user_id,time.time()+lifetime))
    return token,csrf


def owner_code_required():
    with contextlib.closing(connect()) as db:
        return not db.execute('SELECT 1 FROM users LIMIT 1').fetchone() and bool(db.execute("SELECT 1 FROM installation WHERE key='owner_code'").fetchone())


def normalize_name(name):
    if not isinstance(name,str):raise ValueError('Scegli un nome account da 2 a 40 caratteri.')
    name=unicodedata.normalize('NFKC',name).strip()
    if not 2<=len(name)<=40 or any(not (ch.isalnum() or ch in ' ._-') for ch in name):
        raise ValueError('Scegli un nome account da 2 a 40 caratteri.')
    return name,name.casefold()


def password_valid(password):
    if not isinstance(password,str) or len(password)>256:
        raise ValueError('La password deve contenere al massimo 256 caratteri.')


def limit(db,key,maximum=8,seconds=900):
    now=time.time();row=db.execute('SELECT * FROM auth_attempts WHERE key=?',(key,)).fetchone()
    if row and row['until']>now and row['count']>=maximum:return False
    count=row['count']+1 if row and row['until']>now else 1
    until=row['until'] if row and row['until']>now else now+seconds
    db.execute('INSERT OR REPLACE INTO auth_attempts VALUES (?,?,?)',(key,count,until))
    return True


def activation_code(value):
    if not isinstance(value,str) or len(value)>2048:return ''
    value=value.strip()
    if '#activate=' in value or value.startswith('activate='):
        value=parse_qs(urlsplit(value).fragment or value).get('activate',[''])[0].strip()
    return value


def register(body,ip):
    if not isinstance(body,dict):raise ValueError('Richiesta non valida.')
    name,normalized=normalize_name(body.get('name'));password_valid(body.get('password'))
    language=body.get('language','it')
    if not isinstance(language,str):raise ValueError('Richiesta non valida.')
    language=language if language in ('it','en','fr','es','zh','ru') else 'it'
    # Hash before taking SQLite's write lock; concurrent registrations are still serialized below.
    encoded=generate_password_hash(body['password'])
    with transaction() as db:
        if not limit(db,'register:'+ip,maximum=40,seconds=3600):error='Troppi tentativi. Riprova tra qualche minuto.'
        elif db.execute('SELECT 1 FROM users WHERE normalized=?',(normalized,)).fetchone():error='Questo nome account è già utilizzato.'
        else:error=None
        if error:return None,error
        first=not db.execute('SELECT 1 FROM users LIMIT 1').fetchone()
        claim=db.execute("SELECT value FROM installation WHERE key='owner_code'").fetchone()
        if first and claim and not hmac.compare_digest(claim['value'],digest(activation_code(body.get('setup_code','')))):
            return None,'Codice dell’installazione non valido.'
        legacy=first and not db.execute("SELECT 1 FROM installation WHERE key='legacy_claimed'").fetchone()
        account={'id':secrets.token_hex(16),'name':name,'storage':'legacy' if legacy else 'private','password_required':bool(body['password']),'language':language}
        with c.use_account(account):
            if account['storage']=='private':c.library_dir().mkdir(parents=True,exist_ok=True)
            c.init()
            with c.transaction() as tenant:
                c.put(tenant,'setup_complete',0);c.put(tenant,'mail_verified',0);c.put(tenant,'paused',1)
        db.execute('INSERT INTO users(id,name,normalized,password,storage,created,language,password_required) VALUES (?,?,?,?,?,?,?,?)',(account['id'],name,normalized,encoded,account['storage'],time.time(),account['language'],account['password_required']))
        if first:
            db.execute("DELETE FROM installation WHERE key='owner_code'")
            db.execute("INSERT OR IGNORE INTO installation VALUES ('legacy_claimed','1')")
    if first:
        with contextlib.suppress(FileNotFoundError):(c.DATA/'account-setup-code').unlink()
    return account,None


def login(body,ip):
    if not isinstance(body,dict):return None,'Nome account o password non corretti.'
    name=str(body.get('name',''));password=body.get('password','')
    if not isinstance(password,str) or len(password)>256:return None,'Nome account o password non corretti.'
    with transaction() as db:
        key='login:'+ip
        if not limit(db,key):return None,'Troppi tentativi. Riprova tra qualche minuto.'
        row=db.execute('SELECT * FROM users WHERE normalized=?',(unicodedata.normalize('NFKC',name).strip().casefold(),)).fetchone()
    if row is None or not check_password_hash(row['password'],password):return None,'Nome account o password non corretti.'
    with transaction() as db:db.execute('DELETE FROM auth_attempts WHERE key=?',(key,))
    return {'id':row['id'],'name':row['name'],'storage':row['storage'],'language':row['language'],'password_required':bool(row['password_required'])},None


def change_password(user_id,body,ip):
    if not isinstance(body,dict):raise ValueError('Richiesta non valida.')
    password_valid(body.get('password'))
    current=body.get('current_password','')
    if not isinstance(current,str) or len(current)>256:raise ValueError('Password attuale non corretta.')
    with transaction() as db:
        if not limit(db,'password:'+user_id+':'+ip):raise ValueError('Troppi tentativi. Riprova tra qualche minuto.')
        row=db.execute('SELECT password FROM users WHERE id=?',(user_id,)).fetchone()
    if not row or not check_password_hash(row['password'],current):raise ValueError('Password attuale non corretta.')
    encoded=generate_password_hash(body['password'])
    with transaction() as db:
        changed=db.execute('UPDATE users SET password=?,password_required=? WHERE id=? AND password=?',
            (encoded,bool(body['password']),user_id,row['password']))
        if not changed.rowcount:raise ValueError('Password attuale non corretta.')
        db.execute('DELETE FROM browser_sessions WHERE user_id=?',(user_id,))
        db.execute('DELETE FROM auth_attempts WHERE key=?',('password:'+user_id+':'+ip,))


def delete_account(account,body,ip):
    # Caller holds exclusive access for the entire request.
    if not isinstance(body,dict):raise ValueError('Richiesta non valida.')
    current=body.get('current_password','')
    if not isinstance(current,str) or len(current)>256:raise ValueError('Password attuale non corretta.')
    with transaction() as db:
        if not limit(db,'delete:'+account['id']+':'+ip):raise ValueError('Troppi tentativi. Riprova tra qualche minuto.')
        row=db.execute('SELECT password FROM users WHERE id=?',(account['id'],)).fetchone()
    if not row or not check_password_hash(row['password'],current):raise ValueError('Password attuale non corretta.')
    # The legacy command-line sender also uses this lock.
    with (c.data_dir()/'send.lock').open('a') as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:raise ValueError('Account occupato. Attendi la fine delle operazioni e riprova.')
        with c.transaction() as db:
            for table in ['jobs','sent','files','library_checks','events','sessions','attempts','settings',
                          'reading_list','reading_file_metadata']:
                db.execute('DELETE FROM '+table)
            for key,value in {'paused':1,'setup_complete':0,'mail_verified':0,'heartbeat':0,'scan':0,'next_run':0,'interval_minutes':30}.items():c.put(db,key,value)
        with contextlib.suppress(FileNotFoundError):c.config_path().unlink()
        for name in ['uploads','tmp','covers','reader','recommendation-covers']:
            path=c.data_dir()/name
            if path.is_symlink():path.unlink()
            elif path.exists():shutil.rmtree(path)
        with transaction() as db:
            db.execute('DELETE FROM browser_sessions WHERE user_id=?',(account['id'],))
            db.execute('DELETE FROM users WHERE id=?',(account['id'],))
            for prefix in ['delete:','password:']:
                db.execute('DELETE FROM auth_attempts WHERE key LIKE ?',(prefix+account['id']+':%',))
