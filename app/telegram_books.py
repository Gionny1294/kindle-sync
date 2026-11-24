"""Account-scoped Telegram book bot connector. Credentials never enter API responses."""
import asyncio
import contextlib
import fcntl
import json
import math
import os
from pathlib import Path
import re
import secrets
import tempfile
import time
import unicodedata
from xml.etree import ElementTree as ET
import zipfile
from app import core as c
from app.recommendations import read_json, snapshot

FORMATS=('epub','pdf','mobi','azw3','fb2','txt','docx','rtf')
COMMAND=re.compile(r'/(?:book|get|dl)(?:_[A-Za-z0-9][A-Za-z0-9_]{0,127}|\d+(?:_[A-Za-z0-9]+)?)(?!\w)')


LANGUAGE_ALIASES={
    'it':('Italian','Italiano','Italiana','ita','it'),
    'en':('English','Inglese','eng','en'),
    'fr':('French','Français','Francais','Francese','fra','fre','fr'),
    'es':('Spanish','Español','Espanol','Spagnolo','spa','es'),
    'zh':('Simplified Chinese','Traditional Chinese','Chinese','Mandarin','Cinese','简体中文','繁體中文','中文','汉语','漢語','zho','chi','cmn','zh'),
    'ru':('Russian','Russo','Русский','rus','ru'),
}
LANGUAGE_FLAGS={'it':('🇮🇹',),'en':('🇬🇧','🇺🇸'),'fr':('🇫🇷',),'es':('🇪🇸',),'zh':('🇨🇳','🇹🇼'),'ru':('🇷🇺',)}
LANGUAGE_PREFIX=r'(?:language|lingua|langue|idioma|язык|语言|語言)\s*[:：=-]\s*'


def download_preferences():
    from flask import has_request_context
    from app import i18n
    with contextlib.closing(c.connect()) as db:preference=read_json(db,'download_language','app')
    if preference not in ('app',*LANGUAGE_ALIASES):preference='app'
    app_language=i18n.language() if has_request_context() else 'it'
    return {'language_preference':preference,'language':app_language if preference=='app' else preference}


def save_download_preferences(body):
    if not isinstance(body,dict) or body.get('language') not in ('app',*LANGUAGE_ALIASES):
        raise ValueError('Lingua dei download non valida.')
    with c.transaction() as db:c.put(db,'download_language',json.dumps(body['language']))
    return status()


def language_code(value):
    value=unicodedata.normalize('NFKC',str(value or '')).strip().casefold()
    for code,aliases in LANGUAGE_ALIASES.items():
        if value in {alias.casefold() for alias in aliases} or value.split('-')[0].split('_')[0] in {alias.casefold() for alias in aliases}:return code
    return None


def matches_language(label,language):
    if language not in LANGUAGE_ALIASES:return False
    label=unicodedata.normalize('NFKC',label)
    declarations=list(re.finditer(LANGUAGE_PREFIX+r'([^\n|,;]+)',label,re.I))
    if declarations:
        explicit=set()
        for match in declarations:
            identified=None
            for code,aliases in LANGUAGE_ALIASES.items():
                choices='|'.join(re.escape(alias) for alias in sorted(aliases,key=len,reverse=True))
                if re.match(r'(?:'+choices+r')(?:[-_][a-z]{2,4})?(?!\w)',match[1].strip(),re.I):identified=code;break
            explicit.add(identified)
        return explicit=={language}
    recognized=set()
    for code,aliases in LANGUAGE_ALIASES.items():
        names=[alias for alias in aliases if len(alias)>2 or not alias.isascii()]
        choices='|'.join(re.escape(alias) for alias in sorted(names,key=len,reverse=True))
        metadata=r'(?:^|[\n,;|(\[])\s*(?:[🌐🌍🌎]\ufe0f?\s*)?(?:'+choices+r')\s*(?=[,;|)\]\n]|$)'
        if re.search(metadata,label,re.I) or any(flag in label for flag in LANGUAGE_FLAGS[code]):recognized.add(code)
    return recognized=={language}



def config():
    with contextlib.closing(c.connect()) as db:return read_json(db,'telegram_config',{})


def write_config(value):
    with c.transaction() as db:c.put(db,'telegram_config',json.dumps(value))


def status():
    cfg=config()
    return dict(bot=cfg.get('bot') or os.environ.get('TELEGRAM_BOOK_BOT',''),configured=bool(cfg),
        connected=bool(cfg.get('authorized')),pending=bool(cfg.get('code_hash')),password_required=bool(cfg.get('password_required')),
        destination='Libri/Singoli',formats=list(FORMATS),**download_preferences())


@contextlib.contextmanager
def lock():
    with (c.data_dir()/'telegram.lock').open('a') as handle:
        try:fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:raise ValueError('Telegram è occupato. Attendi la fine dell’operazione.')
        try:yield
        finally:fcntl.flock(handle,fcntl.LOCK_UN)


def perform(operation,timeout=35):
    # A fresh connection uses the same saved StringSession, under a process-safe lock.
    from telethon import TelegramClient, errors
    from telethon.sessions import StringSession
    with lock():
        cfg=config()
        if not cfg.get('api_id'):raise ValueError('Collega Telegram per scaricare i libri.')
        async def run():
            client=TelegramClient(StringSession(cfg.get('session','')),cfg['api_id'],cfg['api_hash'],
                timeout=8,request_retries=1,connection_retries=1,retry_delay=1,flood_sleep_threshold=0)
            try:
                await client.connect()
                try:return await operation(client,cfg)
                finally:
                    cfg['session']=client.session.save()
                    write_config(cfg)
            finally:await client.disconnect()
        try:return asyncio.run(asyncio.wait_for(run(),timeout=timeout))
        except errors.SessionPasswordNeededError:
            return {**status(),'password_required':True}
        except errors.FloodWaitError as exc:raise ValueError('Telegram richiede una pausa. Riprova tra '+str(exc.seconds)+' secondi.') from None
        except errors.PhoneCodeInvalidError:raise ValueError('Codice Telegram non valido.') from None
        except errors.PhoneCodeExpiredError:raise ValueError('Codice Telegram scaduto. Collega di nuovo Telegram.') from None
        except errors.PasswordHashInvalidError:raise ValueError('Password Telegram non corretta.') from None
        except errors.ApiIdInvalidError:raise ValueError('API ID o API hash Telegram non validi.') from None
        except ValueError:raise
        except Exception:raise ValueError('Operazione Telegram non completata. Controlla il collegamento e riprova.') from None


def connect(body):
    if not isinstance(body,dict):raise ValueError('Richiesta non valida.')
    bot=str(body.get('bot','')).lstrip('@');api_hash=body.get('api_hash','');phone=body.get('phone','')
    try:api_id=int(body.get('api_id',''))
    except (ValueError,TypeError):raise ValueError('API ID Telegram non valido.') from None
    if not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]{4,63}[Bb][Oo][Tt]',bot) or not isinstance(api_hash,str) or not re.fullmatch('[a-fA-F0-9]{32}',api_hash) or not isinstance(phone,str) or not re.fullmatch(r'\+[0-9]{7,16}',phone.strip()) or not 0<api_id<2**31:
        raise ValueError('Completa bot, API ID, API hash e numero Telegram con prefisso internazionale.')
    with lock():
        old=config()
        if old.get('code_requested',0)>time.time()-60:raise ValueError('Attendi un minuto prima di richiedere un nuovo codice Telegram.')
        if old.get('authorized'):raise ValueError('Scollega Telegram prima di cambiare account.')
        write_config(dict(bot=bot,api_id=api_id,api_hash=api_hash,phone=phone.strip(),session=''))
    async def send(client,cfg):
        sent=await client.send_code_request(cfg['phone'])
        cfg['code_hash']=sent.phone_code_hash;cfg['code_requested']=time.time()
    perform(send)
    return status()


def verify(body):
    if not isinstance(body,dict):raise ValueError('Richiesta non valida.')
    code=body.get('code','');password=body.get('password','')
    if not isinstance(code,str) or len(code)>12 or not isinstance(password,str) or len(password)>256:raise ValueError('Codice o password Telegram non validi.')
    async def signin(client,cfg):
        if not cfg.get('code_hash') or time.time()-cfg.get('code_requested',0)>600:raise ValueError('Codice Telegram scaduto. Collega di nuovo Telegram.')
        from telethon import errors
        if cfg.get('password_required'):
            if not password:raise ValueError('Inserisci la password di verifica in due passaggi di Telegram.')
            await client.sign_in(password=password)
        else:
            if not code.strip():raise ValueError('Inserisci il codice ricevuto su Telegram.')
            try:await client.sign_in(phone=cfg['phone'],code=code.replace(' ',''),phone_code_hash=cfg['code_hash'])
            except errors.SessionPasswordNeededError:
                cfg['password_required']=True
                if not password:raise
                await client.sign_in(password=password)
        cfg['authorized']=True
        for key in ['code_hash','code_requested','phone','password_required']:cfg.pop(key,None)
    result=perform(signin)
    return result or status()


def disconnect():
    async def logout(client,cfg):
        if await client.is_user_authorized():await client.log_out()
    if config():perform(logout)
    with lock(),c.transaction() as db:
        db.execute("DELETE FROM settings WHERE key IN ('telegram_config','telegram_search')")
    return status()


def edition_metadata(label,language='it'):
    if not matches_language(label,language):return None
    fmt=re.search(r'\b('+'|'.join(FORMATS)+r')\b',label,re.I)
    if not fmt:return None
    size=re.search(r'(\d+(?:[.,]\d+)?)\s*(KB|MB|GB|KiB|MiB|GiB)\b',label,re.I)
    amount=float(size[1].replace(',','.')) if size else float('inf')
    scale={'kb':1024,'kib':1024,'mb':1024**2,'mib':1024**2,'gb':1024**3,'gib':1024**3}
    size_bytes=amount*scale[size[2].lower()] if size else float('inf')
    if size and (not math.isfinite(size_bytes) or not 0<size_bytes<=c.MAX_BYTES):return None
    return dict(format=fmt[1].lower(),size=int(size_bytes) if size else None,language=language)


def edition_names(label):
    """Read the bot's book heading without treating notices or hints as titles."""
    lines=[line.strip() for line in label.splitlines() if line.strip()]
    for index,line in enumerate(lines):
        heading=re.match(r'^(?:[📚📖📕📗📘📙📓📔📒]\ufe0f?\s*|(?:title|titolo)\s*:\s*)(.+)$',line,re.I)
        if not heading:continue
        author=lines[index+1] if index+1<len(lines) else ''
        if re.match(r'^(?:year|anno|language|lingua)\s*:|^[🌐🌍🌎/]|\b(?:'+ '|'.join(FORMATS)+r')\b',author,re.I):author=''
        return {'title':heading[1].strip(),'author':re.sub(r'^(?:author|autore|by)\s*:\s*','',author,flags=re.I)}
    return {}


def edition_key(option):
    return (0 if option['format']=='epub' else 1,option['size'] if option['size'] is not None else float('inf'))


def parse_editions(messages,language='it'):
    """Select only editions in the requested language; prefer EPUB then size."""
    results=[];seen=set()
    for message in messages:
        body=message.raw_text or ''
        matches=list(COMMAND.finditer(body));last=0
        for index,match in enumerate(matches):
            # The command and trailing '(epub, 539 KB)' belong to the same edition.
            end=body.find('\n',match.end())
            if end<0:end=len(body)
            if index+1<len(matches):end=min(end,matches[index+1].start())
            block=body[last:end];last=end
            meta=edition_metadata(block,language)
            if not meta and not body[:match.start()].strip() and len(matches)==1:
                block=body;meta=edition_metadata(block,language)
            if meta and match[0] not in seen:
                seen.add(match[0]);results.append(dict(label=block.strip()[:900],command=match[0],**edition_names(block),**meta))
        # Some bots use a single edition per message with a callback Download button.
        if not matches:
            meta=edition_metadata(body,language)
            if not meta:continue
            if message.document:
                results.append(dict(label=body[:900],document_id=message.id,**meta));continue
            for row_index,row in enumerate(message.buttons or []):
                for button_index,button in enumerate(row):
                    if getattr(button,'data',None) and re.search(r'download|scarica|📥|⬇',button.text or '',re.I):
                        results.append(dict(label=body[:900],message_id=message.id,row=row_index,column=button_index,**meta))
    return sorted((item for item in results if item['size'] is None or item['size']<=c.MAX_BYTES),key=edition_key)[:30]


async def bot_entity(client,cfg):
    if not cfg.get('authorized') or not await client.is_user_authorized():
        cfg['authorized']=False
        raise ValueError('Collega Telegram per scaricare i libri.')
    entity=await client.get_entity(cfg['bot'])
    if not getattr(entity,'bot',False):raise ValueError('Il destinatario configurato non è un bot Telegram.')
    return entity


async def replies(client,entity,after,seconds=14,document=False,language='it'):
    deadline=time.monotonic()+seconds;found={};last_change=time.monotonic()
    while time.monotonic()<deadline:
        for message in await client.get_messages(entity,limit=40,min_id=after):
            if message.out or message.id<=after:continue
            signature=(message.raw_text or '',bool(message.document))
            if message.id not in found or found[message.id][0]!=signature:last_change=time.monotonic()
            found[message.id]=(signature,message)
            if document and message.document:return [message]
        # Collect split results, and wait past initial "searching" notices.
        messages=[entry[1] for entry in found.values()]
        if not document and messages and time.monotonic()-last_change>2 and parse_editions(messages,language):return messages
        await asyncio.sleep(1)
    return [entry[1] for entry in found.values()]


def search_query(value):
    if not isinstance(value,str) or len(value)>1000:
        raise ValueError('Inserisci titolo, autore o ISBN (da 2 a 250 caratteri).')
    value=' '.join(unicodedata.normalize('NFKC',value).split())
    if not 2<=len(value)<=250 or value.startswith('/') or any(unicodedata.category(ch).startswith('C') for ch in value):
        raise ValueError('Inserisci titolo, autore o ISBN (da 2 a 250 caratteri).')
    return value


def search(book_url=None,query=None):
    book=None
    if query is None:
        book=next((b for b in snapshot()['books'] if b['url']==book_url),None)
        if not book:raise ValueError('Libro non disponibile nei Consigliati. Aggiorna la pagina.')
        query=' '.join((book['title']+' '+book['author']).split())[:250].lstrip('/')
    query=search_query(query)
    language=download_preferences()['language']
    with c.transaction() as db:
        if read_json(db,'telegram_last_search',0)>time.time()-20:raise ValueError('Attendi qualche secondo prima di una nuova ricerca.')
        c.put(db,'telegram_last_search',time.time())
    async def find(client,cfg):
        entity=await bot_entity(client,cfg)
        # Both recommendation and free searches send one validated query to the configured bot.
        sent=await client.send_message(entity,query)
        editions=parse_editions(await replies(client,entity,sent.id,language=language),language)
        tokens={secrets.token_hex(16):dict(option,**({'requested_title':book['title'],'requested_author':book['author']} if book else {}),expires=time.time()+600) for option in editions}
        with c.transaction() as db:c.put(db,'telegram_search',json.dumps(tokens))
        return {'query':query,'language':language,'options':[{'token':token,**{k:(COMMAND.sub('',value).strip() if k=='label' else value) for k,value in option.items() if k in ['label','format','size','language']}} for token,option in tokens.items()]}
    return perform(find,timeout=30)


def verify_epub_language(path,language='it'):
    with zipfile.ZipFile(path) as archive:
        for name in archive.namelist():
            if not name.lower().endswith('.opf'):continue
            from app.covers import bounded_read
            data=bounded_read(archive,name,2*1024*1024)
            if b'<!ENTITY' in data.upper() or b'<!DOCTYPE' in data.upper():raise ValueError('Metadati EPUB non validi.')
            try:root=ET.fromstring(data)
            except ET.ParseError:continue
            languages=[(node.text or '').strip().lower() for node in root.iter() if node.tag.rsplit('}',1)[-1]=='language']
            if languages and not any(language_code(lang)==language for lang in languages):
                raise ValueError('La lingua del file non corrisponde a quella scelta. Download annullato.')


def download_name(temp,original,option):
    """Use selected title/author or EPUB metadata; retain the verified file extension."""
    title=option.get('requested_title');author=option.get('requested_author','')
    if not title and Path(original).suffix.lower()=='.epub':
        from app.covers import xml,member_path
        with zipfile.ZipFile(temp) as archive:
            try:
                container=xml(archive,'META-INF/container.xml')
                package=next((node.get('full-path') for node in container.iter() if node.tag.rsplit('}',1)[-1]=='rootfile'),None)
                packages=[member_path('',package)] if package else [name for name in archive.namelist() if name.lower().endswith('.opf')]
                for package in packages[:1]:
                    root=xml(archive,package)
                    title=next((' '.join(node.itertext()).strip() for node in root.iter() if node.tag.rsplit('}',1)[-1]=='title'),'')
                    author=', '.join(' '.join(node.itertext()).strip() for node in root.iter() if node.tag.rsplit('}',1)[-1]=='creator')
            except (ValueError,KeyError,ET.ParseError):pass
    if not title:title=option.get('title');author=option.get('author','')
    if not title:return original
    def clean(value):
        value=unicodedata.normalize('NFC',str(value or ''))
        return ' '.join(''.join(' ' if unicodedata.category(ch).startswith('C') or ch in '/\\:*?"<>|' else ch for ch in value).split()).strip(' .')
    title=clean(title);author=clean(author)
    if not title:return original
    stem=title+(' - '+author if author else '')
    # Reserve bytes for the extension and collision suffix; never split Unicode.
    while len(stem.encode('utf-8'))>180:stem=stem[:-1]
    return c.clean_name(stem.rstrip(' .')+Path(original).suffix.lower())


def install_file(temp,name,language='it'):
    name=c.clean_name(name)
    if Path(name).suffix.lower().lstrip('.') not in FORMATS:raise ValueError('Il bot non ha inviato un formato libro supportato.')
    c.validate(temp,name)
    if Path(name).suffix.lower()=='.epub':verify_epub_language(temp,language)
    target=c.download_dir()
    if not target.is_dir():raise ValueError('Cartella Libri/Singoli non disponibile. Controlla il volume del servizio.')
    digest=c.engine.sha256(temp)
    # No overwrite and no symlink following. Same-named different editions get a digest suffix.
    for filename in [name,Path(name).stem[:100]+'-'+digest[:12]+Path(name).suffix.lower()]:
        destination=target/filename
        if destination.exists() and not destination.is_symlink() and destination.is_file() and c.engine.sha256(destination)==digest:return destination
        stage=None
        try:
            # Write and validate in a hidden temp file, then atomically publish without replacement.
            with tempfile.NamedTemporaryFile(dir=target,prefix='.kindle-',suffix='.part',delete=False) as output:
                stage=Path(output.name)
                with temp.open('rb') as source:
                    import shutil
                    shutil.copyfileobj(source,output)
                output.flush();os.fsync(output.fileno())
            try:os.link(stage,destination)
            finally:stage.unlink(missing_ok=True)
            return destination
        except FileExistsError:continue
        finally:
            if stage is not None:stage.unlink(missing_ok=True)
    raise ValueError('Esiste già un file con questo nome. Controlla la cartella Singoli.')


def register_download(saved):
    # Bind mounts expose the same ZFS file under different paths. Reuse the
    # library path when available so later scans cannot create a second entry.
    path=saved;origin='download'
    for candidate in [c.library_dir()/saved.name,c.library_dir()/'Singoli'/saved.name]:
        if candidate.is_file() and not candidate.is_symlink() and candidate.samefile(saved):
            path=candidate;origin='library';break
    with c.transaction() as db:
        fid=c.register(db,path,saved.name,origin)
        db.execute('UPDATE files SET name=?,deleted=0,available=1 WHERE id=?',(saved.name,fid))
    return fid


def download(token):
    language=download_preferences()['language']
    with c.transaction() as db:
        stored=read_json(db,'telegram_search',{});option=stored.get(token)
        if not option or option['expires']<time.time():raise ValueError('Ricerca scaduta. Premi di nuovo Scarica.')
        if option.get('language','it')!=language:raise ValueError('La lingua dei download è cambiata. Ripeti la ricerca.')
        stored.pop(token)
        c.put(db,'telegram_search',json.dumps(stored))
    async def receive(client,cfg):
        entity=await bot_entity(client,cfg)
        if option.get('document_id'):
            message=await client.get_messages(entity,ids=option['document_id'])
        else:
            previous=await client.get_messages(entity,limit=1);after=previous[0].id if previous else 0
            if option.get('command'):await client.send_message(entity,option['command'])
            else:
                original=await client.get_messages(entity,ids=option['message_id'])
                await original.click(option['row'],option['column'])
            messages=await replies(client,entity,after,seconds=45,document=True)
            message=next((m for m in messages if m.document),None)
        if not message or not message.document:raise ValueError('Il bot non ha inviato il file. Controlla il bot su Telegram e riprova.')
        file=message.file
        if not file or not file.name or not file.size or file.size>c.MAX_BYTES:raise ValueError('File non disponibile o troppo grande: limite 45 MB.')
        filename=file.name
        if Path(filename).suffix.lower().lstrip('.')!=option['format']:raise ValueError('Il formato ricevuto non corrisponde all’edizione scelta.')
        temp=c.data_dir()/'tmp'/('telegram-'+secrets.token_hex(12)+'.part')
        def progress(current,total):
            if current>c.MAX_BYTES or total>c.MAX_BYTES:raise ValueError('File troppo grande: limite 45 MB.')
        try:
            await client.download_media(message,file=str(temp),progress_callback=progress)
            c.validate(temp,'book.'+option['format'])
            saved=install_file(temp,download_name(temp,filename,option),language)
            register_download(saved)
            return dict(name=saved.name,destination='Libri/Singoli',format=option['format'])
        finally:temp.unlink(missing_ok=True)
    return perform(receive,timeout=110)
