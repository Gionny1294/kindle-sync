"""Read local books from bounded snapshots; never serve book HTML as a document."""
import codecs
import contextlib
import fcntl
from functools import lru_cache
import hashlib
import html
from io import BytesIO
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import tempfile
from urllib.parse import unquote, urlsplit
import xml.etree.ElementTree as ET
import zipfile

import bleach
import html5lib
from PIL import Image
from app import core as c
from app.covers import bounded_read, member_path, xml, MAX_IMAGE

MAX_CHAPTER = 4 * 1024 * 1024
IMAGE_TYPES = {'image/jpeg', 'image/png', 'image/gif', 'image/webp'}
IMAGE_EXTS = {'.jpg', '.jpeg', '.png', '.gif', '.webp'}
TAGS = {'p','div','span','br','h1','h2','h3','h4','h5','h6','section','article',
        'blockquote','pre','code','em','strong','b','i','u','s','sup','sub','small',
        'ul','ol','li','dl','dt','dd','hr','table','thead','tbody','tfoot','tr','td',
        'th','caption','figure','figcaption','img','a','ruby','rt','rp'}


def run_conversion(args, folder, output=None):
    """No shell, bounded time and output, isolated converter preferences."""
    converter = shutil.which(args[0])
    if converter is None:
        raise ValueError('Il convertitore non è disponibile. Controlla l’installazione del servizio.')
    # Run limits in a fresh process. Python callbacks after fork can deadlock a threaded server.
    setup = '''import os, resource, sys
resource.setrlimit(resource.RLIMIT_FSIZE, (512 * 1024 * 1024,) * 2)
resource.setrlimit(resource.RLIMIT_CPU, (85, 90))
os.execv(sys.argv[1], sys.argv[1:])'''
    env = dict(os.environ, QT_QPA_PLATFORM='offscreen', CALIBRE_CONFIG_DIRECTORY=str(folder/'config'),
               XDG_CACHE_HOME=str(folder/'cache'), TMPDIR=str(folder))
    try:
        with open(output, 'wb') if output else open(os.devnull, 'wb') as out:
            process = subprocess.Popen([sys.executable, '-I', '-c', setup, converter, *args[1:]],
                cwd=folder, env=env, stdout=out, stderr=subprocess.DEVNULL, start_new_session=True)
            try:
                code = process.wait(timeout=90)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
                raise ValueError('Conversione troppo lunga. Prova una copia EPUB o PDF del libro.') from None
        if code:
            raise ValueError('Impossibile aprire il libro: documento danneggiato, formato non compatibile o protezione DRM.')
    except FileNotFoundError:
        raise ValueError('Il convertitore non è disponibile. Controlla l’installazione del servizio.') from None


def convert_epub(source, target):
    # The converter only receives a private copy, never the library or its siblings.
    with tempfile.TemporaryDirectory(prefix='convert-', dir=target.parent) as tmp:
        folder = Path(tmp)
        copied = folder/('input'+source.suffix.lower())
        shutil.copyfile(source, copied)
        converted = folder/'book.epub'
        run_conversion(['ebook-convert', str(copied), str(converted)], folder)
        c.validate(converted)
        converted.replace(target)


def snapshot(row):
    path = c.safe_path(row)
    cache = c.data_dir()/'reader'
    cache.mkdir(exist_ok=True)
    folder = cache/row['digest']
    suffix = path.suffix.lower()
    output_suffix = suffix if suffix in {'.epub','.pdf','.txt','.html','.htm','.cbz'} else ('.txt' if suffix=='.doc' else '.epub')
    target = folder/('book'+output_suffix)
    with (cache/(row['digest']+'.lock')).open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if target.is_file():
            return target
        folder.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='source-', dir=folder) as tmp:
            copied = Path(tmp)/('source'+suffix)
            shutil.copyfile(path, copied)
            c.validate(copied)
            if c.engine.sha256(copied)!=row['digest']:
                raise ValueError('Il libro è cambiato. Aggiorna la cartella e riaprilo.')
            if output_suffix==suffix:
                copied.replace(target)
            elif suffix=='.doc':
                text = Path(tmp)/'book.txt'
                run_conversion(['antiword','-m','UTF-8.txt',str(copied)], Path(tmp), text)
                text.replace(target)
            else:
                convert_epub(copied, target)
    return target


def decode(raw):
    if raw.startswith((b'\xff\xfe', b'\xfe\xff')):
        return raw.decode('utf-16')
    try:
        return raw.decode('utf-8-sig')
    except UnicodeDecodeError:
        return raw.decode('cp1252', errors='replace')


@lru_cache(maxsize=32)
def text_layout(path):
    """Keep encoding and character boundaries consistent across all text sections."""
    with path.open('rb') as stream:prefix=stream.read(3)
    encoding,offset=('utf-16-le',2) if prefix.startswith(b'\xff\xfe') else (('utf-16-be',2) if prefix.startswith(b'\xfe\xff') else ('utf-8',3 if prefix.startswith(b'\xef\xbb\xbf') else 0))
    def sections(encoding,offset):
        decoder=codecs.getincrementaldecoder(encoding)(errors='strict' if encoding=='utf-8' else 'replace')
        bounds=[];cursor=start=offset
        with path.open('rb') as stream:
            stream.seek(offset)
            while raw:=stream.read(max(4,MAX_CHAPTER)):
                cursor+=len(raw);decoder.decode(raw,final=False)
                end=cursor-len(decoder.getstate()[0])
                if end>start:bounds.append((start,end));start=end
        decoder.decode(b'',final=True)
        if cursor>start:bounds.append((start,cursor))
        return bounds or [(offset,offset)]
    try:return encoding,sections(encoding,offset)
    except UnicodeDecodeError:return 'cp1252',sections('cp1252',0)


@lru_cache(maxsize=32)
def archive_book(path):
    try:
        with zipfile.ZipFile(path) as z:
            if path.suffix=='.cbz':
                images=sorted((n for n in z.namelist() if Path(n).suffix.lower() in IMAGE_EXTS and not n.startswith('__MACOSX/')),
                              key=lambda n:[int(x) if x.isdigit() else x.lower() for x in re.split(r'(\d+)',n)])
                if not images or len(images)>3000:
                    raise ValueError('Fumetto vuoto o con troppe pagine.')
                return {'chapters':[{'path':n,'title':'Pagina '+str(i+1),'title_key':'Pagina {number}','title_number':i+1} for i,n in enumerate(images)], 'images':images,'language':'und'}
            container=xml(z, 'META-INF/container.xml')
            root=next(e.get('full-path') for e in container.iter() if e.tag.rsplit('}',1)[-1]=='rootfile')
            package=member_path('',root)
            opf=xml(z,package)
            items={e.get('id'):e for e in opf.iter() if e.tag.rsplit('}',1)[-1]=='item'}
            images=[member_path(package,e.get('href')) for e in items.values() if e.get('media-type') in IMAGE_TYPES]
            chapters=[]
            for ref in opf.iter():
                if ref.tag.rsplit('}',1)[-1]!='itemref':
                    continue
                item=items.get(ref.get('idref'))
                if item is None or item.get('media-type') not in {'application/xhtml+xml','text/html'}:
                    continue
                name=member_path(package,item.get('href'))
                # Titles from the actual reading order, including legacy EPUB 2 books.
                doc=html5lib.parse(bounded_read(z,name,MAX_CHAPTER), namespaceHTMLElements=False)
                heading=next((e for e in doc.iter() if e.tag in {'h1','h2','h3','title'} and ''.join(e.itertext()).strip()),None)
                title=' '.join(''.join(heading.itertext()).split())[:160] if heading is not None else 'Capitolo '+str(len(chapters)+1)
                chapters.append({'path':name,'title':title,**({'title_key':'Capitolo {number}','title_number':len(chapters)+1} if heading is None else {})})
                if len(chapters)>3000:
                    raise ValueError('Il libro contiene troppi capitoli.')
            if not chapters:
                raise ValueError('Il libro non contiene capitoli leggibili. Potrebbe essere protetto da DRM.')
            language=next((''.join(e.itertext()).strip() for e in opf.iter() if e.tag.rsplit('}',1)[-1]=='language'),'und')
            return {'chapters':chapters,'images':images,'language':language if re.fullmatch(r'[A-Za-z]{2,8}(?:-[A-Za-z0-9]{1,8})*',language) else 'und'}
    except (zipfile.BadZipFile, KeyError, StopIteration, ET.ParseError, TypeError):
        raise ValueError('Libro danneggiato o protetto: impossibile leggere i capitoli.') from None


def manifest(row):
    path=snapshot(row)
    kind='pdf' if path.suffix=='.pdf' else 'book'
    if path.suffix in {'.epub','.cbz'}:
        chapters=archive_book(path)['chapters']
    else:
        # Split very long plain text into manageable reading sections.
        count=len(text_layout(path)[1]) if path.suffix=='.txt' else 1
        if path.suffix in {'.html','.htm'} and path.stat().st_size>MAX_CHAPTER:
            raise ValueError('Documento HTML troppo grande per il lettore (massimo 4 MB).')
        chapters=[{'title':'Lettura' if count==1 else 'Parte '+str(i+1),'title_key':'Lettura' if count==1 else 'Parte {number}','title_number':i+1} for i in range(count)]
    return {'kind':kind,'title':row['name'],'digest':row['digest'],
            'chapters':[{k:v for k,v in ch.items() if k!='path'} for ch in chapters],
            'language':archive_book(path).get('language','und') if path.suffix in {'.epub','.cbz'} else 'und',
            'converted':Path(row['name']).suffix.lower()!=path.suffix,
            'source_url':'/api/files/'+row['id']+'/reader/pdf' if kind=='pdf' else None}


def sanitized(raw, row, base='', book=None):
    tree=html5lib.parse(raw,namespaceHTMLElements=False)
    body=tree.find('body')
    # Remove executable and hidden content entirely, including its text.
    for parent in list(tree.iter()):
        for node in list(parent):
            if node.tag in {'script','style','iframe','object','embed','form','input','button','textarea','select','template'}:
                parent.remove(node)
    chapter_paths={ch['path']:i for i,ch in enumerate(book['chapters'])} if book else {}
    images={name:i for i,name in enumerate(book['images'])} if book else {}
    for node in body.iter():
        attrs=dict(node.attrib)
        node.attrib.clear()
        for key in ['lang','dir','title']:
            if key in attrs:node.set(key,attrs[key])
        if attrs.get('id'):node.set('id','book-'+attrs['id'])
        if node.tag=='a':
            href=attrs.get('href','')
            try:
                parts=urlsplit(href)
                dest=member_path(base,href) if parts.path else base
                if not parts.scheme and not parts.netloc and dest in chapter_paths:
                    node.set('href','#book-'+unquote(parts.fragment))
                    node.set('data-chapter',str(chapter_paths[dest]))
                    node.set('data-fragment',unquote(parts.fragment))
                elif href.startswith('#'):
                    node.set('href','#book-'+unquote(parts.fragment))
            except ValueError:
                pass
        if node.tag in {'img','{http://www.w3.org/2000/svg}image'}:
            href=attrs.get('src') or attrs.get('href') or attrs.get('{http://www.w3.org/1999/xlink}href') or ''
            try:
                dest=member_path(base,href)
                if dest in images:
                    node.tag='img'
                    node.set('src','/api/files/'+row['id']+'/reader/images/'+str(images[dest]))
                    node.set('alt',attrs.get('alt',''))
            except ValueError:
                pass
        for key in ['colspan','rowspan']:
            if attrs.get(key,'').isdigit() and int(attrs[key])<=100:node.set(key,attrs[key])
    content=html5lib.serialize(body,tree='etree',omit_optional_tags=False)
    return bleach.clean(content,tags=TAGS,attributes={'*':['id','lang','dir','title'],
        'a':['href','data-chapter','data-fragment'],'img':['src','alt'],'td':['colspan','rowspan'],'th':['colspan','rowspan']},
        protocols={'http','https'},strip=True)


def chapter(row, index):
    path=snapshot(row)
    if path.suffix in {'.epub','.cbz'}:
        book=archive_book(path)
        if not 0<=index<len(book['chapters']):
            raise ValueError('Capitolo non disponibile.')
        if path.suffix=='.cbz':
            return '<img src="/api/files/'+row['id']+'/reader/images/'+str(index)+'" alt="Pagina '+str(index+1)+'">'
        name=book['chapters'][index]['path']
        with zipfile.ZipFile(path) as z:
            return sanitized(bounded_read(z,name,MAX_CHAPTER),row,name,book)
    if path.suffix=='.txt':
        encoding,bounds=text_layout(path)
        if not 0<=index<len(bounds):raise ValueError('Capitolo non disponibile.')
        start,end=bounds[index]
        with path.open('rb') as stream:
            stream.seek(start);raw=stream.read(end-start)
        return '<pre class="plain-text">'+html.escape(raw.decode(encoding,errors='replace'))+'</pre>'
    if index!=0 or path.suffix not in {'.htm','.html'}:
        raise ValueError('Capitolo non disponibile.')
    if path.stat().st_size>MAX_CHAPTER:raise ValueError('Documento HTML troppo grande per il lettore.')
    return sanitized(path.read_bytes(),row)


def image(row,index):
    path=snapshot(row)
    if path.suffix not in {'.epub','.cbz'}:raise ValueError('Immagine non disponibile.')
    book=archive_book(path)
    if not 0<=index<len(book['images']):raise ValueError('Immagine non disponibile.')
    with zipfile.ZipFile(path) as z:
        raw=bounded_read(z,book['images'][index],MAX_IMAGE)
    # Rasterize to strip metadata and avoid serving active content from archives.
    with Image.open(BytesIO(raw),formats=['JPEG','PNG','GIF','WEBP']) as im:
        if im.width*im.height>16_000_000:raise ValueError('Immagine troppo grande.')
        im.thumbnail((1800,2400))
        result=BytesIO()
        rgba=im.convert('RGBA')
        rgb=Image.new('RGB',rgba.size,'white');rgb.paste(rgba,mask=rgba.getchannel('A'))
        rgb.save(result,format='JPEG',quality=88)
    result.seek(0)
    return result
