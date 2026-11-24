"""Persistent queue, shared by the web process and the single sending worker."""
import configparser
import contextlib
from contextvars import ContextVar
import hashlib
import importlib.util
import os
from pathlib import Path
import sqlite3
import time
import unicodedata
import uuid
import zipfile

ROOT = Path(__file__).resolve().parent.parent
DATA = Path(os.environ.get('DATA_DIR', '/data'))
LIBRARY = Path(os.environ.get('LIBRARY_DIR', '/library'))
CONFIG = DATA / 'kindle-sync.conf'
MAX_BYTES = 45 * 1024 * 1024
EXTRA_SUFFIXES = {'.mobi', '.azw', '.azw3', '.fb2', '.odt', '.cbz'}
spec = importlib.util.spec_from_file_location('kindle_engine', ROOT / 'kindle-sync.py')
engine = importlib.util.module_from_spec(spec)
spec.loader.exec_module(engine)
ALLOWED_SUFFIXES = engine.ALLOWED_SUFFIXES | EXTRA_SUFFIXES
engine.CONFIG = CONFIG
engine.STATE_DIR = DATA


_account = ContextVar('kindle_account', default=None)


def data_dir():
    account=_account.get()
    return DATA if account is None or account['storage']=='legacy' else DATA/'users'/account['id']


def library_dir():
    account=_account.get()
    return LIBRARY if account is None or account['storage']=='legacy' else data_dir()/'library'


def download_dir():
    return Path(os.environ.get('BOOK_DOWNLOAD_DIR','/book-downloads'))


def config_path():
    account=_account.get()
    return CONFIG if account is None or account['storage']=='legacy' else data_dir()/'kindle-sync.conf'


@contextlib.contextmanager
def use_account(account):
    token=_account.set(account)
    try:yield
    finally:_account.reset(token)


def load_mail_config():
    parser=configparser.ConfigParser(interpolation=None)
    parser.read(config_path())
    if not parser.has_section('kindle') or config_errors(dict(parser['kindle'])):
        raise ValueError('Completa la configurazione di invio del tuo account.')
    return parser['kindle']


def connect():
    db = sqlite3.connect(data_dir() / 'state.sqlite3', timeout=30)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA busy_timeout=30000')
    db.execute('PRAGMA foreign_keys=ON')
    return db


@contextlib.contextmanager
def transaction():
    db = connect()
    try:
        db.execute('BEGIN IMMEDIATE')
        yield db
        db.commit()
    except BaseException:
        db.rollback()
        raise
    finally:
        db.close()


def init():
    data_dir().mkdir(parents=True, exist_ok=True)
    (data_dir() / 'uploads').mkdir(exist_ok=True)
    (data_dir() / 'tmp').mkdir(exist_ok=True)
    (data_dir() / 'covers').mkdir(exist_ok=True)
    with contextlib.closing(connect()) as db:
        db.execute('PRAGMA journal_mode=WAL')
        db.executescript('''
        CREATE TABLE IF NOT EXISTS sent(sha256 TEXT PRIMARY KEY,path TEXT NOT NULL,sent_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS files(id TEXT PRIMARY KEY,name TEXT NOT NULL,path TEXT UNIQUE NOT NULL,
          digest TEXT NOT NULL,size INTEGER NOT NULL,mtime REAL NOT NULL,origin TEXT NOT NULL,
          available INTEGER NOT NULL DEFAULT 1,deleted INTEGER NOT NULL DEFAULT 0,created REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS jobs(id INTEGER PRIMARY KEY,file_id TEXT NOT NULL REFERENCES files(id),
          digest TEXT NOT NULL,name TEXT NOT NULL,status TEXT NOT NULL,source TEXT NOT NULL,
          created REAL NOT NULL,started REAL,finished REAL,detail TEXT NOT NULL DEFAULT '');
        CREATE UNIQUE INDEX IF NOT EXISTS one_active_digest ON jobs(digest) WHERE status IN ('queued','sending');
        CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY,value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY,created REAL NOT NULL,message TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS sessions(token TEXT PRIMARY KEY,csrf TEXT NOT NULL,expires REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS attempts(ip TEXT PRIMARY KEY,count INTEGER NOT NULL,until REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS library_checks(id INTEGER PRIMARY KEY,send_now INTEGER NOT NULL,
          status TEXT NOT NULL,created REAL NOT NULL,started REAL,finished REAL,
          checked INTEGER NOT NULL DEFAULT 0,new_files INTEGER NOT NULL DEFAULT 0,
          queued INTEGER NOT NULL DEFAULT 0,uncertain INTEGER NOT NULL DEFAULT 0,
          invalid INTEGER NOT NULL DEFAULT 0,detail TEXT NOT NULL DEFAULT '');
        CREATE UNIQUE INDEX IF NOT EXISTS one_library_check ON library_checks((1))
          WHERE status IN ('queued','running');
        CREATE TABLE IF NOT EXISTS reading_list(id TEXT PRIMARY KEY,title TEXT NOT NULL,author TEXT NOT NULL,
          isbn TEXT NOT NULL,url TEXT NOT NULL,cover_url TEXT,file_digest TEXT,created REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS reading_file_metadata(digest TEXT PRIMARY KEY,title TEXT NOT NULL,
          author TEXT NOT NULL,isbn TEXT NOT NULL);
        ''')
        if 'auto_send' not in {r['name'] for r in db.execute('PRAGMA table_info(files)')}:
            db.execute('ALTER TABLE files ADD COLUMN auto_send INTEGER NOT NULL DEFAULT 1')
        defaults = {'interval_minutes': '30', 'paused': '0' if not config_errors() else '1', 'next_run': str(time.time()+300), 'heartbeat': '0', 'scan': '0', 'setup_complete':'1' if not config_errors() else '0','mail_verified':'1' if not config_errors() else '0'}
        db.executemany('INSERT OR IGNORE INTO settings VALUES (?,?)', defaults.items())
        db.commit()


def setting(db, key):
    return db.execute('SELECT value FROM settings WHERE key=?', (key,)).fetchone()[0]


def put(db, key, value):
    db.execute('INSERT OR REPLACE INTO settings VALUES (?,?)', (key, str(value)))


def event(db, message):
    db.execute('INSERT INTO events(created,message) VALUES (?,?)', (time.time(), message))
    db.execute('DELETE FROM events WHERE id NOT IN (SELECT id FROM events ORDER BY id DESC LIMIT 300)')


def config():
    p = configparser.ConfigParser(interpolation=None)
    p.read(config_path())
    return dict(p['kindle']) if p.has_section('kindle') else {}


def config_errors(cfg=None):
    c = cfg if cfg is not None else config()
    errors = []
    for field in ['kindle_address', 'smtp_host', 'smtp_user', 'smtp_password']:
        if not c.get(field, '').strip():
            errors.append(field)
    for key in ['kindle_address', 'smtp_user']:
        val = c.get(key, '')
        if val and ('@' not in val or any(ch.isspace() for ch in val) or '\r' in val or '\n' in val):
            errors.append(key)
    host = c.get('smtp_host', '')
    if host and (len(host)>253 or any(ch not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.-' for ch in host)):
        errors.append('smtp_host')
    try:
        if not 1 <= int(c.get('smtp_port', '465')) <= 65535:
            errors.append('smtp_port')
    except ValueError:
        errors.append('smtp_port')
    if c.get('smtp_ssl', 'true') not in ['true','false']:
        errors.append('smtp_ssl')
    return sorted(set(errors))


def save_config(changes):
    if not isinstance(changes, dict):
        raise ValueError("Configurazione non valida.")
    # Configuration is outside the image and never returned to the browser.
    import fcntl
    with (data_dir() / 'config.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        c = config()
        for k, v in changes.items():
            if k in ['kindle_address','smtp_host','smtp_port','smtp_user','smtp_password','smtp_ssl'] and v != '':
                if not isinstance(v, str) or len(v)>1024 or any(x in v for x in ['\r','\n','\x00']):
                    raise ValueError('Valore di configurazione non valido.')
                c[k] = v.strip() if k != 'smtp_password' else v
        if config_errors(c):
            raise ValueError('Controlla indirizzi e-mail, server, porta e campi obbligatori.')
        c['source_dir'] = str(library_dir())
        p = configparser.ConfigParser(interpolation=None); p['kindle'] = c
        tmp = data_dir() / ('config-' + uuid.uuid4().hex)
        with tmp.open('x') as h:
            os.chmod(tmp, 0o600); p.write(h); h.flush(); os.fsync(h.fileno())
        tmp.replace(config_path())
        with transaction() as db:put(db,'mail_verified',0)


def clean_name(name):
    name = unicodedata.normalize('NFC', name or '')
    if not name or len(name.encode('utf-8'))>220 or name in ['.','..'] or any(ord(c)<32 for c in name) or '/' in name or '\\' in name:
        raise ValueError('Nome file non valido. Usa un nome più breve senza barre.')
    if Path(name).suffix.lower() not in ALLOWED_SUFFIXES:
        raise ValueError('Formato non supportato. Scegli EPUB, PDF, DOC, DOCX, TXT, RTF, HTML, MOBI, AZW, AZW3, FB2, ODT o CBZ.')
    return name


def validate(path, name=None):
    suffix = Path(name or path.name).suffix.lower()
    if suffix not in ALLOWED_SUFFIXES:
        raise ValueError('Formato non supportato.')
    size = path.stat().st_size
    if not 0 < size <= MAX_BYTES:
        raise ValueError('Il file deve avere un contenuto e non superare 45 MB.')
    with path.open('rb') as f:
        head = f.read(1024)
    if suffix == '.pdf' and b'%PDF-' not in head:
        raise ValueError('Il contenuto non è un PDF valido.')
    if suffix in ['.epub','.docx','.odt','.cbz']:
        try:
            with zipfile.ZipFile(path) as z:
                names = z.namelist()
                if len(names)>20000 or sum(i.file_size for i in z.infolist())>512*1024*1024:
                    raise ValueError('Archivio del documento troppo complesso.')
                marker = {'.epub':'META-INF/container.xml','.docx':'[Content_Types].xml','.odt':'content.xml'}.get(suffix)
                if marker and marker not in names:
                    raise ValueError('Il contenuto non corrisponde al formato scelto.')
                if suffix=='.cbz' and not any(Path(n).suffix.lower() in {'.png','.jpg','.jpeg','.webp'} for n in names):
                    raise ValueError('Il fumetto non contiene immagini leggibili.')
        except zipfile.BadZipFile:
            raise ValueError('Documento danneggiato o formato non valido.') from None
    if suffix == '.doc' and not head.startswith(b'\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1'):
        raise ValueError('Il contenuto non è un documento DOC valido.')
    if suffix == '.rtf' and not head.lstrip().startswith(b'{\\rtf'):
        raise ValueError('Il contenuto non è un documento RTF valido.')
    if suffix in {'.mobi','.azw','.azw3'} and (len(head)<68 or head[60:68] not in {b'BOOKMOBI',b'TEXtREAd'}):
        raise ValueError('Il contenuto non è un libro MOBI/AZW valido.')
    if suffix=='.fb2' and b'FictionBook' not in head:
        raise ValueError('Il contenuto non è un documento FB2 valido.')
    if suffix in ['.txt','.htm','.html'] and b'\0' in head:
        raise ValueError('Il file contiene dati binari incompatibili con il formato.')
    return size


def safe_path(row):
    p = Path(row['path'])
    base = download_dir() if row['origin']=='download' else (data_dir() / 'uploads' if row['origin'] == 'upload' else library_dir())
    if p.is_symlink() or not p.resolve().is_relative_to(base.resolve()) or not p.is_file():
        raise ValueError('Il file non è più disponibile nella cartella autorizzata.')
    return p


def register(db, path, name, origin):
    size = validate(path, name)
    before = path.stat()
    digest = engine.sha256(path)
    after = path.stat()
    if (before.st_size,before.st_mtime_ns)!=(after.st_size,after.st_mtime_ns):
        raise ValueError('Il file sta cambiando: sarà controllato al prossimo passaggio.')
    old = db.execute('SELECT * FROM files WHERE path=?', (str(path),)).fetchone()
    fid = old['id'] if old else uuid.uuid4().hex
    db.execute('''INSERT INTO files(id,name,path,digest,size,mtime,origin,created,auto_send) VALUES (?,?,?,?,?,?,?,?,?)
       ON CONFLICT(path) DO UPDATE SET digest=excluded.digest,size=excluded.size,mtime=excluded.mtime,available=1''',
       (fid,name,str(path),digest,size,after.st_mtime,origin,time.time(),int(path.suffix.lower() in engine.ALLOWED_SUFFIXES)))
    if Path(name).suffix.lower()=='.epub':
        from app.reading_list import index_epub
        index_epub(db,path,digest)
    return fid


def enqueue(db, ids, source):
    results = []
    for fid in dict.fromkeys(ids):
        f = db.execute('SELECT * FROM files WHERE id=? AND deleted=0 AND available=1',(fid,)).fetchone()
        if not f:
            raise ValueError('Uno dei file non è disponibile. Aggiorna l’elenco.')
        if source=='automatic' and not f['auto_send']:
            continue
        if db.execute('SELECT 1 FROM sent WHERE sha256=?',(f['digest'],)).fetchone():
            results.append({'file_id':fid,'status':'accepted','detail':'Già registrato: nessun nuovo invio.'}); continue
        active = db.execute("SELECT id,status FROM jobs WHERE digest=? AND status IN ('queued','sending')",(f['digest'],)).fetchone()
        if active:
            if source!='automatic' and active['status']=='queued':
                db.execute('UPDATE jobs SET source=? WHERE id=?',(source,active['id']))
            results.append({'file_id':fid,'job_id':active['id'],'status':active['status']}); continue
        last = db.execute('SELECT status FROM jobs WHERE digest=? ORDER BY id DESC LIMIT 1',(f['digest'],)).fetchone()
        if last and last['status']=='uncertain':
            if source=='automatic':
                continue
            raise ValueError('Esito incerto: controlla il Kindle prima di autorizzare un nuovo tentativo dalla cronologia.')
        if source=='automatic' and last and last['status'] not in {'failed','cancelled'}:
            continue
        cur = db.execute("INSERT INTO jobs(file_id,digest,name,status,source,created) VALUES (?,?,?,'queued',?,?)",(fid,f['digest'],f['name'],source,time.time()))
        results.append({'file_id':fid,'job_id':cur.lastrowid,'status':'queued'})
    return results
