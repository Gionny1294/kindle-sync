import contextlib
import fcntl
import os
from pathlib import Path
import shutil
import signal
import smtplib
import socket
import ssl
import time
import threading
import uuid
from app import core as c, accounts

STOP = False


def stop(*_):
    global STOP
    STOP = True


def error_message(exc):
    # Never include SMTP response text: it can contain addresses and credentials.
    if isinstance(exc, smtplib.SMTPAuthenticationError):
        return 'Autenticazione SMTP rifiutata. Controlla utente e password per app.'
    if isinstance(exc, smtplib.SMTPRecipientsRefused):
        return 'Destinatario rifiutato dal server SMTP. Controlla l’indirizzo Kindle.'
    if isinstance(exc, (smtplib.SMTPSenderRefused, smtplib.SMTPDataError)):
        return 'Messaggio rifiutato dal server SMTP. Controlla dimensione e autorizzazioni.'
    if isinstance(exc, ssl.SSLError):
        return 'Connessione TLS non verificata. Controlla server e certificato.'
    if isinstance(exc, (ConnectionRefusedError, socket.gaierror, smtplib.SMTPConnectError)):
        return 'Server SMTP non raggiungibile. Controlla rete, server e porta.'
    if isinstance(exc, ValueError):
        return str(exc)
    return 'Connessione interrotta o risposta non disponibile. L’accettazione SMTP non è verificabile.'


def scan(force=False):
    result={'available':True,'checked':0,'new_files':0,'invalid':0}
    if not c.library_dir().is_dir():
        with c.transaction() as db:
            c.event(db,'Cartella libri non disponibile: controlla il volume Docker.')
        result['available']=False
        return result
    seen = set()
    for path in c.library_dir().rglob('*'):
        if STOP:
            raise ValueError('Controllo interrotto dal riavvio del servizio. Verrà ripreso.')
        try:
            if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(c.library_dir().resolve()) or path.suffix.lower() not in c.ALLOWED_SUFFIXES:
                continue
            seen.add(str(path))
            result['checked']+=1
            stat = path.stat()
            if not force and time.time()-stat.st_mtime<10:
                continue
            with contextlib.closing(c.connect()) as db:
                old=db.execute('SELECT * FROM files WHERE path=?',(str(path),)).fetchone()
            if not force and old and old['size']==stat.st_size and old['mtime']==stat.st_mtime and old['available']:
                continue
            with c.transaction() as db:
                c.register(db,path,path.name,'library')
            if not old:
                result['new_files']+=1
        except (ValueError,OSError):
            # Do not flood logs with unsupported existing files or reveal paths.
            result['invalid']+=1
            continue
    with c.transaction() as db:
        for row in db.execute("SELECT id,path FROM files WHERE origin='library' AND available=1").fetchall():
            if row['path'] not in seen:
                db.execute('UPDATE files SET available=0 WHERE id=?',(row['id'],))
        c.put(db,'scan',time.time())
    return result


def recover_uploads():
    # A completed atomic upload can outlive a web crash before the DB commit.
    # Partial uploads are never registered or sent.
    for path in (c.data_dir()/'uploads').glob('*/*'):
        if path.name=='.uploading' or path.is_symlink() or not path.is_file():
            continue
        try:
            if time.time()-path.stat().st_mtime<10:
                continue
            with c.transaction() as db:
                if not db.execute('SELECT 1 FROM files WHERE path=?',(str(path),)).fetchone():
                    c.register(db,path,path.name,'upload')
                    c.event(db,'Caricamento completato recuperato dopo un riavvio.')
        except (OSError,ValueError):
            continue


def run_one():
    # Also used by the legacy CLI. All processes share the sent ledger.
    with (c.data_dir()/'send.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        with c.transaction() as db:
            job=db.execute("SELECT * FROM jobs WHERE status='queued' ORDER BY CASE WHEN source='automatic' THEN 1 ELSE 0 END,id LIMIT 1").fetchone()
            if not job:
                return False
            f=db.execute('SELECT * FROM files WHERE id=?',(job['file_id'],)).fetchone()
            if db.execute('SELECT 1 FROM sent WHERE sha256=?',(job['digest'],)).fetchone():
                db.execute("UPDATE jobs SET status='skipped',finished=?,detail=? WHERE id=?",(time.time(),'Già accettato da SMTP: invio duplicato evitato.',job['id']))
                return True
            db.execute("UPDATE jobs SET status='sending',started=?,detail=? WHERE id=?",(time.time(),'Preparazione del documento e connessione SMTP.',job['id']))
        workdir=c.data_dir()/'tmp'/uuid.uuid4().hex
        attempted=False
        try:
            if c.config_errors():
                raise ValueError('Configurazione incompleta. Completa le impostazioni prima di riprovare.')
            with contextlib.closing(c.connect()) as db:
                if c.setting(db,'mail_verified')!='1':raise ValueError('Completa e verifica la configurazione di invio del tuo account.')
            if not f or f['deleted'] or not f['available']:
                raise ValueError('File non più disponibile.')
            path=c.safe_path(f)
            workdir.mkdir()
            snapshot=workdir/c.clean_name(f['name'])
            shutil.copyfile(path,snapshot)
            c.validate(snapshot)
            if c.engine.sha256(snapshot)!=job['digest']:
                raise ValueError('Il file è cambiato dopo la selezione. Aggiorna l’elenco e selezionalo di nuovo.')
            cfg=c.load_mail_config()
            if snapshot.suffix.lower() in c.EXTRA_SUFFIXES:
                from app.reader import convert_epub
                converted=workdir/(snapshot.stem+'.epub')
                convert_epub(snapshot,converted)
                c.validate(converted)
                snapshot=converted
            with c.transaction() as db:
                db.execute('UPDATE jobs SET detail=? WHERE id=?',('Trasmissione al server SMTP in corso.',job['id']))
            attempted=True
            c.engine.send_file(snapshot,cfg)
            with c.transaction() as db:
                db.execute("INSERT OR REPLACE INTO sent VALUES (?,?,?)",(job['digest'],str(path),c.engine.datetime.now(c.engine.timezone.utc).isoformat()))
                db.execute("UPDATE jobs SET status='accepted',finished=?,detail=? WHERE id=?",(time.time(),'Accettato dal server SMTP. La consegna al Kindle richiede verifica sul dispositivo.',job['id']))
                c.event(db,'Un documento è stato accettato dal server SMTP.')
        except Exception as exc:
            definite=isinstance(exc,(ValueError,smtplib.SMTPAuthenticationError,smtplib.SMTPRecipientsRefused,smtplib.SMTPSenderRefused,smtplib.SMTPDataError,smtplib.SMTPConnectError,socket.gaierror,ConnectionRefusedError,ssl.SSLError))
            status='failed' if not attempted or definite else 'uncertain'
            detail=error_message(exc)
            with c.transaction() as db:
                db.execute('UPDATE jobs SET status=?,finished=?,detail=? WHERE id=?',(status,time.time(),detail,job['id']))
                c.event(db,'Invio non completato. '+detail)
        finally:
            shutil.rmtree(workdir,ignore_errors=True)
        return True


def recover_inflight():
    with c.transaction() as db:
        db.execute("UPDATE jobs SET status='uncertain',finished=?,detail=? WHERE status='sending'",(time.time(),'Worker riavviato durante un invio. Controlla il Kindle prima di riprovare.'))
        db.execute("UPDATE library_checks SET status='queued',started=NULL WHERE status='running'")
        c.event(db,'Worker avviato. Coda e cronologia ripristinate.')


def enqueue_waiting(db, source):
    rows=db.execute("""SELECT f.id FROM files f WHERE f.available=1 AND f.deleted=0
      AND f.digest NOT IN (SELECT sha256 FROM sent)
      AND NOT EXISTS (SELECT 1 FROM jobs j WHERE j.digest=f.digest AND j.status='uncertain')""").fetchall()
    return c.enqueue(db,[r['id'] for r in rows],source)


def process_library_check():
    # The web process only requests work. One worker serializes full rescans and sends.
    with c.transaction() as db:
        check=db.execute("SELECT * FROM library_checks WHERE status='queued' ORDER BY id LIMIT 1").fetchone()
        if not check:
            return False
        db.execute("UPDATE library_checks SET status='running',started=?,detail=? WHERE id=?",
                   (time.time(),'Controllo completo dei libri nella cartella in corso.',check['id']))
    try:
        result=scan(force=True)
        if not result['available']:
            raise ValueError('La cartella dei libri non è disponibile. Controlla il volume Docker.')
        recover_uploads()
        with c.transaction() as db:
            # A simultaneous "send now" can upgrade an already running scan.
            send_now=db.execute('SELECT send_now FROM library_checks WHERE id=?',(check['id'],)).fetchone()[0]
            queued=enqueue_waiting(db,'manual') if send_now else []
            uncertain=db.execute("""SELECT count(DISTINCT f.digest) FROM files f
              WHERE f.available=1 AND f.deleted=0 AND f.digest NOT IN (SELECT sha256 FROM sent)
              AND EXISTS (SELECT 1 FROM jobs j WHERE j.digest=f.digest AND j.status='uncertain')""").fetchone()[0]
            detail=('Controllo completato. '+str(len(queued))+' libri accodati o già in coda.' if send_now
                    else 'Controllo completato. Libreria aggiornata senza avviare nuovi invii.')
            if uncertain:
                detail+=' '+str(uncertain)+' esiti incerti richiedono un controllo in Attività.'
            if result['invalid']:
                detail+=' '+str(result['invalid'])+' file non leggibili o non validi esclusi.'
            db.execute("""UPDATE library_checks SET status='completed',finished=?,checked=?,new_files=?,
              queued=?,uncertain=?,invalid=?,detail=? WHERE id=?""",
              (time.time(),result['checked'],result['new_files'],len(queued),uncertain,result['invalid'],detail,check['id']))
            c.event(db,detail)
    except Exception:
        with c.transaction() as db:
            db.execute("UPDATE library_checks SET status='failed',finished=?,detail=? WHERE id=?",
              (time.time(),'Controllo non completato. Verifica che la cartella sia disponibile e riprova.',check['id']))
            c.event(db,'Controllo manuale della libreria non completato.')
    return True


def schedule_tick():
    with c.transaction() as db:
        if c.setting(db,'setup_complete')=='1' and c.setting(db,'mail_verified')=='1' and c.setting(db,'paused')=='0' and time.time()>=float(c.setting(db,'next_run')):
            enqueue_waiting(db,'automatic')
            c.put(db,'next_run',time.time()+int(c.setting(db,'interval_minutes'))*60)
            c.event(db,'Controllo automatico completato; nuovi file aggiunti alla coda.')


def all_accounts():
    profiles=accounts.profiles()
    return profiles if profiles else [None]


def heartbeats():
    while not STOP:
        # The root heartbeat is also used by Docker's worker healthcheck.
        with c.use_account(None),c.transaction() as db:c.put(db,'heartbeat',time.time())
        for account in accounts.profiles():
            with c.use_account(account):
                if (c.data_dir()/'state.sqlite3').exists():
                    with c.transaction() as db:c.put(db,'heartbeat',time.time())
        for _ in range(15):
            if STOP:return
            time.sleep(1)


def main():
    os.umask(0o077)
    c.init();accounts.init()
    signal.signal(signal.SIGTERM,stop);signal.signal(signal.SIGINT,stop)
    worker_lock=(c.DATA/'worker.lock').open('a')
    fcntl.flock(worker_lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    heartbeat=threading.Thread(target=heartbeats,daemon=True);heartbeat.start()
    initialized=set();last_scan={}
    try:
        while not STOP:
            worked=False
            for account in all_accounts():
                if STOP:break
                with c.use_account(account):
                    key=str(c.data_dir())
                    if key not in initialized:
                        c.init();recover_inflight()
                        for path in (c.data_dir()/'tmp').iterdir():
                            if path.is_dir():shutil.rmtree(path,ignore_errors=True)
                        initialized.add(key)
                    with c.transaction() as db:c.put(db,'heartbeat',time.time())
                    if process_library_check():last_scan[key]=time.time();worked=True
                    if time.time()-last_scan.get(key,0)>30:
                        scan();recover_uploads();last_scan[key]=time.time()
                    schedule_tick()
                    if run_one():worked=True
            if not worked:time.sleep(1)
    finally:
        heartbeat.join(timeout=2)
        for account in [None,*accounts.profiles()]:
            with c.use_account(account):
                with c.transaction() as db:
                    c.put(db,'heartbeat',0);c.event(db,'Worker fermato.')


if __name__=='__main__':
    main()
