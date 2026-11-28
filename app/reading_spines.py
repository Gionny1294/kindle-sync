"""Private spine photos and display proportions for the current reading profile."""
import base64
import binascii
import contextlib
import io
import os
from pathlib import Path
import re
import subprocess
import uuid
from xml.etree import ElementTree as ET
import zipfile

from PIL import Image, ImageOps

from app import core as c, reading_list
from app.covers import bounded_read, member_path, xml

MAX_IMAGE = 1_500_000
EPUB_NS = '{http://www.idpf.org/2007/ops}'


def snapshot(db):
    return {row['entry_id']: {
        'url': f"/api/reading-shelves/{row['entry_id']}/spine?v={row['revision']}",
        'active': bool(row['show_spine']), 'height': row['height_class'], 'width': row['width_class']
    } for row in db.execute('''SELECT s.entry_id,revision,show_spine,height_class,width_class
        FROM reading_book_spines s LEFT JOIN reading_entry_views v ON v.entry_id=s.entry_id
        WHERE COALESCE(v.in_library,1)=1''')}


def photo(data):
    if not isinstance(data,str) or len(data) > (MAX_IMAGE * 4 // 3 + 8):
        raise ValueError('Scegli una foto del dorso più piccola.')
    try:
        raw = base64.b64decode(data,validate=True)
        with Image.open(io.BytesIO(raw),formats=['JPEG','PNG','WEBP']) as original:
            width,height = original.size
            if width < 8 or height < 30 or width * height > 2_000_000 or width > height * .7:
                raise ValueError('Ritaglia solo il dorso verticale del libro.')
            original.load()
            managed=ImageOps.exif_transpose(original).convert('RGB')
            managed.thumbnail((380,1030),Image.Resampling.LANCZOS)
            output=io.BytesIO()
            managed.save(output,format='WEBP',quality=86,method=4)
            return output.getvalue()
    except (binascii.Error,OSError,Image.DecompressionBombError) as exc:
        raise ValueError('Formato della foto del dorso non valido.') from exc


def tag_name(node):
    return node.tag.rsplit('}',1)[-1]


def epub_pages(path):
    """Use pagination when available, otherwise estimate 280 words per page."""
    with zipfile.ZipFile(path) as archive:
        if len(archive.filelist)>10000:
            return 0
        container=xml(archive,'META-INF/container.xml')
        rootfile=next((node.get('full-path') for node in container.iter()
                       if tag_name(node)=='rootfile' and node.get('full-path')),None)
        if not rootfile:
            return 0
        package=member_path('',rootfile)
        opf=xml(archive,package)
        for node in opf.iter():
            if tag_name(node)!='meta':
                continue
            property_name=(node.get('property') or node.get('name') or '').lower()
            value=(node.get('content') or (node.text or '')).strip()
            if property_name in ('schema:numberofpages','pagecount','calibre:page_count') and re.fullmatch(r'[1-9]\d{0,4}',value):
                return int(value)

        items={node.get('id'):node for node in opf.iter() if tag_name(node)=='item' and node.get('href')}
        for node in items.values():
            is_navigation='nav' in node.get('properties','').split()
            is_ncx=node.get('media-type')=='application/x-dtbncx+xml'
            if not (is_navigation or is_ncx):
                continue
            try:
                navigation=xml(archive,member_path(package,node.get('href')))
            except (KeyError,ValueError,ET.ParseError):
                continue
            if is_ncx:
                count=sum(tag_name(page)=='pageTarget' for page in navigation.iter())
            else:
                count=sum(tag_name(link)=='a' and bool(link.get('href'))
                          for section in navigation.iter()
                          if tag_name(section)=='nav' and 'page-list' in section.get(EPUB_NS+'type','').split()
                          for link in section.iter())
            if count:
                return count

        words=0
        remaining=6*1024*1024
        spine=[node for node in opf.iter() if tag_name(node)=='itemref' and node.get('linear')!='no']
        for ref in spine[:600]:
            item=items.get(ref.get('idref'))
            if item is None or item.get('media-type') not in ('application/xhtml+xml','text/html'):
                continue
            try:
                raw=bounded_read(archive,member_path(package,item.get('href')),min(remaining,1024*1024))
            except (KeyError,ValueError):
                continue
            remaining-=len(raw)
            text=raw.decode('utf-8',errors='replace')
            text=re.sub(r'<[^<>]*>',' ',text)
            words+=len(re.findall(r'\b\w+\b',text))
            if remaining<=0:
                break
        return max(1,round(words/280)) if words else 0


def visual_size(row):
    """Return height and width classes from the linked file, or a standard size."""
    if row is None:
        return 2,1
    suffix=Path(row['name']).suffix.lower()
    if suffix not in ('.pdf','.epub'):
        return 2,1
    points=pages=0
    try:
        path=c.safe_path(row)
        if suffix=='.pdf':
            output=subprocess.run(['pdfinfo','-f','1','-l','1',str(path)],capture_output=True,
                                  timeout=4,check=True,text=True,env={**os.environ,'LC_ALL':'C'}).stdout
            count=re.search(r'^Pages:\s+(\d+)',output,re.M)
            size=re.search(r'^Page\s+(?:1\s+)?size:\s+[\d.]+\s+x\s+([\d.]+)\s+pts',output,re.M)
            pages=int(count.group(1)) if count else 0
            points=float(size.group(1)) if size else 0
        else:
            pages=epub_pages(path)
    except (OSError,ValueError,KeyError,ET.ParseError,zipfile.BadZipFile,subprocess.SubprocessError):
        return 2,1
    if not pages and not points:
        return 2,1
    if points:
        height=sum(points>=limit for limit in (430,560,700,830))
    else:
        height=sum(pages>=limit for limit in (55,130,285,500))
    width=sum(pages>=limit for limit in (95,230,420))
    return height,width


def resolve_entry(db,entry_id):
    books=reading_list.entries(db)
    entry=next((book for book in books if book['id']==entry_id),None)
    library,_=reading_list.split_views(db,[entry] if entry else [])
    if not library:
        raise ValueError('Libro non presente nella libreria.')
    return entry,reading_list.current_catalog(db).match(entry,books)


def set_display(entry_id,body):
    active=body.get('active')
    if not isinstance(active,bool):
        raise ValueError('Richiesta non valida.')
    image=photo(body['photo']) if 'photo' in body else None
    with contextlib.closing(c.connect()) as db:
        _,file=resolve_entry(db,entry_id)
    height,width=visual_size(file) if active else (2,1)
    with c.transaction() as db:
        available=db.execute('''SELECT 1 FROM reading_list r LEFT JOIN reading_entry_views v ON v.entry_id=r.id
            WHERE r.id=? AND COALESCE(v.in_library,1)=1''',(entry_id,)).fetchone()
        if not available:
            raise ValueError('Libro non presente nella libreria.')
        if image is not None:
            db.execute('''INSERT INTO reading_book_spines VALUES (?,?,?,?,?,?)
                ON CONFLICT(entry_id) DO UPDATE SET image=excluded.image,revision=excluded.revision,
                  show_spine=excluded.show_spine,height_class=excluded.height_class,width_class=excluded.width_class''',
                (entry_id,image,uuid.uuid4().hex,int(active),height,width))
        else:
            if active:
                cursor=db.execute('UPDATE reading_book_spines SET show_spine=1,height_class=?,width_class=? WHERE entry_id=?',
                                  (height,width,entry_id))
            else:
                cursor=db.execute('UPDATE reading_book_spines SET show_spine=0 WHERE entry_id=?',(entry_id,))
            if not cursor.rowcount:
                raise ValueError('Scatta o seleziona prima una foto del dorso.')
    return {'ok':True}


def get_photo(entry_id):
    with contextlib.closing(c.connect()) as db:
        row=db.execute('''SELECT s.image FROM reading_book_spines s
            LEFT JOIN reading_entry_views v ON v.entry_id=s.entry_id
            WHERE s.entry_id=? AND COALESCE(v.in_library,1)=1''',(entry_id,)).fetchone()
    return row['image'] if row else None
