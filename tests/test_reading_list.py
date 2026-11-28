import base64
import contextlib
from datetime import datetime
import io
import json
import time
import zipfile

import pytest
from PIL import Image

from app import book_catalog, core, reading_list, recommendations, telegram_books


@pytest.fixture
def client(tmp_path, monkeypatch):
    data = tmp_path / 'data'
    library = tmp_path / 'library'
    library.mkdir()
    monkeypatch.setattr(core, 'DATA', data)
    monkeypatch.setattr(core, 'LIBRARY', library)
    monkeypatch.setattr(core, 'CONFIG', data / 'kindle-sync.conf')
    from app import accounts, web
    core.init()
    accounts.init()
    return web.app.test_client()


def register(client, name):
    csrf = client.get('/api/session').json['csrf']
    response = client.post('/api/auth/register', json={'name': name, 'password': 'test-password'},
                           headers={'X-CSRF-Token': csrf})
    assert response.status_code == 200, response.json
    return {'X-CSRF-Token': response.json['csrf'], 'X-Account-ID': response.json['user']['id']}


def upload(client, headers, name, content):
    response = client.post('/api/upload', headers=headers, data={'file': (io.BytesIO(content), name)})
    assert response.status_code == 201, response.json
    return response.json['file_id']


def recommend(cover=''):
    source = recommendations.DEFAULTS[0]
    today = datetime.now(recommendations.ROME).date().isoformat()
    book = {'title': 'Una nuova storia', 'author': 'Luca Riva', 'isbn': '9780306406157',
            'url': 'https://www.example.com/book', 'cover': cover, 'published': today, 'rank': 1}
    with core.transaction() as db:
        recommendations.initialize(db)
        core.put(db, 'recommendation_sources', json.dumps([source]))
        cache = {source['id']: {'url': source['url'], 'books': [book], 'checked': time.time()}}
        core.put(db, 'recommendation_cache', json.dumps(cache))
    return book


def epub(title='Una nuova storia', author='Luca Riva', isbn='0-306-40615-2'):
    data = io.BytesIO()
    with zipfile.ZipFile(data, 'w') as archive:
        archive.writestr('META-INF/container.xml', '<container><rootfile full-path="book.opf"/></container>')
        archive.writestr('book.opf', f'''<package xmlns:dc="http://purl.org/dc/elements/1.1/">
            <metadata><dc:title>{title}</dc:title><dc:creator>{author}</dc:creator>
            <dc:identifier>urn:isbn:{isbn}</dc:identifier></metadata></package>''')
    return data.getvalue()


def test_saved_recommendation_links_later_upload_and_survives_refresh(client):
    headers = register(client, 'First')
    book = recommend()
    source = {'recommendation_url': book['url']}
    assert client.post('/api/reading-list', headers=headers, json=source).status_code == 200
    assert client.post('/api/reading-list', headers=headers, json=source).status_code == 200
    entry, = client.get('/api/state').json['reading_list']
    assert entry['file_id'] is None

    # A saved recommendation stays available when the chart changes.
    with core.transaction() as db:
        core.put(db, 'recommendation_cache', '{}')
    assert reading_list.download_book(entry['id'])['title'] == book['title']
    file_id = upload(client, headers, 'unrelated-filename.epub', epub())
    state = client.get('/api/state').json
    assert state['reading_list'][0]['file_id'] == file_id
    assert state['reading_list_files'] == {file_id: entry['id']}
    assert client.post('/api/reading-list', headers=headers, json={'file_id': file_id}).status_code == 200
    assert len(client.get('/api/state').json['reading_list']) == 1
    assert client.post('/api/reading-list', headers=headers, json={'remove_id': entry['id']}).status_code == 200
    assert client.get('/api/state').json['reading_list'] == []
    assert client.get('/api/state').json['files'][0]['id'] == file_id


def test_saved_catalogue_result_survives_expiry_and_matches_recommendation(client):
    headers = register(client, 'First')
    recommendation = recommend()
    book = {'key': 'work', 'title': recommendation['title'], 'author': recommendation['author'],
            'isbns': [recommendation['isbn']], 'records': [{'work': '/works/OL123W'}],
            'covers': ['https://covers.openlibrary.org/b/id/456-M.jpg?default=false'], 'rating': {}}
    result, = book_catalog.store_results([book])
    source = {'catalogue_id': result['id']}
    assert client.post('/api/reading-list', headers=headers, json=source).status_code == 200
    assert client.post('/api/reading-list', headers=headers, json=source).status_code == 200
    entry, = client.get('/api/state').json['reading_list']
    assert entry['url'] == result['url'] == 'https://openlibrary.org/works/OL123W'
    assert entry['reading_key'] == result['reading_key']
    assert entry['reading_isbn'] == result['reading_isbn'] == recommendation['isbn']
    assert entry['cover_url'] == '/api/downloads/covers/456'
    assert entry['file_id'] is None

    with core.transaction() as db:
        db.execute('UPDATE download_catalogue SET expires=0')
    assert client.post('/api/reading-list', headers=headers, json=source).status_code == 400
    assert reading_list.download_book(entry['id'])['title'] == book['title']
    assert client.post('/api/reading-list', headers=headers,
                       json={'recommendation_url': recommendation['url']}).status_code == 200
    saved, = client.get('/api/state').json['reading_list']
    assert saved['id'] == entry['id']
    register(client, 'Second')
    assert client.get('/api/state').json['reading_list'] == []


def test_read_status_defaults_to_unchecked_and_is_private(client):
    headers = register(client, 'First')
    file_id = upload(client, headers, 'Private.txt', b'Test book')
    client.post('/api/reading-list', headers=headers, json={'file_id': file_id})
    entry, = client.get('/api/state').json['reading_list']
    endpoint = f"/api/reading-list/{entry['id']}/read"
    assert entry['is_read'] is False
    assert client.post(endpoint, headers=headers, json={'is_read': 1}).status_code == 400
    assert client.post(endpoint, headers=headers, json={'is_read': True}).json == {'is_read': True}
    assert client.get('/api/state').json['reading_list'][0]['is_read'] is True
    second = register(client, 'Second')
    assert client.post(endpoint, headers=second, json={'is_read': False}).status_code == 404
    assert client.get('/api/state').json['reading_list'] == []
    session = client.get('/api/session').json
    login = client.post('/api/auth/login', json={'name': 'First', 'password': 'test-password'},
                        headers={'X-CSRF-Token': session['csrf']}).json
    assert client.get('/api/state').json['reading_list'][0]['is_read'] is True
    assert client.post(endpoint, headers={'X-CSRF-Token': login['csrf']}, json={'is_read': False}).status_code == 200
    assert client.get('/api/state').json['reading_list'][0]['is_read'] is False


def test_download_search_finds_local_books_and_reuses_the_existing_file(client, monkeypatch):
    headers = register(client, 'First')
    file_id = upload(client, headers, 'unrelated-name.epub', epub())
    monkeypatch.setattr(book_catalog, 'configured_catalogues', lambda: [])
    monkeypatch.setattr(book_catalog, 'library_rows', lambda *_: ([], False))
    search = '/api/downloads/catalogue/search'
    response = client.post(search, headers=headers, json={'query': '9780306406157'})
    local, = response.json['books']
    assert local['local_only'] and local['file_id'] == file_id
    assert local['title'] == 'Una nuova storia'

    remote = {'title': 'Una nuova storia', 'author': 'Luca Riva', 'authors': ['Luca Riva'],
              'isbns': ['9780306406157'], 'cover': '', 'work': '/works/OL123W'}
    monkeypatch.setattr(book_catalog, 'library_rows', lambda *_: ([remote], False))
    featured, = client.post(search, headers=headers, json={'query': 'Una nuova storia'}).json['books']
    assert featured['file_id'] == file_id
    assert featured['url'] == 'https://openlibrary.org/works/OL123W'
    assert client.post('/api/downloads/catalogue/edition', headers=headers, json={'id': featured['id']}).json == {
        'file_id': file_id, 'options': [],
    }
    other = register(client, 'Second')
    other_book, = client.post(search, headers=other, json={'query': 'Una nuova storia'}).json['books']
    assert 'file_id' not in other_book


def test_library_read_marks_sync_with_da_leggere_without_adding_an_entry(client):
    headers = register(client, 'First')
    file_id = upload(client, headers, 'Private.txt', b'Test book')
    file_mark = f'/api/files/{file_id}/read'
    assert client.post(file_mark, headers=headers, json={'is_read': True}).json == {'is_read': True}
    state = client.get('/api/state').json
    assert state['files'][0]['is_read'] is True
    assert state['reading_list'] == []
    assert client.post('/api/reading-list', headers=headers, json={'file_id': file_id}).status_code == 200
    entry, = client.get('/api/state').json['reading_list']
    assert entry['is_read'] is True
    assert client.post(f"/api/reading-list/{entry['id']}/read", headers=headers,
                       json={'is_read': False}).status_code == 200
    state = client.get('/api/state').json
    assert state['reading_list'][0]['is_read'] is False
    assert state['files'][0]['is_read'] is False
    other = register(client, 'Second')
    assert client.post(file_mark, headers=other, json={'is_read': True}).status_code == 404


def test_saved_covers_survive_search_expiry_and_recover_existing_entries(client, monkeypatch):
    headers = register(client, 'First')
    image = io.BytesIO()
    Image.new('RGB', (40, 60), 'green').save(image, format='PNG')
    catalogue_cover = 'https://covers.example.com/romanzo.png'
    recommended_cover = 'https://covers.example.com/consigliato.png'
    recovered_cover = 'https://covers.openlibrary.org/b/id/789-M.jpg?default=false'
    calls = []

    def fetch(url):
        calls.append(url)
        if url == 'https://openlibrary.org/works/OL99W.json':
            return b'{"covers":[-1,789]}', 'application/json', url
        assert url in (catalogue_cover, recommended_cover, recovered_cover)
        return image.getvalue(), 'image/png', url

    monkeypatch.setattr(recommendations, 'fetch', fetch)
    result, = book_catalog.store_results([{'key': 'retailer', 'title': 'Un romanzo', 'author': 'Luca Riva',
        'isbns': [], 'covers': [catalogue_cover], 'records': [{'url': 'https://example.com/book'}], 'rating': {}}])
    assert client.post('/api/reading-list', headers=headers, json={'catalogue_id': result['id']}).status_code == 200
    recommendation = recommend(cover=recommended_cover)
    assert client.post('/api/reading-list', headers=headers,
                       json={'recommendation_url': recommendation['url']}).status_code == 200
    with core.transaction() as db:
        db.execute('DELETE FROM download_catalogue')
        core.put(db, 'recommendation_cache', '{}')
        db.execute('INSERT INTO reading_list VALUES (?,?,?,?,?,?,?,?)',
                   ('old', 'Vecchio titolo', 'Autrice', '', 'https://openlibrary.org/works/OL99W', None, None, time.time()))
    entries = {entry['title']: entry for entry in client.get('/api/state').json['reading_list']}
    for entry in entries.values():
        response = client.get(f"/api/reading-list/{entry['id']}/cover")
        assert response.mimetype == 'image/webp'
        with Image.open(io.BytesIO(response.data)) as rendered:
            assert rendered.size == (40, 60)
        response.close()
    assert set(calls) == {catalogue_cover, recommended_cover, recovered_cover, 'https://openlibrary.org/works/OL99W.json'}
    assert reading_list.cover_info('old')['cover_sources'] == [recovered_cover]
    register(client, 'Second')
    assert client.get('/api/reading-list/old/cover').status_code == 404


def test_list_is_private_and_account_deletion_clears_it(client):
    first = register(client, 'First')
    file_id = upload(client, first, 'Private.txt', b'Test book')
    assert client.post('/api/reading-list', json={'file_id': file_id}).status_code == 403
    assert client.post('/api/reading-list', headers=first, json={'file_id': file_id}).status_code == 200
    entry = client.get('/api/state').json['reading_list'][0]
    case = client.post('/api/reading-shelves', headers=first,
                       json={'operation': 'create', 'name': 'Private bookcase'})
    assert case.status_code == 200, case.json
    photo = io.BytesIO()
    Image.new('RGB', (32, 32), 'green').save(photo, format='PNG')
    stand = client.post('/api/shelf-objects', headers=first, json={
        'operation': 'add', 'kind': 'photo', 'case_id': case.json['id'],
        'photo': base64.b64encode(photo.getvalue()).decode(),
    })
    assert stand.status_code == 200, stand.json
    photo_url = f"/api/shelf-objects/{stand.json['id']}/photo"
    assert client.get(photo_url).status_code == 200
    second = register(client, 'Second')
    assert client.get('/api/state').json['reading_list'] == []
    assert client.get(photo_url).status_code == 404
    assert client.post('/api/reading-list', headers=second, json={'file_id': file_id}).status_code == 400
    assert client.post('/api/reading-list', headers=second, json={'remove_id': entry['id']}).status_code == 200
    assert client.post('/api/telegram/search', headers=second, json={'reading_list_id': entry['id']}).status_code == 400
    session = client.get('/api/session').json
    response = client.post('/api/auth/login', json={'name': 'First', 'password': 'test-password'},
                           headers={'X-CSRF-Token': session['csrf']})
    assert response.status_code == 200, response.json
    assert client.get('/api/state').json['reading_list'][0]['id'] == entry['id']
    response = client.post('/api/account/delete', json={'current_password': 'test-password'},
                           headers={'X-CSRF-Token': response.json['csrf']})
    assert response.status_code == 200, response.json
    with contextlib.closing(core.connect()) as db:
        assert reading_list.entries(db) == []
        assert db.execute('SELECT count(*) FROM reading_shelves').fetchone()[0] == 0
        assert db.execute('SELECT count(*) FROM reading_shelf_objects').fetchone()[0] == 0


def test_catalog_backfill_and_explicit_download_link(client, tmp_path, monkeypatch):
    headers = register(client, 'First')
    book = recommend()
    client.post('/api/reading-list', headers=headers, json={'recommendation_url': book['url']})
    file_id = upload(client, headers, 'old.epub', epub())
    with core.transaction() as db:
        db.execute('DELETE FROM reading_file_metadata')
    assert client.get('/api/state').json['reading_list'][0]['file_id'] is None
    reading_list.index_existing_epubs()
    entry = client.get('/api/state').json['reading_list'][0]
    assert entry['file_id'] == file_id

    downloads = tmp_path / 'downloads'
    downloads.mkdir()
    monkeypatch.setenv('BOOK_DOWNLOAD_DIR', str(downloads))
    saved = downloads / 'Downloaded.txt'
    saved.write_text('Test download')
    downloaded_id = telegram_books.register_download(saved, entry['id'])
    assert client.get('/api/state').json['reading_list'][0]['file_id'] == downloaded_id


def test_ambiguous_filenames_do_not_link(client):
    headers = register(client, 'First')
    book = recommend()
    client.post('/api/reading-list', headers=headers, json={'recommendation_url': book['url']})
    upload(client, headers, 'Una nuova storia.txt', b'First file')
    upload(client, headers, 'Una nuova storia.pdf', b'%PDF-1.5\nSecond file')
    assert client.get('/api/state').json['reading_list'][0]['file_id'] is None


def test_download_folder_is_used_and_stays_account_specific(client, tmp_path, monkeypatch):
    root = tmp_path / 'downloads'
    root.mkdir()
    monkeypatch.setenv('BOOK_DOWNLOAD_DIR', str(root))
    first = register(client, 'First')
    response = client.post('/api/downloads/folder', headers=first, json={'folder': 'Romanzi/2026'})
    assert response.status_code == 200, response.json
    assert response.json['destination'] == 'Libri/Singoli/Romanzi/2026'
    temp = tmp_path / 'download.txt'
    temp.write_text('Test book')
    saved = telegram_books.install_file(temp, 'Book.txt')
    assert saved == root / 'Romanzi' / '2026' / 'Book.txt'
    file = telegram_books.register_download(saved)
    assert any(row['folder'] == 'Singoli/Romanzi/2026' and row['id'] == file
               for row in client.get('/api/state').json['files'])
    outside = tmp_path / 'outside'
    outside.mkdir()
    (root / 'alias').symlink_to(outside, target_is_directory=True)
    assert client.post('/api/downloads/folder', headers=first, json={'folder': 'alias/unavailable'}).status_code == 400
    assert not list(outside.iterdir())
    second = register(client, 'Second')
    assert client.get('/api/telegram', headers=second).json['folder'] == ''
    assert client.post('/api/downloads/folder', headers=first, json={'folder': ''}).status_code == 409


def test_xml_declarations_are_rejected_independent_of_encoding():
    from app.covers import parse_xml
    for encoding in ('utf-8', 'utf-16'):
        assert parse_xml('<book/>'.encode(encoding)).tag == 'book'
        with pytest.raises(ValueError, match='Dichiarazioni'):
            parse_xml('<!DOCTYPE book><book/>'.encode(encoding))


def test_new_strings_in_every_locale():
    from app.i18n import LANGUAGES, catalog
    strings = ('Da leggere', 'Letto', 'Copertina non disponibile.', 'Caricamento della lista…', 'Salva in Da leggere', 'Rimuovi da Da leggere',
               'Da scaricare o caricare', 'La lista è vuota.', 'Salva un titolo da Libreria, Consigliati o Download.',
               'Salvato in Da leggere.', 'Rimosso da Da leggere.', 'Libro non presente in Da leggere.',
               'Chiudi il libro e torna a Da leggere', 'Cartella dei download', 'Sottocartella',
               'Cartella dei download salvata.', 'Indica una sottocartella valida della cartella download.',
               'Nuova fonte')
    for lang in LANGUAGES:
        for string in strings:
            assert catalog(lang)[string]
            if lang != 'it':
                assert catalog(lang)[string] != string
