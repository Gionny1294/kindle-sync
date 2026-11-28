"""Objects, photo stands and mixed item positions on a personal shelf."""
import base64
import binascii
import contextlib
import io
import time
import uuid

from PIL import Image, ImageOps

from app import core as c

PRESETS={'plant','bricks','bookend'}


def case_exists(db,case_id):
    return isinstance(case_id,str) and (not case_id or bool(db.execute(
        'SELECT 1 FROM reading_shelves WHERE id=?',(case_id,)).fetchone()))


def photo(value):
    if not isinstance(value,str) or len(value)>3_000_000:
        raise ValueError('Scegli una foto più piccola.')
    try:
        raw=base64.b64decode(value,validate=True)
        with Image.open(io.BytesIO(raw),formats=['JPEG','WEBP','PNG']) as source:
            width,height=source.size
            if not width or not height or width*height>3_000_000:
                raise ValueError('Scegli una foto più piccola.')
            source.load()
            image=ImageOps.exif_transpose(source).convert('RGB')
            image.thumbnail((760,760),Image.Resampling.LANCZOS)
            result=io.BytesIO()
            image.save(result,'WEBP',quality=86,method=4)
            return result.getvalue()
    except (OSError,binascii.Error,Image.DecompressionBombError) as exc:
        raise ValueError('Formato della foto non valido.') from exc


def save(body):
    operation=body.get('operation')
    if operation=='delete':
        identifier=body.get('id')
        if not isinstance(identifier,str):
            raise ValueError('Richiesta non valida.')
        with c.transaction() as db:
            if not db.execute('DELETE FROM reading_shelf_objects WHERE id=?',(identifier,)).rowcount:
                raise ValueError('Oggetto non disponibile.')
            db.execute('DELETE FROM reading_shelf_layout WHERE item=?',('object:'+identifier,))
        return {'ok':True}
    if operation!='add':
        raise ValueError('Richiesta non valida.')
    kind,case_id=body.get('kind'),body.get('case_id')
    if not isinstance(kind,str) or kind not in PRESETS|{'photo'}:
        raise ValueError('Scegli un oggetto o una foto.')
    name=body.get('name','')
    if not isinstance(name,str) or len(name.strip())>100:
        raise ValueError('Nome della foto non valido.')
    image=photo(body.get('photo')) if kind=='photo' else b''
    with c.transaction() as db:
        if not case_exists(db,case_id):
            raise ValueError('Libreria non disponibile.')
        identifier=uuid.uuid4().hex
        db.execute('INSERT INTO reading_shelf_objects VALUES (?,?,?,?,?,?,?)',
                   (identifier,case_id,name.strip(),kind,image,uuid.uuid4().hex,time.time()))
    return {'id':identifier}


def photo_data(identifier):
    with contextlib.closing(c.connect()) as db:
        row=db.execute("SELECT image FROM reading_shelf_objects WHERE id=? AND kind='photo'",(identifier,)).fetchone()
    return row['image'] if row else None


def ordered_layout(db,case_id,books):
    available=['book:'+entry for entry in books]
    available.extend('object:'+row['id'] for row in db.execute(
        'SELECT id FROM reading_shelf_objects WHERE case_id=? ORDER BY created,id',(case_id,)))
    return apply_positions(available,[row['item'] for row in db.execute(
        'SELECT item FROM reading_shelf_layout WHERE case_id=? ORDER BY position,item',(case_id,))])


def apply_positions(available,positions):
    allowed=set(available)
    visible=[item for item in positions if item in allowed]
    known=set(visible)
    return visible+[item for item in available if item not in known]


def snapshot(db,cases,unsorted):
    objects={}
    layouts={}
    for row in db.execute('SELECT id,case_id,name,kind,revision FROM reading_shelf_objects ORDER BY created,id'):
        identifier=row['id']
        kind=row['kind']
        objects.setdefault(row['case_id'],[]).append({
            'id':identifier,'kind':kind,'name':row['name'],
            'url':f"/api/shelf-objects/{identifier}/photo?v={row['revision']}" if kind=='photo'
                  else f'/static/editorial/objects/{kind}.webp'})
    overrides={}
    for row in db.execute('SELECT case_id,item FROM reading_shelf_layout ORDER BY case_id,position,item'):
        overrides.setdefault(row['case_id'],[]).append(row['item'])
    for case_id,books in [('',unsorted),*((case['id'],case['books']) for case in cases)]:
        available=['book:'+identifier for identifier in books]
        available.extend('object:'+item['id'] for item in objects.get(case_id,[]))
        layouts[case_id]=apply_positions(available,overrides.get(case_id,[]))
    return objects,layouts


def write_layout(db,case_id,items):
    db.execute('DELETE FROM reading_shelf_layout WHERE case_id=?',(case_id,))
    db.executemany('INSERT INTO reading_shelf_layout VALUES (?,?,?)',
                   ((case_id,item,index) for index,item in enumerate(items)))


def move(db,body):
    from app.reading_shelves import ordered_ids
    source,target=body.get('case_id'),body.get('target_case')
    item,adjacent=body.get('item'),body.get('adjacent')
    after=body.get('after')
    if not case_exists(db,source) or not case_exists(db,target) or not isinstance(item,str):
        raise ValueError('Libreria o oggetto non disponibile.')
    if (adjacent is not None and not isinstance(adjacent,str)) or not isinstance(after,bool):
        raise ValueError('Posizione non valida.')
    original=ordered_layout(db,source,ordered_ids(db,source))
    destination=original.copy() if source==target else ordered_layout(db,target,ordered_ids(db,target))
    if item not in original or (adjacent is not None and (adjacent not in destination or adjacent==item)):
        raise ValueError('Il ripiano è cambiato. Riprova a spostare.')
    if source!=target:
        kind,identifier=item.split(':',1)
        if kind=='book':
            others=db.execute('SELECT count(*) FROM reading_shelf_books WHERE entry_id=? AND shelf_id!=?',
                              (identifier,source)).fetchone()[0]
            if not target and others:
                raise ValueError('Il libro è in più librerie. Usa Scegli librerie per toglierlo da tutte.')
            db.execute('DELETE FROM reading_shelf_books WHERE entry_id=? AND shelf_id=?',(identifier,source))
            db.execute('DELETE FROM reading_shelf_order WHERE entry_id=? AND case_id=?',(identifier,source))
            if target:
                db.execute('INSERT OR IGNORE INTO reading_shelf_books VALUES (?,?)',(target,identifier))
        else:
            db.execute('UPDATE reading_shelf_objects SET case_id=? WHERE id=?',(target,identifier))
        original.remove(item)
        write_layout(db,source,original)
    if item in destination:
        destination.remove(item)
    offset=destination.index(adjacent)+int(after) if adjacent is not None else len(destination)
    destination.insert(offset,item)
    write_layout(db,target,destination)
    return {'ok':True}
