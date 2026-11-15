"""Local EPUB cover thumbnails. Never extract archives or fetch remote resources."""
import fcntl
from io import BytesIO
import os
from pathlib import Path
import posixpath
import uuid
from urllib.parse import unquote, urlsplit
import xml.etree.ElementTree as ET
import zipfile
from PIL import Image, UnidentifiedImageError
from app import core as c

MAX_XML=512*1024
MAX_IMAGE=12*1024*1024
Image.MAX_IMAGE_PIXELS=16_000_000


def member_path(base, href):
    parsed=urlsplit(href)
    if parsed.scheme or parsed.netloc:
        raise ValueError('Risorsa esterna esclusa.')
    name=unquote(parsed.path)
    if not name or name.startswith('/') or '\\' in name or '\x00' in name:
        raise ValueError('Percorso non valido.')
    name=posixpath.normpath(posixpath.join(posixpath.dirname(base),name))
    if name=='..' or name.startswith('../'):
        raise ValueError('Percorso non valido.')
    return name


def bounded_read(archive,name,limit):
    info=archive.getinfo(name)
    if info.file_size>limit or info.flag_bits & 1:
        raise ValueError('Risorsa troppo grande o cifrata.')
    with archive.open(info) as stream:
        content=stream.read(limit+1)
    if len(content)>limit:
        raise ValueError('Risorsa troppo grande.')
    return content


def xml(archive,name):
    data=bounded_read(archive,name,MAX_XML)
    upper=data.upper()
    if b'<!DOCTYPE' in upper or b'<!ENTITY' in upper:
        raise ValueError('Dichiarazioni XML non ammesse.')
    return ET.fromstring(data)


def cover_image(archive):
    if len(archive.infolist())>20000:
        return None
    container=xml(archive,'META-INF/container.xml')
    rootfile=next((e.get('full-path') for e in container.iter() if e.tag.rsplit('}',1)[-1]=='rootfile'),None)
    if not rootfile:
        return None
    package=member_path('',rootfile)
    opf=xml(archive,package)
    items=[e for e in opf.iter() if e.tag.rsplit('}',1)[-1]=='item']
    images=[e for e in items if e.get('media-type') in ['image/jpeg','image/png','image/webp']]
    cover_id=next((e.get('content') for e in opf.iter() if e.tag.rsplit('}',1)[-1]=='meta' and e.get('name','').lower()=='cover'),None)
    candidates=[e.get('href') for e in images if 'cover-image' in e.get('properties','').split()]
    candidates += [e.get('href') for e in images if cover_id and e.get('id')==cover_id]
    # Legacy cover pages may wrap a JPEG/PNG in XHTML or SVG markup.
    guides=[e.get('href') for e in opf.iter() if e.tag.rsplit('}',1)[-1]=='reference' and e.get('type')=='cover']
    for href in guides:
        if not href:continue
        page=member_path(package,href)
        if Path(page).suffix.lower() in ['.jpg','.jpeg','.png','.webp']:
            candidates.append(href);continue
        try:
            doc=xml(archive,page)
            for el in doc.iter():
                if el.tag.rsplit('}',1)[-1] in ['img','image']:
                    src=el.get('src') or el.get('href') or el.get('{http://www.w3.org/1999/xlink}href')
                    if src:
                        target=member_path(page,src)
                        if Path(target).suffix.lower() in ['.jpg','.jpeg','.png','.webp']:
                            return bounded_read(archive,target,MAX_IMAGE)
        except (ValueError,KeyError,ET.ParseError):
            continue
    candidates += [e.get('href') for e in images if any(word in e.get('href','').lower() for word in ['cover','front','copertina'])]
    for href in dict.fromkeys(candidates):
        if not href:continue
        try:return bounded_read(archive,member_path(package,href),MAX_IMAGE)
        except (ValueError,KeyError):continue
    return None


def thumbnail(row):
    path=c.safe_path(row)
    if path.suffix.lower()!='.epub':return None
    cache=c.data_dir()/'covers'
    cache.mkdir(exist_ok=True)
    target=cache/(row['digest']+'.jpg')
    absent=cache/(row['digest']+'.none')
    with (cache/(row['digest']+'.lock')).open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        if target.is_file():return target
        if absent.exists():return None
        try:
            with zipfile.ZipFile(path) as archive:raw=cover_image(archive)
            if raw is None:
                absent.touch();return None
            with Image.open(BytesIO(raw),formats=['JPEG','PNG','WEBP']) as im:
                if im.width*im.height>16_000_000:raise ValueError('Immagine troppo grande.')
                im.thumbnail((240,360))
                rgb=Image.new('RGB',im.size,'white')
                rgba=im.convert('RGBA');rgb.paste(rgba,mask=rgba.getchannel('A'))
                temp=cache/(uuid.uuid4().hex+'.tmp')
                try:
                    with temp.open('xb') as out:
                        os.chmod(temp,0o600)
                        rgb.save(out,format='JPEG',quality=82,optimize=True)
                        out.flush();os.fsync(out.fileno())
                    temp.replace(target)
                finally:
                    if temp.exists():temp.unlink()
            return target
        except (OSError,ValueError,KeyError,ET.ParseError,zipfile.BadZipFile,UnidentifiedImageError,Image.DecompressionBombError):
            absent.touch();return None
