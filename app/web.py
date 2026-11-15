import contextlib
from datetime import datetime
import hashlib
import hmac
import json
import os
from pathlib import Path
import secrets
import smtplib
import ssl
import time
import uuid
from flask import Flask, request, jsonify, render_template, make_response, send_file, g
from werkzeug.exceptions import HTTPException
from app import core as c
from app.worker import error_message
from app.covers import thumbnail
from app import reader
from app import i18n
from app import accounts

os.umask(0o077)
c.init()
accounts.init()
app=Flask(__name__)
ASSET_VERSION=hashlib.sha256(b''.join(path.read_bytes() for path in
    [Path(app.static_folder)/name for name in ['i18n.js','accounts.js','reader.js','app.js','style.css']]+
    sorted((Path(app.static_folder)/'locales').glob('*.json')))).hexdigest()[:12]
app.config.update(MAX_CONTENT_LENGTH=46*1024*1024,MAX_FORM_MEMORY_SIZE=16384,MAX_FORM_PARTS=12)


def fail(message, code=400):
    return jsonify(error=message),code


def token_hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


def session():
    return accounts.session(request.cookies.get('kindle_session',''))


@app.before_request
def protect_changes():
    s=session()
    g.account=accounts.user(s['user_id']) if s else None
    if g.account:
        g.account_context=c.use_account(g.account)
        g.account_context.__enter__()
    public=request.path in ['/api/session','/api/auth/login','/api/auth/register','/api/auth/logout']
    if request.path.startswith('/api/') and not public and not g.account:
        return fail('Seleziona un account per continuare.',401)
    expected=request.headers.get('X-Account-ID')
    if request.path.startswith('/api/') and not public and expected and expected!=g.account['id']:
        return fail('L’account è cambiato. Seleziona di nuovo il tuo account.',409)
    if request.method not in ['GET','HEAD','OPTIONS']:
        if s is None or not hmac.compare_digest(s['csrf'],request.headers.get('X-CSRF-Token','')):
            return fail('Sessione scaduta. Ricarica la pagina.',403)
    if request.method=='POST' and request.path!='/api/upload' and not request.is_json:
        return fail('Richiesta JSON necessaria.',415)


@app.teardown_request
def end_account(_error):
    context=getattr(g,'account_context',None)
    if context:context.__exit__(None,None,None)


@app.after_request
def headers(response):
    if not request.path.startswith('/static/'):
        response.headers['Content-Language']=i18n.language()
        if response.is_json:
            response.set_data(app.json.dumps(i18n.response_messages(response.get_json(),i18n.language())))
    response.headers['Cache-Control']='no-store' if not request.path.startswith('/static/') else 'no-cache'
    response.headers['X-Content-Type-Options']='nosniff'
    response.headers['Referrer-Policy']='no-referrer'
    response.headers['X-Frame-Options']='DENY'
    response.headers.setdefault('Content-Security-Policy',"default-src 'self'; script-src 'self' 'wasm-unsafe-eval'; worker-src 'self' blob:; style-src 'self'; img-src 'self' data: blob:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
    return response


@app.errorhandler(Exception)
def handle_error(exc):
    if isinstance(exc,ValueError):
        return fail(str(exc))
    if isinstance(exc,HTTPException):
        return fail('File troppo grande: limite 45 MB.' if exc.code==413 else 'Richiesta non valida.',exc.code)
    # No exception contents, body or config in logs.
    app.logger.error('Errore interno: %s',type(exc).__name__)
    return fail('Operazione non completata. Controlla la diagnostica e riprova.',500)


@app.get('/')
def index():
    return render_template('index.html',assets_version=ASSET_VERSION)


@app.get('/manifest.webmanifest')
def manifest():
    data=json.loads((Path(app.static_folder)/'manifest.webmanifest').read_text())
    data['name']='Kindle Sync'
    data['lang']='zh-CN' if i18n.language()=='zh' else i18n.language()
    return app.response_class(app.json.dumps(data),mimetype='application/manifest+json')


@app.get('/healthz')
def health():
    with contextlib.closing(c.connect()) as db:
        db.execute('SELECT 1').fetchone()
    return jsonify(status='ok')


@app.get('/api/session')
def get_session():
    s=session()
    token=None
    if s is None:
        token,csrf=accounts.new_session()
    else:csrf=s['csrf']
    res=make_response(jsonify(csrf=csrf,user=public_account(g.account),
        accounts=[public_account(a) for a in accounts.profiles()],owner_code_required=accounts.owner_code_required()))
    if token:set_session_cookie(res,token)
    return res


def public_account(account):
    return {'id':account['id'],'name':account['name'],'legacy':account['storage']=='legacy','language':account.get('language','it'),'password_required':bool(account.get('password_required',True))} if account else None


def set_session_cookie(response,token,remember=False):
    response.set_cookie('kindle_session',token,httponly=True,samesite='Strict',secure=os.environ.get('COOKIE_SECURE','false')=='true',max_age=accounts.REMEMBER_SECONDS if remember else None)


def signed_in(account):
    remember=request.get_json().get('remember') is True
    token,csrf=accounts.new_session(account['id'],request.cookies.get('kindle_session'),remember=remember)
    res=make_response(jsonify(user=public_account(account),csrf=csrf))
    set_session_cookie(res,token,remember=remember)
    return res


@app.post('/api/auth/register')
def register_account():
    account,error=accounts.register(request.get_json(),request.remote_addr or 'local')
    if error=='Codice dell’installazione non valido.':return jsonify(error=error,code='activation_invalid'),400
    return fail(error,400) if error else signed_in(account)


@app.post('/api/auth/login')
def login_account():
    account,error=accounts.login(request.get_json(),request.remote_addr or 'local')
    return fail(error,429 if error and error.startswith('Troppi') else 401) if error else signed_in(account)


@app.post('/api/auth/logout')
def logout_account():
    token,csrf=accounts.new_session(old_token=request.cookies.get('kindle_session'))
    res=make_response(jsonify(csrf=csrf,user=None))
    set_session_cookie(res,token)
    return res


@app.post('/api/account/password')
def change_account_password():
    accounts.change_password(g.account['id'],request.get_json(),request.remote_addr or 'local')
    token,csrf=accounts.new_session(old_token=request.cookies.get('kindle_session'))
    res=make_response(jsonify(csrf=csrf,user=None))
    set_session_cookie(res,token)
    return res


@app.get('/api/state')
def state():
    with contextlib.closing(c.connect()) as db:
        settings=dict(db.execute('SELECT key,value FROM settings').fetchall())
        rows=db.execute('''SELECT f.*,s.sent_at,
          (SELECT status FROM jobs WHERE digest=f.digest ORDER BY id DESC LIMIT 1) AS job_status,
          (SELECT detail FROM jobs WHERE digest=f.digest ORDER BY id DESC LIMIT 1) AS detail,
          (SELECT id FROM jobs WHERE digest=f.digest ORDER BY id DESC LIMIT 1) AS job_id
          FROM files f LEFT JOIN sent s ON s.sha256=f.digest WHERE f.deleted=0 ORDER BY f.created DESC,f.name COLLATE NOCASE''').fetchall()
        files=[{k:r[k] for k in ['id','name','size','origin','available','created','sent_at','job_status','detail','job_id','auto_send']} for r in rows]
        for f in files:
            f['status']='accepted' if f['sent_at'] else (f['job_status'] or 'pending')
        for f,r in zip(files,rows):
            f['cover_url']=cover_url(r)
            try:
                f['folder']=str(Path(r['path']).relative_to(c.library_dir()).parent) if r['origin']=='library' else 'Caricamenti'
            except ValueError:
                f['folder']='Libreria'
            f['format']=Path(r['name']).suffix.lstrip('.').upper()
            f['auto_send']=bool(r['auto_send'])
        recent=[]
        for r in db.execute("""SELECT s.*,f.id AS file_id,f.name,f.available,f.deleted,f.digest,
            EXISTS(SELECT 1 FROM jobs j WHERE j.digest=s.sha256 AND j.status='accepted') AS tracked
            FROM sent s LEFT JOIN files f ON f.id=(SELECT f2.id FROM files f2 WHERE f2.digest=s.sha256
              ORDER BY f2.deleted ASC,f2.available DESC,f2.created DESC,f2.id LIMIT 1)
            ORDER BY julianday(s.sent_at) DESC,s.sha256 LIMIT 10"""):
            recent.append({'name':r['name'] or Path(r['path']).name,'sent_at':r['sent_at'],
                'status':'accepted' if r['tracked'] else 'legacy','file_id':r['file_id'],
                'cover_url':cover_url(r,id_key='file_id') if r['file_id'] and not r['deleted'] else None})
        library_check=db.execute('SELECT * FROM library_checks ORDER BY id DESC LIMIT 1').fetchone()
        jobs=[dict(r) for r in db.execute('SELECT id,file_id,name,status,source,created,started,finished,detail FROM jobs ORDER BY id DESC LIMIT 300')]
        # Keep legacy timestamps explicitly separate from verified delivery.
        legacy=[{'name':Path(r['path']).name,'sent_at':r['sent_at'],'status':'legacy'} for r in db.execute("SELECT * FROM sent WHERE sha256 NOT IN (SELECT digest FROM jobs WHERE status='accepted') ORDER BY sent_at DESC LIMIT 300")]
        last=db.execute('SELECT max(sent_at) FROM sent').fetchone()[0]
        logs=[dict(r) for r in db.execute('SELECT created,message FROM events ORDER BY id DESC LIMIT 60')]
    cfg=c.config()
    return jsonify(account=public_account(g.account),setup_required=settings.get('setup_complete')!='1',mail_ready=settings.get('mail_verified')=='1' and not c.config_errors(),files=files,jobs=jobs,legacy=legacy,last_sent=last,logs=logs,recent_sent=recent,
       library_check=dict(library_check) if library_check else None,
       schedule={'paused':settings['paused']=='1','interval_minutes':int(settings['interval_minutes']),'next_run':float(settings['next_run'])},
       diagnostics={'worker_ok':time.time()-float(settings['heartbeat'])<180,'last_scan':float(settings['scan']),
          'library_ok':c.library_dir().is_dir(),'database_ok':True,'config_ok':not c.config_errors(),
          'missing':c.config_errors(),'configured':{k:bool(cfg.get(k)) for k in ['kindle_address','smtp_user','smtp_password','smtp_host','smtp_port']},
          'smtp_ssl':cfg.get('smtp_ssl','true')=='true'})


def cover_url(row,id_key='id'):
    if row['available'] and Path(row['name']).suffix.lower()=='.epub':
        return '/api/files/'+row[id_key]+'/cover?v='+row['digest'][:16]
    return None


@app.get('/api/files/<file_id>/cover')
def file_cover(file_id):
    with contextlib.closing(c.connect()) as db:
        row=db.execute('SELECT * FROM files WHERE id=? AND deleted=0 AND available=1',(file_id,)).fetchone()
    if not row:return fail('Libro non disponibile.',404)
    try:
        path=thumbnail(row)
    except (ValueError,OSError):
        path=None
    return send_file(path or Path(app.static_folder)/'cover-placeholder.svg',conditional=True)


@app.post('/api/library/check')
def request_library_check():
    body=request.get_json()
    if not isinstance(body,dict) or not isinstance(body.get('send_now'),bool):
        raise ValueError('Indica se controllare soltanto o avviare anche l’invio.')
    if body['send_now']:require_mail()
    with c.transaction() as db:
        active=db.execute("SELECT * FROM library_checks WHERE status IN ('queued','running') LIMIT 1").fetchone()
        if active:
            if body['send_now']:
                db.execute('UPDATE library_checks SET send_now=1 WHERE id=?',(active['id'],))
            check_id=active['id']
        else:
            check_id=db.execute("INSERT INTO library_checks(send_now,status,created,detail) VALUES (?,'queued',?,?)",
                (int(body['send_now']),time.time(),'Controllo richiesto. In attesa del worker.')).lastrowid
        check=dict(db.execute('SELECT * FROM library_checks WHERE id=?',(check_id,)).fetchone())
    return jsonify(check=check),202


def selected_ids(body):
    ids=body.get('ids',[])
    if not isinstance(ids,list) or not 1<=len(ids)<=100 or any(not isinstance(i,str) or len(i)!=32 for i in ids):
        raise ValueError('Seleziona da 1 a 100 file.')
    return ids


@app.post('/api/send')
def send():
    require_mail()
    ids=selected_ids(request.get_json())
    with c.transaction() as db:
        results=c.enqueue(db,ids,'manual')
    return jsonify(results=results),202


@app.post('/api/files/<file_id>/automatic')
def file_automatic(file_id):
    body=request.get_json()
    if not isinstance(body,dict) or not isinstance(body.get('enabled'),bool):
        raise ValueError('Indica se abilitare l’invio automatico del libro.')
    with c.transaction() as db:
        row=db.execute('SELECT * FROM files WHERE id=? AND deleted=0',(file_id,)).fetchone()
        if not row:return fail('Libro non disponibile.',404)
        db.execute('UPDATE files SET auto_send=? WHERE id=?',(int(body['enabled']),file_id))
        if not body['enabled']:
            # Cancel only automatic jobs that have not started; manual requests still run.
            db.execute("UPDATE jobs SET status='cancelled',finished=?,detail=? WHERE file_id=? AND status='queued' AND source='automatic'",
                (time.time(),'Invio automatico disattivato per questo libro.',file_id))
    return jsonify(ok=True)


def reader_file(file_id):
    with contextlib.closing(c.connect()) as db:
        row=db.execute('SELECT * FROM files WHERE id=? AND deleted=0 AND available=1',(file_id,)).fetchone()
    return row


@app.get('/api/files/<file_id>/reader')
def reader_manifest(file_id):
    row=reader_file(file_id)
    if row is None:return fail('Libro non disponibile.',404)
    return jsonify(reader.manifest(row))


@app.get('/api/files/<file_id>/reader/chapters/<int:index>')
def reader_chapter(file_id,index):
    row=reader_file(file_id)
    if row is None:return fail('Libro non disponibile.',404)
    return jsonify(html=reader.chapter(row,index))


@app.get('/api/files/<file_id>/reader/images/<int:index>')
def reader_image(file_id,index):
    row=reader_file(file_id)
    if row is None:return fail('Libro non disponibile.',404)
    return send_file(reader.image(row,index),mimetype='image/jpeg')


@app.get('/api/files/<file_id>/reader/pdf')
def reader_pdf(file_id):
    row=reader_file(file_id)
    if row is None:return fail('Libro non disponibile.',404)
    if Path(row['name']).suffix.lower()!='.pdf':return fail('Il documento non è un PDF.',400)
    response=send_file(reader.snapshot(row),mimetype='application/pdf',conditional=True,download_name=row['name'])
    response.headers['Content-Security-Policy']="sandbox; default-src 'none'"
    return response


@app.post('/api/upload')
def upload():
    if request.form.get('send_now')=='true':require_mail()
    incoming=request.files.get('file')
    if incoming is None:
        raise ValueError('Scegli un file da caricare.')
    name=c.clean_name(incoming.filename)
    folder=c.data_dir()/'uploads'/uuid.uuid4().hex
    folder.mkdir()
    dest=folder/name
    partial=folder/'.uploading'
    try:
        incoming.save(partial)
        c.validate(partial,name)
        with partial.open('rb') as h:
            os.fsync(h.fileno())
        partial.replace(dest)
        with c.transaction() as db:
            digest=c.engine.sha256(dest)
            old=db.execute('SELECT id FROM files WHERE digest=? AND available=1 AND deleted=0',(digest,)).fetchone()
            fid=old['id'] if old else c.register(db,dest,name,'upload')
            results=c.enqueue(db,[fid],'manual') if request.form.get('send_now')=='true' else []
        if old:
            dest.unlink();folder.rmdir()
        return jsonify(file_id=fid,results=results),201
    except BaseException:
        if dest.exists():dest.unlink()
        if partial.exists():partial.unlink()
        folder.rmdir()
        raise


@app.post('/api/delete')
def delete():
    ids=selected_ids(request.get_json())
    with c.transaction() as db:
        rows=[]
        for fid in ids:
            f=db.execute('SELECT * FROM files WHERE id=? AND deleted=0',(fid,)).fetchone()
            if not f:raise ValueError('File non disponibile.')
            if db.execute("SELECT 1 FROM jobs WHERE digest=? AND status IN ('queued','sending')",(f['digest'],)).fetchone():
                raise ValueError('Attendi la fine degli invii prima di eliminare il file.')
            rows.append(f)
        # Original library files remain untouched; this is clearly shown in confirmation.
        for f in rows:
            if f['origin']=='upload' and f['available']:
                c.safe_path(f).unlink()
            db.execute('UPDATE files SET deleted=1 WHERE id=?',(f['id'],))
        c.event(db,'File rimossi dal catalogo. Gli originali della libreria sono conservati.')
    return jsonify(ok=True)


@app.post('/api/retry/<int:job_id>')
def retry(job_id):
    require_mail()
    with c.transaction() as db:
        job=db.execute('SELECT * FROM jobs WHERE id=?',(job_id,)).fetchone()
        if not job or job['status'] not in ['failed','uncertain']:
            raise ValueError('Questo invio non può essere riprovato.')
        latest=db.execute('SELECT id FROM jobs WHERE digest=? ORDER BY id DESC LIMIT 1',(job['digest'],)).fetchone()
        if latest['id']!=job_id:
            raise ValueError('È già presente un tentativo più recente.')
        if job['status']=='uncertain':
            if request.get_json().get('confirmed') is not True:
                raise ValueError('Controlla il Kindle: un nuovo tentativo potrebbe creare un duplicato.')
            db.execute("UPDATE jobs SET status='reviewed',detail=detail || ' Nuovo tentativo autorizzato dopo controllo.' WHERE id=?",(job_id,))
        results=c.enqueue(db,[job['file_id']],'retry')
    return jsonify(results=results),202


@app.post('/api/schedule')
def schedule():
    body=request.get_json();interval=body.get('interval_minutes');paused=body.get('paused')
    if not isinstance(interval,int) or isinstance(interval,bool) or not 5<=interval<=10080 or not isinstance(paused,bool):
        raise ValueError('Scegli una frequenza da 5 a 10080 minuti.')
    if not paused:require_mail()
    with c.transaction() as db:
        c.put(db,'interval_minutes',interval);c.put(db,'paused',int(paused));c.put(db,'next_run',time.time()+interval*60)
        c.event(db,'Programmazione aggiornata. '+('Invii automatici in pausa.' if paused else 'Invii automatici attivi.'))
    return jsonify(ok=True)


@app.post('/api/config')
def update_config():
    c.save_config(request.get_json())
    with c.transaction() as db:c.event(db,'Configurazione Kindle e SMTP aggiornata.')
    return jsonify(ok=True)


@app.post('/api/config/check')
def check_config():
    if c.config_errors():return fail('Configurazione incompleta. Compila i campi necessari.')
    try:
        verify_smtp()
    except Exception as exc:
        return fail(error_message(exc),422)
    with c.transaction() as db:
        c.put(db,'mail_verified',1);c.put(db,'setup_complete',1)
        c.event(db,'Connessione TLS e autenticazione SMTP verificate. Nessun documento inviato.')
    return jsonify(message='Connessione TLS e autenticazione riuscite. Nessun documento inviato; verifica sul Kindle la consegna dei documenti.')


def require_mail():
    with contextlib.closing(c.connect()) as db:verified=c.setting(db,'mail_verified')=='1'
    if c.config_errors() or not verified:raise ValueError('Completa e verifica la configurazione di invio del tuo account.')


def verify_smtp():
    cfg=c.load_mail_config()
    if cfg.getboolean('smtp_ssl',fallback=True):
        server=smtplib.SMTP_SSL(cfg['smtp_host'],cfg.getint('smtp_port'),timeout=15,context=ssl.create_default_context())
    else:
        server=smtplib.SMTP(cfg['smtp_host'],cfg.getint('smtp_port'),timeout=15)
        server.ehlo();server.starttls(context=ssl.create_default_context());server.ehlo()
    with server:
        server.login(cfg['smtp_user'],cfg['smtp_password']);server.noop()


@app.get('/api/mail')
def mail_settings():
    cfg=c.config()
    return jsonify(settings={key:cfg.get(key,'') for key in ['kindle_address','smtp_user','smtp_host','smtp_port','smtp_ssl']},
        password_set=bool(cfg.get('smtp_password')))


@app.post('/api/setup/mail')
def setup_mail():
    body=request.get_json()
    if not isinstance(body,dict) or not isinstance(body.get('settings'),dict) or not isinstance(body.get('automatic'),bool):
        raise ValueError('Configurazione non valida.')
    if body.get('sender_approved') is not True:
        raise ValueError('Autorizza l’e-mail del mittente nelle impostazioni documenti personali di Amazon.')
    c.save_config(body['settings'])
    try:verify_smtp()
    except Exception as exc:return fail(error_message(exc),422)
    with c.transaction() as db:
        c.put(db,'mail_verified',1);c.put(db,'setup_complete',1)
        c.put(db,'paused',int(not body['automatic']));c.put(db,'next_run',time.time()+int(c.setting(db,'interval_minutes'))*60)
        c.event(db,'Configurazione Kindle e SMTP aggiornata.')
    return jsonify(ok=True,message='Configurazione completata. Nessun libro è stato inviato.')


@app.post('/api/setup/skip')
def setup_skip():
    with c.transaction() as db:
        c.put(db,'setup_complete',1);c.put(db,'paused',1)
    return jsonify(ok=True)


@app.post('/api/account/language')
def account_language():
    body=request.get_json()
    if not isinstance(body,dict) or body.get('language') not in i18n.LANGUAGES:raise ValueError('Richiesta non valida.')
    with accounts.transaction() as db:db.execute('UPDATE users SET language=? WHERE id=?',(body['language'],g.account['id']))
    return jsonify(ok=True)
