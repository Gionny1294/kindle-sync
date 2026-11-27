"""Search the enabled book catalogues and retrieve details for download cards."""
from concurrent.futures import ThreadPoolExecutor
import contextlib
import hashlib
import http.client
import json
import re
import secrets
import time
import unicodedata
from urllib.parse import urlencode

from app import core as c, reading_list, recommendations


CATALOGUE = 'https://openlibrary.org'
SEARCH_FIELDS = 'key,title,author_name,isbn,cover_i,ratings_average,ratings_count'
PAGE_SIZE = 24
RETAILERS = {
    'IBS': 'https://www.ibs.it/search/',
    'Feltrinelli': 'https://www.lafeltrinelli.it/search/',
    'Mondadori': 'https://www.mondadoristore.it/search',
    'Goodreads': 'https://www.goodreads.com/search',
}
RATING_KEYS = ('rating_value','rating_count','rating_source','rating_url')


def fetch_json(url):
    try:
        data, _, _ = recommendations.fetch(url)
        result = json.loads(data)
        return result if isinstance(result, dict) else {}
    except (OSError, http.client.HTTPException, ValueError):
        return {}


def identity(title, author=''):
    return recommendations.book_identity({'title': title, 'author': author})


def search_isbn(query):
    if re.fullmatch(r'(?i)(?:ISBN(?:-1[03])?[: ]*)?[\dX -]{10,24}', query):
        return reading_list.isbn_key(re.sub(r'(?i)^ISBN(?:-1[03])?[: ]*', '', query))
    return ''


def preferred_edition(query, options):
    target = identity(query)
    for option in options:
        title, author = option.get('title', ''), option.get('author', '')
        if title and target in (identity(title), identity(title, author), identity(author, title)):
            return option
    return options[0]


def suggested_book(query, title, author, isbn):
    query_key = identity(query)
    for book in recommendations.snapshot()['books']:
        if isbn and reading_list.isbn_key(book.get('isbn')) == isbn:
            return book
        if isbn:
            continue
        if title and author and identity(book['title'], book.get('author', '')) == identity(title, author):
            return book
        if not title and query_key in (identity(book['title'], book.get('author', '')),
                                      identity(book.get('author', ''), book['title'])):
            return book
    return {}


def catalogue_book(query, title, author, isbn, language):
    params = {'fields': SEARCH_FIELDS, 'limit': 8, 'lang': language}
    if isbn:
        params['isbn'] = isbn
    elif title:
        params['title'] = title
        if author:
            params['author'] = author
    else:
        params['q'] = query

    rows = fetch_json(CATALOGUE + '/search.json?' + urlencode(params)).get('docs', [])
    if not isinstance(rows, list):
        return {}
    target = identity(query)
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get('title'), str):
            continue
        names = row.get('author_name') or []
        if not isinstance(names, list) or not all(isinstance(name, str) for name in names):
            continue
        row_author = ', '.join(names)
        if isbn:
            identifiers = row.get('isbn') or []
            matches = isinstance(identifiers, list) and isbn in identifiers
        elif title:
            matches = identity(row['title']) == identity(title) and (
                not author or identity(author) in (identity(row_author), *(identity(name) for name in names)))
        else:
            matches = target in (identity(row['title']), identity(row['title'], row_author),
                                 identity(row_author, row['title']))
        if matches:
            return {**row, 'author': row_author}
    return {}


def work_synopsis(work_key):
    work = fetch_json(CATALOGUE + work_key + '.json') if work_key else {}
    description = work.get('description')
    if isinstance(description, dict):
        description = description.get('value')
    return recommendations.synopsis_text(description)


def preview(query, language, option):
    title, author = option.get('title', ''), option.get('author', '')
    isbn = search_isbn(query)
    known = suggested_book(query, title, author, isbn)
    row = catalogue_book(query, known.get('title') or title, known.get('author') or author, isbn, language)
    work_key = row.get('key', '')
    work_key = work_key if isinstance(work_key, str) and re.fullmatch(r'/works/OL\d+W', work_key) else ''
    synopsis = work_synopsis(work_key)
    if not synopsis and known:
        try:
            synopsis = recommendations.book_synopsis(known)['synopsis']
        except ValueError:
            pass

    cover = known.get('cover_url')
    cover_id = row.get('cover_i')
    if not cover and type(cover_id) is int and 0 < cover_id <= 999999999:
        cover = f'/api/downloads/covers/{cover_id}'
    rating = recommendations.rating_fields(row.get('ratings_average'), row.get('ratings_count'))
    if rating:
        rating.update(rating_source='Open Library', rating_url=CATALOGUE+work_key if work_key else '')
    elif known.get('rating_value'):
        rating = {key: known.get(key) for key in ('rating_value','rating_count','rating_source','rating_url')}
    return {'title': known.get('title') or row.get('title') or natural_title(title or query),
            'author': known.get('author') or row.get('author') or natural_author(author),
            'cover_url': cover, 'synopsis': synopsis, **rating}


def natural_title(value):
    value = ' '.join(value.split())
    return value.capitalize() if value.isupper() else value


def natural_author(value):
    value = ' '.join(value.split())
    return value.title() if value.isupper() else value


def words(value):
    folded = unicodedata.normalize('NFKD', value.casefold())
    return re.findall(r'[^\W_]+', ''.join(ch for ch in folded if not unicodedata.combining(ch)))


def exact_author(query, authors):
    target = sorted(words(query))
    return bool(target) and any(sorted(words(author)) == target for author in authors)


def author_contains(query, authors):
    target = set(words(query))
    return bool(target) and any(target.issubset(words(author)) for author in authors)


def search_matches(query, book, isbn, author_search):
    if isbn:
        return isbn in book.get('isbns', [])
    authors = book.get('authors') or [book.get('author', '')]
    if author_search:
        return author_contains(query, authors)
    target = set(words(query))
    return bool(target) and target.issubset(words(book['title'] + ' ' + book.get('author', '')))


def configured_catalogues():
    with c.transaction() as db:
        recommendations.initialize(db)
        sources = recommendations.read_json(db, 'recommendation_sources', [])
        cache = recommendations.read_json(db, 'recommendation_cache', {})
    return [(source, cache.get(source['id'], {}).get('books', []))
            for source in sources if source['enabled']]


def library_rows(query, language, page, isbn, author_search):
    fields = SEARCH_FIELDS if isbn else SEARCH_FIELDS.replace(',isbn', '')
    if not author_search:fields += ',editions,editions.title,editions.cover_i'
    params = {'fields': fields, 'limit': PAGE_SIZE, 'lang': language, 'page': page}
    if isbn:
        params['isbn'] = isbn
    elif author_search:
        params['author'] = query
    else:
        params['q'] = query
    payload = fetch_json(CATALOGUE + '/search.json?' + urlencode(params))
    rows = payload.get('docs', [])
    result = []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        key, title, authors = row.get('key'), row.get('title'), row.get('author_name') or []
        if (not isinstance(key, str) or not re.fullmatch(r'/works/OL\d+W', key)
                or not isinstance(title, str) or not title.strip() or not isinstance(authors, list)):
            continue
        authors = [natural_author(name)[:250] for name in authors if isinstance(name, str)]
        cover_id = row.get('cover_i')
        editions = row.get('editions')
        if isinstance(editions, dict) and isinstance(editions.get('docs'), list) and editions['docs']:
            edition = editions['docs'][0]
            if isinstance(edition, dict) and isinstance(edition.get('title'), str) and edition['title'].strip():
                target = set(words(query))
                if isbn or (not target.issubset(words(title+' '+', '.join(authors))) and
                            target.issubset(words(edition['title']+' '+', '.join(authors)))):
                    title = edition['title']
                    cover_id = edition.get('cover_i') or cover_id
        cover = (f'https://covers.openlibrary.org/b/id/{cover_id}-M.jpg?default=false'
                 if type(cover_id) is int and 0 < cover_id <= 999999999 else '')
        rating = recommendations.rating_fields(row.get('ratings_average'), row.get('ratings_count'), CATALOGUE + key)
        if rating:
            rating['rating_source'] = 'Open Library'
        identifiers = row.get('isbn', []) if isbn else []
        result.append(dict(title=natural_title(title)[:350], author=', '.join(authors)[:250], authors=authors,
                           isbns=identifiers if isinstance(identifiers, list) else [],
                           cover=cover, work=key, **rating))
    total = payload.get('numFound', 0)
    return result, type(total) is int and total > PAGE_SIZE * page


def retailer_record(book):
    isbn = reading_list.isbn_key(book.get('isbn'))
    author = natural_author(book.get('author') or '')
    return {**book, 'title': natural_title(book['title']), 'author': author,
            'authors': [name.strip() for name in author.split(',') if name.strip()],
            'isbns': [isbn] if isbn else []}


def retailer_rows(provider, query, page):
    query_field = 'q' if provider == 'Goodreads' else 'query'
    params = {query_field: query, 'page': page}
    if provider == 'Goodreads':
        params['search_type'] = 'books'
    url = RETAILERS[provider] + '?' + urlencode(params)
    try:
        content, _, base = recommendations.fetch(url)
        books = recommendations.parse_books(content, base, 40, enrich=False)
    except (OSError, http.client.HTTPException, ValueError):
        return [], False
    if provider == 'Mondadori':
        more = f'aria-label="Vai alla pagina {page + 1}"'.encode() in content
    elif provider == 'Goodreads':
        more = b'class="next_page"' in content
    else:
        more = re.search(rb'<link\b[^>]+rel="next"', content) is not None
    return [retailer_record(book) for book in books], bool(books) and more


def custom_rows(source, cached):
    if cached:
        return [retailer_record(book) for book in cached]
    try:
        content, _, base = recommendations.fetch(source['url'])
        return [retailer_record(book) for book in recommendations.parse_books(content, base, 100, enrich=False)]
    except (OSError, http.client.HTTPException, ValueError):
        return []


def result_key(book):
    first_author = book['authors'][0] if book.get('authors') else book.get('author', '')
    first_author = ' '.join(sorted(words(first_author)))
    return hashlib.sha256(identity(book['title'], first_author).encode()).hexdigest()


def merge_results(rows):
    merged = {}
    by_isbn = {}
    for row in rows:
        fallback = result_key(row)
        key = next((by_isbn[isbn] for isbn in row.get('isbns', []) if isbn in by_isbn), fallback)
        book = merged.setdefault(key, dict(key=key, title=row['title'], author=row['author'], covers=[],
                                           records=[], isbns=[], rating={}))
        for isbn in row.get('isbns', []):
            if not isinstance(isbn, str) or not isbn:
                continue
            by_isbn[isbn] = key
            if isbn not in book['isbns']:
                book['isbns'].append(isbn)
        if not book['author']:
            book['author'] = row['author']
        cover = row.get('cover')
        if cover and cover not in book['covers']:
            book['covers'].append(cover)
        source_key = row.get('work') or row.get('url')
        if source_key and all(record.get('work') != source_key and record.get('url') != source_key for record in book['records']):
            book['records'].append({field: row[field] for field in ('title','author','url','isbn','work') if field in row})
        if row.get('rating_value') and (not book['rating'] or (row.get('rating_count') or 0) > (book['rating'].get('rating_count') or 0)):
            book['rating'] = {field: row.get(field) for field in RATING_KEYS}
    return list(merged.values())


def search(query, language, page, author_search=False):
    isbn = search_isbn(query)
    configured = configured_catalogues()
    providers = dict.fromkeys(recommendations.source_name(source['url']) for source, _ in configured)
    is_first = page == 1
    with ThreadPoolExecutor(max_workers=6) as pool:
        library_job = pool.submit(library_rows, query, language, page, isbn, author_search)
        retailer_jobs = [pool.submit(retailer_rows, provider, query, page)
                         for provider in RETAILERS if provider in providers]
        custom_jobs = [pool.submit(custom_rows, source, cached) for source, cached in configured
                       if is_first and recommendations.source_name(source['url']) not in RETAILERS]
        library, library_more = library_job.result()
        retailers = [job.result() for job in retailer_jobs]
        rows = [book for books, _ in retailers for book in books]
        rows.extend(book for job in custom_jobs for book in job.result())
        if is_first:
            rows.extend(retailer_record(book) for source, cached in configured
                        if recommendations.source_name(source['url']) in RETAILERS for book in cached)
        if is_first and not isbn and not author_search:
            author_search = any(exact_author(query, book['authors']) for book in rows + library)
            if author_search:
                strict_library, has_author_pages = library_rows(query, language, page, '', True)
                if strict_library:
                    library, library_more = strict_library, has_author_pages
    matches = merge_results(book for book in rows + library if search_matches(query, book, isbn, author_search))
    target = identity(query)
    if not author_search and not isbn:
        matches.sort(key=lambda book: target not in (identity(book['title']), identity(book['title'], book['author']),
                                                      identity(book['author'], book['title'])))
    return {'books': store_results(matches), 'more': library_more or any(more for _, more in retailers),
            'author_search': author_search}


def catalogue_table(db):
    db.execute('CREATE TABLE IF NOT EXISTS download_catalogue (id TEXT PRIMARY KEY, data TEXT NOT NULL, expires REAL NOT NULL)')


def store_results(books):
    if not books:
        return []
    expiry = time.time() + 2 * 3600
    results = []
    with c.transaction() as db:
        catalogue_table(db)
        db.execute('DELETE FROM download_catalogue WHERE expires < ?', (time.time(),))
        for book in books:
            token = secrets.token_hex(16)
            db.execute('INSERT INTO download_catalogue VALUES (?, ?, ?)', (token, json.dumps(book), expiry))
            results.append({'id':token, 'key':book['key'], 'title':book['title'], 'author':book['author'],
                            'cover_url':f'/api/downloads/catalogue/{token}/cover' if book['covers'] else None,
                            **book['rating']})
    return results


def get_book(token):
    if not isinstance(token, str) or not re.fullmatch(r'[a-f0-9]{32}', token):
        return None
    with contextlib.closing(c.connect()) as db:
        catalogue_table(db)
        row = db.execute('SELECT data FROM download_catalogue WHERE id=? AND expires>?', (token, time.time())).fetchone()
    return json.loads(row['data']) if row else None


def book_cover(book):
    for url in book['covers'][:3]:
        path = recommendations.cached_cover(url, 'download-covers')
        if path:
            return path
    return None


def book_details(book):
    rating = book['rating']
    providers = set()
    for source in book['records']:
        provider = 'Open Library' if source.get('work') else recommendations.source_name(source['url'])
        if provider in providers:
            continue
        providers.add(provider)
        if source.get('work'):
            synopsis = work_synopsis(source['work'])
            if synopsis:
                return {'synopsis': synopsis, **rating}
            continue
        try:
            content, _, base = recommendations.fetch(source['url'])
            synopsis, _ = recommendations.detail_book_text(content, base, source)
            if not rating:
                fields = recommendations.detail_metadata(content, base, source)
                rating = {field: fields.get(field) for field in RATING_KEYS} if fields.get('rating_value') else {}
            if synopsis:
                return {'synopsis': synopsis, **rating}
        except (OSError, http.client.HTTPException, ValueError):
            continue
    return {'synopsis': '', **rating}


def match_editions(book, editions):
    named = [edition for edition in editions if edition.get('title')]
    if not named:
        return editions
    title = identity(book['title'])
    author = book.get('author', '')
    return [edition for edition in named if identity(edition['title']) == title and (
        not author or not edition.get('author') or
        author_contains(edition['author'], [author]) or author_contains(author, [edition['author']]))]
