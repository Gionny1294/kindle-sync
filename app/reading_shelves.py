"""Personal bookshelves stored in the current Kindle Sync account."""
import contextlib
from datetime import datetime
import csv
import io
import json
import re
import time
import unicodedata
import uuid

from app import core as c
from app import goodreads_ratings, reading_list, reading_objects
from app.recommendations import ROME

MAX_CSV_SIZE = 8 * 1024 * 1024
GOODREADS = 'https://www.goodreads.com/book/show/'
SYSTEM_SHELVES = {'read', 'to-read', 'currently-reading'}


def normalize(name):
    return ' '.join(unicodedata.normalize('NFKC', name).casefold().split())


def ordered_ids(db, case_id):
    query = """SELECT r.id FROM reading_list r
        LEFT JOIN reading_entry_views v ON v.entry_id=r.id
        LEFT JOIN reading_shelf_order o ON o.entry_id=r.id AND o.case_id=?
        WHERE COALESCE(v.in_library,1)=1 AND """
    if case_id:
        query += "EXISTS (SELECT 1 FROM reading_shelf_books b WHERE b.entry_id=r.id AND b.shelf_id=?)"
        args = (case_id,case_id)
    else:
        query += "NOT EXISTS (SELECT 1 FROM reading_shelf_books b WHERE b.entry_id=r.id)"
        args = (case_id,)
    query += " ORDER BY o.position IS NULL,o.position,r.created DESC,r.id"
    return [row['id'] for row in db.execute(query,args)]


def snapshot(db):
    shelves = {row['id']:dict(row,books=[]) for row in db.execute(
        'SELECT id,name FROM reading_shelves ORDER BY name COLLATE NOCASE,id')}
    ordered=db.execute("""SELECT b.shelf_id,b.entry_id FROM reading_shelf_books b
       JOIN reading_list r ON r.id=b.entry_id
       LEFT JOIN reading_entry_views v ON v.entry_id=b.entry_id
       LEFT JOIN reading_shelf_order o ON o.case_id=b.shelf_id AND o.entry_id=b.entry_id
       WHERE COALESCE(v.in_library,1)=1
       ORDER BY b.shelf_id,o.position IS NULL,o.position,r.created DESC,r.id""")
    for row in ordered:
        shelves[row['shelf_id']]['books'].append(row['entry_id'])
    return list(shelves.values())


def reorder(db, body):
    case_id, entry_id, adjacent = (body.get(key) for key in ('case_id','id','adjacent'))
    after = body.get('after')
    if not all(isinstance(value,str) for value in (case_id,entry_id,adjacent)) or not isinstance(after,bool):
        raise ValueError('Richiesta non valida.')
    order = ordered_ids(db,case_id)
    if entry_id == adjacent or entry_id not in order or adjacent not in order:
        raise ValueError('Il libro non si trova più in questa libreria.')
    order.remove(entry_id)
    order.insert(order.index(adjacent)+int(after),entry_id)
    db.execute('DELETE FROM reading_shelf_order WHERE case_id=?',(case_id,))
    db.executemany('INSERT INTO reading_shelf_order VALUES (?,?,?)',
                   ((case_id,book,index) for index,book in enumerate(order)))
    return {'order':order}


def shelf_name(value):
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > 80:
        raise ValueError('Inserisci un nome per lo scaffale, fino a 80 caratteri.')
    return value.strip()


def shelf_id(db, name, existing):
    key = normalize(name)
    if key not in existing:
        value = uuid.uuid4().hex
        db.execute('INSERT INTO reading_shelves(id,name,normalized) VALUES (?,?,?)', (value, name, key))
        existing[key] = value
    return existing[key]


def change(body):
    operation = body.get('operation')
    identifier = body.get('id')
    if operation == 'file-book':
        return {'id':reading_list.update({'file_id':identifier},view='in_library')}
    if operation in ('rename','delete','place-book','remove-book','queue-book') and not isinstance(identifier,str):
        raise ValueError('Richiesta non valida.')
    with c.transaction() as db:
        if operation=='move-slot':
            return reading_objects.move(db,body)
        if operation == 'reorder':
            return reorder(db,body)
        if operation in ('remove-book','queue-book'):
            if not db.execute('SELECT 1 FROM reading_list WHERE id=?',(identifier,)).fetchone():
                raise ValueError('Libro non trovato.')
            reading_list.set_view(db,identifier,'in_queue' if operation=='queue-book' else 'in_library',
                                  operation=='queue-book')
            return {}
        if operation in ('create', 'rename'):
            name = shelf_name(body.get('name'))
            current = body.get('id') if operation == 'rename' else None
            duplicate = db.execute('SELECT id FROM reading_shelves WHERE normalized=?', (normalize(name),)).fetchone()
            if duplicate and duplicate['id'] != current:
                raise ValueError('Esiste già uno scaffale con questo nome.')
            if operation == 'create':
                return {'id': shelf_id(db, name, {})}
            if not db.execute('UPDATE reading_shelves SET name=?,normalized=? WHERE id=?',
                              (name, normalize(name), current)).rowcount:
                raise ValueError('Scaffale non trovato.')
            return {'id': current}
        if operation == 'delete':
            if not db.execute('DELETE FROM reading_shelves WHERE id=?', (body.get('id'),)).rowcount:
                raise ValueError('Scaffale non trovato.')
            db.execute('DELETE FROM reading_shelf_order WHERE case_id=?',(identifier,))
            db.execute('DELETE FROM reading_shelf_layout WHERE case_id=?',(identifier,))
            db.execute("UPDATE reading_shelf_objects SET case_id='' WHERE case_id=?",(identifier,))
            return {}
        if operation in ('add-book', 'place-book'):
            chosen = body.get('shelves')
            if not isinstance(chosen, list) or not all(isinstance(item, str) for item in chosen):
                raise ValueError('Seleziona gli scaffali del libro.')
            chosen = set(chosen)
            if not chosen <= {row['id'] for row in db.execute('SELECT id FROM reading_shelves')}:
                raise ValueError('Uno scaffale selezionato non esiste più.')
            if operation == 'add-book':
                raw_title = body.get('title')
                raw_author = body.get('author', '')
                is_read = body.get('is_read')
                if not isinstance(raw_title, str) or not 1 <= len(raw_title.strip()) <= 500:
                    raise ValueError('Inserisci un titolo di massimo 500 caratteri.')
                if not isinstance(raw_author, str) or len(raw_author.strip()) > 500 or not isinstance(is_read, bool):
                    raise ValueError('Controlla autore e stato di lettura.')
                entry = dict(title=raw_title.strip(), author=raw_author.strip(), isbn='', url='', file_digest=None)
                entry_id = insert_entry(db, entry)
                reading_list.update_read_mark(db,entry_id,is_read,
                    finished_at=time.time() if is_read else None,
                    rating_half=reading_list.rating_parts(body.get('personal_rating')) if is_read else None)
            else:
                entry_id = body.get('id')
                if not db.execute('SELECT 1 FROM reading_list WHERE id=?', (entry_id,)).fetchone():
                    raise ValueError('Libro non trovato.')
                reading_list.set_view(db,entry_id,'in_library',True)
            old_shelves={row['shelf_id'] for row in db.execute(
                'SELECT shelf_id FROM reading_shelf_books WHERE entry_id=?',(entry_id,))}
            db.executemany('DELETE FROM reading_shelf_order WHERE case_id=? AND entry_id=?',
                           ((shelf,entry_id) for shelf in old_shelves-chosen))
            db.executemany('DELETE FROM reading_shelf_layout WHERE case_id=? AND item=?',
                           ((shelf,'book:'+entry_id) for shelf in old_shelves-chosen))
            if chosen:
                db.execute("DELETE FROM reading_shelf_order WHERE case_id='' AND entry_id=?",(entry_id,))
                db.execute("DELETE FROM reading_shelf_layout WHERE case_id='' AND item=?",('book:'+entry_id,))
            db.execute('DELETE FROM reading_shelf_books WHERE entry_id=?', (entry_id,))
            db.executemany('INSERT INTO reading_shelf_books(shelf_id,entry_id) VALUES (?,?)',
                           ((item, entry_id) for item in chosen))
            return {'id': entry_id}
    raise ValueError('Richiesta non valida.')


def parse_goodreads(content):
    if not isinstance(content, str) or len(content.encode('utf-8')) > MAX_CSV_SIZE:
        raise ValueError('Scegli un CSV Goodreads di massimo 8 MB.')
    rows = csv.DictReader(io.StringIO(content.removeprefix('\ufeff'), newline=''), strict=True)
    headers = {normalize(value): value for value in (rows.fieldnames or [])}
    if not {'title', 'exclusive shelf'} <= headers.keys():
        raise ValueError('Mancano le colonne Title o Exclusive Shelf del CSV Goodreads.')
    result, skipped = [], 0
    try:
        for row in rows:
            def field(name):
                value = row.get(headers[name]) if name in headers else None
                return value.strip() if isinstance(value, str) else ''

            if not (title := field('title')):
                skipped += 1
                continue
            book_id = field('book id')
            exclusive = normalize(field('exclusive shelf'))
            categories = []
            for name in [*field('bookshelves').split(','), field('exclusive shelf')]:
                name = name.strip()[:80]
                if name and normalize(name) not in SYSTEM_SHELVES:
                    categories.append(name)
            finished=None
            if exclusive=='read':
                try:
                    finished=datetime.strptime(field('date read'),'%Y/%m/%d').replace(
                        tzinfo=ROME,hour=12).timestamp()
                except ValueError:
                    pass
            own=field('my rating').replace(',','.')
            try:
                rating=reading_list.rating_parts(float(own))
            except ValueError:
                rating=None
            url=GOODREADS+book_id if re.fullmatch(r'[1-9]\d{0,19}',book_id,re.ASCII) else ''
            result.append({
                'title': title[:500], 'author': field('author')[:500],
                'isbn': reading_list.isbn_key(field('isbn13')) or reading_list.isbn_key(field('isbn')),
                'url':url,'file_digest':None,'shelves':categories,'is_read':exclusive=='read',
                'finished_at':finished,'rating_half':rating if exclusive=='read' else None,
                **goodreads_ratings.csv_rating(field('average rating'),field('ratings count'),url)
            })
    except csv.Error as exc:
        raise ValueError('Il file CSV è incompleto oppure contiene un campo non valido.') from exc
    if not result:
        raise ValueError('Il CSV non contiene libri da importare.')
    return result, skipped


def insert_entry(db, book):
    entry = uuid.uuid4().hex
    db.execute('INSERT INTO reading_list VALUES (?,?,?,?,?,?,?,?)',
               (entry, book['title'], book['author'], book['isbn'], book['url'], None,
                book['file_digest'], time.time()))
    reading_list.add_view(db,entry,'in_library')
    return entry


def plan(db, books):
    entries = reading_list.entries(db)
    by_url = {book['url']: book for book in entries if book['url']}
    by_isbn = {reading_list.isbn_key(book['isbn']): book for book in entries if book['isbn']}
    by_identity = {reading_list.fingerprint(book)['reading_key']: book for book in entries}
    changes = {}
    for book in books:
        identity = reading_list.fingerprint(book)['reading_key']
        match = by_url.get(book['url']) or by_isbn.get(book['isbn']) or by_identity.get(identity)
        if match is None:
            match = dict(book, id=None)
        if match['isbn'] and book['isbn'] and match['isbn'] != book['isbn'] and match['url'] != book['url']:
            match = dict(book, id=None)
        key = ('entry', match['id']) if match['id'] else ('csv', match['url'], match['isbn'], reading_list.fingerprint(match)['reading_key'])
        if book['url']:
            by_url[book['url']] = match
        if book['isbn']:
            by_isbn[book['isbn']] = match
        by_identity[identity] = match
        if key in changes:
            book['shelves'] = changes[key][1]['shelves'] + book['shelves']
        changes[key] = match, book
    return entries, list(changes.values())


def import_goodreads(body):
    try:
        books, skipped = parse_goodreads(body.get('csv'))
    except csv.Error as exc:
        raise ValueError('Il file CSV è incompleto o non valido.') from exc
    preview = body.get('preview') is True
    connection = contextlib.closing(c.connect()) if preview else c.transaction()
    with connection as db:
        entries, changes = plan(db, books)
        existing = dict(db.execute('SELECT normalized,id FROM reading_shelves'))
        new_shelves = {normalize(name) for _, book in changes for name in book['shelves']} - existing.keys()
        added = sum(match['id'] is None for match, _ in changes)
        if not preview:
            catalog=reading_list.current_catalog(db)
            file_marks={}
            own_entries={row[0] for row in db.execute('SELECT entry_id FROM reading_finish WHERE rating_half IS NOT NULL')}
            own_files={row[0] for row in db.execute('SELECT digest FROM reading_file_finish WHERE rating_half IS NOT NULL')}
            for match, book in changes:
                if match['id'] is None:
                    # Use the imported bibliographic identity to link a previously uploaded book.
                    probe = dict(book, url=book['url'] or GOODREADS)
                    linked = catalog.match(probe, entries)
                    if linked:
                        book['file_digest'] = linked['digest']
                    match = dict(book, id=insert_entry(db, book))
                    entries.append(match)
                elif (not match['url'] and book['url']) or (not match['isbn'] and book['isbn']):
                    match = dict(match, isbn=match['isbn'] or book['isbn'], url=match['url'] or book['url'])
                    db.execute('UPDATE reading_list SET isbn=?,url=? WHERE id=?',
                               (match['isbn'], match['url'], match['id']))
                reading_list.set_view(db,match['id'],'in_library',True)
                selected_rating=None if match['id'] in own_entries else book['rating_half']
                reading_list.update_read_mark(db,match['id'],book['is_read'],finished_at=book['finished_at'],
                                             rating_half=selected_rating)
                goodreads_ratings.remember(db,match['id'],book)
                if file := catalog.match(match,entries):
                    digest=file['digest']
                    file_marks[digest]=(book,None if digest in own_files else selected_rating)
                db.executemany('INSERT OR IGNORE INTO reading_shelf_books(shelf_id,entry_id) VALUES (?,?)', (
                    (shelf_id(db, name, existing), match['id']) for name in book['shelves']
                ))
            for digest,(book,selected_rating) in file_marks.items():
                reading_list.set_file_mark(db,digest,book['is_read'],entries,catalog,
                    finished_at=book['finished_at'],rating_half=selected_rating)
    return dict(added=added, updated=len(changes) - added, shelves=len(new_shelves), skipped=skipped)
