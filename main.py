'''
Get all movies and TV shows currently available on Netflix in the Czech Republic from TMDB.
Look for these titles on csfd.cz and save all titles that USER hasn't rated (seen) yet
to a sqlite3 database and a csv file.

To specify:
- user on csfd, either as a command-line argument (`python main.py <user>`, so this
  can run unattended e.g. from cron) or, if omitted, via an interactive prompt.

Requires the environment variable TMDB_API_KEY (a free key from
https://www.themoviedb.org/settings/api). Set it in a .env file in the project
root (see .env.example) instead of exporting it in your shell every time.

Exceptions:
- Movie on csfd must not start with '(' or that movie title will be ignored
'''
import os
import re
import sys
import csv
import logging
import tempfile
import argparse
import unicodedata
from datetime import datetime, timezone
from time import sleep, time
from random import randint

import requests
from curl_cffi import requests as csfd_requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv

import my_database
from my_database import Movies, CsfdFilmCache

# Loads variables from a .env file in the project root into the environment, if one
# exists (see .env.example) - doesn't override a variable already set in the shell.
load_dotenv()

# Anchored to this file's directory rather than a bare relative path, so the log
# always lands next to the script (and stays .gitignored there) no matter what the
# working directory was when it was launched - e.g. from cron, or from an IDE run
# config whose working dir is the parent project folder.
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_PATH = os.path.join(SCRIPT_DIR, 'netflix_csfd_finder.log')

# Configured on the root logger (not this module's own logger) so that every module's
# `logging.getLogger(__name__)` - including my_database.py's - propagates up to these
# same console/file handlers instead of going nowhere.
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(LOG_PATH, encoding='utf-8'),
    ],
)
logger = logging.getLogger(__name__)

TMDB_API_KEY = os.environ.get('TMDB_API_KEY')
TMDB_BASE = 'https://api.themoviedb.org/3'
NETFLIX_PROVIDER_ID = 8

CSFD_BASE = 'https://www.csfd.cz'
CSFD_SEARCH_URL = f'{CSFD_BASE}/hledat/'
CSFD_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                  '(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36'
}

# Per-request ceiling for csfd fetches. curl_cffi has no default timeout, so a
# stalled connection (csfd tarpitting a scraper it doesn't like) would otherwise
# hang for many tens of seconds before curl's own internal limit trips.
CSFD_REQUEST_TIMEOUT = 30
# csfd occasionally drops or stalls a connection mid-run; a couple of backed-off
# retries recover most of those without turning one blip into a lost rating.
CSFD_MAX_ATTEMPTS = 3
CSFD_RETRY_BACKOFF_BASE = 5  # seconds, doubled each subsequent retry (+ jitter)

encoding = 'utf-16'
csv_result = 'movies_not_seen_on_csfd.csv'

# csfd ratings can drift as more people rate a film, so a cached percentage older
# than this is treated as stale and refetched instead of reused.
PERCENTAGE_CACHE_MAX_AGE_DAYS = 180

# Resolved in the `__main__` guard below (from a CLI arg, or an interactive prompt if
# omitted) rather than at import time, so importing this module - e.g. from tests -
# never blocks on stdin. Functions below read this as a module-level global.
user = None


def parse_args():
    parser = argparse.ArgumentParser(
        description="Find Netflix (Czech Republic) titles a given csfd.cz user hasn't rated yet."
    )
    parser.add_argument(
        'user', nargs='?', default=None,
        help='csfd.cz username to compare against. If omitted, you will be prompted for it.',
    )
    return parser.parse_args()


def print_progress(label, current, total):
    """
    Renders a single-line animated percentage progress bar directly to stdout.

    This is intentionally NOT logged: it's a terminal-only "something is happening"
    cue for interactive use, not a record of the run, so it's skipped entirely when
    stdout isn't a real terminal (e.g. redirected to a file, as in the cron example
    in the README) to avoid dumping a stream of overlapping \\r-updated lines there.
    """
    if not sys.stdout.isatty():
        return
    current = min(current, total)
    width = 30
    filled = int(width * current / total)
    bar = '#' * filled + '-' * (width - filled)
    pct = int(100 * current / total)
    sys.stdout.write(f'\r{label}: [{bar}] {pct}% ({current}/{total})')
    sys.stdout.flush()


def finish_progress():
    """Moves the cursor past the current progress line, if one was being drawn."""
    if sys.stdout.isatty():
        sys.stdout.write('\n')
        sys.stdout.flush()


def get_csfd_soup(url, params=None):
    # Sleep some time before making a request to not overwhelm the website
    sleep(randint(1, 3))
    # csfd.cz sits behind a bot-detection challenge that plain `requests` can't pass,
    # so impersonate a real browser's TLS fingerprint. Network errors, timeouts and
    # HTTP error statuses are retried a bounded number of times with exponential
    # backoff; the last error is re-raised once the attempts run out, so callers
    # (get_rating_for_title, get_csfd_movies) still see a normal exception.
    last_error = None
    for attempt in range(1, CSFD_MAX_ATTEMPTS + 1):
        try:
            response = csfd_requests.get(
                url, headers=CSFD_HEADERS, params=params,
                impersonate='chrome', timeout=CSFD_REQUEST_TIMEOUT,
            )
            response.raise_for_status()
            return BeautifulSoup(response.text, 'html.parser')
        except csfd_requests.RequestsError as e:
            last_error = e
            if attempt == CSFD_MAX_ATTEMPTS:
                break
            backoff = CSFD_RETRY_BACKOFF_BASE * 2 ** (attempt - 1) + randint(0, 3)
            logger.warning(
                f'-- csfd request for {url} failed '
                f'(attempt {attempt}/{CSFD_MAX_ATTEMPTS}): {e}; retrying in {backoff}s.'
            )
            sleep(backoff)
    raise last_error


def get_tmdb_page(endpoint, page):
    response = requests.get(f'{TMDB_BASE}/discover/{endpoint}', params={
        'api_key': TMDB_API_KEY,
        'watch_region': 'CZ',
        'with_watch_providers': NETFLIX_PROVIDER_ID,
        'with_watch_monetization_types': 'flatrate',
        # English titles by default (see get_netflix_titles); Czech/Slovak-origin
        # films are kept under their original title, taken from original_title.
        'language': 'en-US',
        'sort_by': 'popularity.desc',
        'page': page,
    })
    response.raise_for_status()
    return response.json()


# Covers Basic Latin, Latin-1 Supplement, and Latin Extended-A/B - i.e. essentially
# every accented Latin character used by European languages (Czech, French, German,
# Vietnamese, etc.), while excluding non-Latin scripts (Tamil, Devanagari, Cyrillic,
# CJK, Arabic, ...).
_LATIN_SCRIPT_MAX_CODEPOINT = 0x2AF


def is_latin_script(text):
    """
    Returns True if `text` contains only Latin-script letters (punctuation, digits,
    and spaces don't count either way). Used to detect when TMDB's discover API
    fell back to a title in its non-Latin original script because no English
    translation was registered for it - unreadable for most users even though it's
    technically the correct title.
    """
    return all(not ch.isalpha() or ord(ch) <= _LATIN_SCRIPT_MAX_CODEPOINT for ch in text)


def _clean_title(title):
    """
    Strips parenthetical qualifiers ("(Extended Cut)", "(US Version)") and
    normalizes curly apostrophes to straight ones. Applied to every title before
    it's stored or matched, whichever TMDB field it came from.
    """
    return re.sub(r'[\(].*?[\)]', '', title).replace('’', "'").strip()


def _match_key(title):
    """
    Reduces a title to a comparison key for seen/unseen matching: case-folded,
    curly apostrophes and the various dashes unified, accents stripped, and
    internal whitespace collapsed. Accent-insensitivity is safe here because a
    matching release year is always required alongside the key match, so
    "Leon"/"Léon"-type collisions can't by themselves mark a film as seen.
    Returns '' for a title with no usable characters.
    """
    if not title:
        return ''
    text = (title.replace('’', "'")
                 .replace('‑', '-').replace('–', '-').replace('—', '-'))
    text = unicodedata.normalize('NFKD', text)
    text = ''.join(ch for ch in text if not unicodedata.combining(ch))
    return re.sub(r'\s+', ' ', text).strip().casefold()


def _match_keys(*titles):
    """Builds the set of non-empty _match_key()s for the given titles, ignoring
    Nones - used to collect every title variant a film might be matched on."""
    keys = set()
    for title in titles:
        if not title:
            continue
        key = _match_key(title)
        if key:
            keys.add(key)
    return keys


# TMDB original_language (ISO 639-1) values whose films are kept under their own
# original title rather than the English one: a Czech/Slovak audience knows these
# in their original form, and it's also how csfd lists them.
ORIGINAL_TITLE_LANGUAGES = {'cs', 'sk'}


def get_english_title(category, tmdb_id):
    """
    Fetches the English title/name for a single TMDB movie/tv id - used as a
    fallback only for the (usually rare) titles whose discover result came back in
    a non-Latin original script with no English title in the payload. Returns None
    if TMDB doesn't have an English title for it either.
    """
    response = requests.get(f'{TMDB_BASE}/{category}/{tmdb_id}', params={
        'api_key': TMDB_API_KEY,
        'language': 'en-US',
    })
    response.raise_for_status()
    data = response.json()
    return data.get('title') or data.get('name')


def get_netflix_titles():
    """
    Get all movies and TV shows currently available on Netflix in the Czech Republic from TMDB.
    Returns a list of (display_title, year, category, match_keys) tuples:
    - display_title: what gets stored/shown - English, or the original title for
      Czech/Slovak films (see ORIGINAL_TITLE_LANGUAGES)
    - year, category (movie/tv)
    - match_keys: a frozenset of normalized keys (English + original title
      variants) used only for seen/unseen matching against csfd, never stored
    """
    if not TMDB_API_KEY:
        raise Exception(
            'Environment variable TMDB_API_KEY is not set. '
            'Get a free key at https://www.themoviedb.org/settings/api'
        )

    titles = []
    for category, date_field in (('movie', 'release_date'), ('tv', 'first_air_date')):
        logger.info(f'Fetching Netflix Czech Republic {category} titles from TMDB...')
        page = 1
        total_pages = 1
        while page <= total_pages:
            data = get_tmdb_page(category, page)
            total_pages = data.get('total_pages', 1)
            for result in data.get('results', []):
                english_title = result.get('title') or result.get('name')
                original_title = result.get('original_title') or result.get('original_name')

                if result.get('original_language') in ORIGINAL_TITLE_LANGUAGES:
                    # Czech/Slovak film - show its original title as-is.
                    raw_title = original_title or english_title
                else:
                    # Everyone else: the en-US discover call already gives the
                    # English title (or the original one if none is registered).
                    raw_title = english_title or original_title
                if not raw_title:
                    continue

                title = _clean_title(raw_title)
                # Match on every variant regardless of which one is displayed, so
                # a match can land on whichever title csfd happens to list.
                key_sources = [english_title, original_title,
                               _clean_title(english_title or ''), _clean_title(original_title or '')]
                if not is_latin_script(title):
                    # A non-Latin original title with no English one in the
                    # payload - ask the detail endpoint explicitly rather than
                    # surface an unreadable script to the user.
                    fallback = get_english_title(category, result['id'])
                    if fallback and is_latin_script(fallback):
                        title = _clean_title(fallback)
                        key_sources.append(fallback)
                    else:
                        logger.warning(
                            f'-- No Latin-script title for TMDB {category} '
                            f'{result.get("id")}; storing "{title}" as-is.'
                        )
                date = result.get(date_field) or ''
                year = date[:4]
                titles.append((title, year, category, frozenset(_match_keys(*key_sources))))
            print_progress(f'Fetching {category} titles', page, total_pages)
            page += 1
            sleep(0.2)
        finish_progress()
    return titles


def get_user_url(soup):
    """
    Searches user on website.
    Returns error if the first search result on website is not equal to searched user, otherwise returns user rating url.
    """
    try:
        first_user = soup.find('a', class_='user-title-name').string
    except AttributeError:
        raise Exception(f"User {user} doesn't exist!")
    if user.lower() != first_user.lower():
        raise Exception(f"Could not find user {user}. The first user found is {first_user}")
    profile_href = soup.find('a', class_='user-title-name')['href']
    return CSFD_BASE + profile_href.replace('prehled', 'hodnoceni')


def get_titles(soup):
    """
    Returns the list of title variants for a film: the primary title from the
    header <h1> first, then every alternate translation in the film-names list.
    csfd only repeats the primary (usually Czech) title into film-names for some
    films, so the <h1> is read explicitly - otherwise the Czech distribution
    title, which is exactly what TMDB gives us to match on, can be missing.
    Strips the site's "more/less" toggle labels mixed into the same list items.
    """
    header = soup.select_one('.film-header-name')
    if header is None:
        return []
    titles = []
    h1 = header.find('h1')
    if h1 is not None:
        primary = h1.get_text(' ', strip=True)
        if primary:
            titles.append(primary)
    name_list = header.select_one('ul.film-names')
    if name_list is not None:
        for li in name_list.find_all('li', recursive=False):
            for toggle in li.find_all('span', class_=['more-name-link', 'less-name-link']):
                toggle.decompose()
            title = li.get_text().strip()
            if title and not title.startswith('(') and title not in titles:
                titles.append(title)
    return titles


def get_year(soup):
    """
    Extracts the release year from the origin div, which mixes the year in with
    nested markup (country, runtime), so a plain string search is used instead of
    relying on it being the only child of a tag.
    """
    origin = soup.select_one('div.origin')
    if origin is None:
        return ''
    match = re.search(r'(19|20)\d{2}', origin.get_text(' ', strip=True))
    return match.group(0) if match else ''


def get_genre(soup):
    genres = soup.select_one('div.genres')
    return genres.get_text(' ', strip=True) if genres else ''


def get_ratings_page_count(soup):
    """
    Reads the total number of ratings pages from the pagination block on a csfd
    ratings-list page (e.g. "1 2 3 ... 17"), so progress can be reported as a
    percentage instead of just a running count. Returns 1 if there's no pagination
    block at all (i.e. everything fits on a single page).
    """
    pagination = soup.select_one('div.pagination')
    if pagination is None:
        return 1
    page_numbers = [
        int(text) for el in pagination.find_all(['a', 'span'])
        if (text := el.get_text(strip=True)).isdigit()
    ]
    return max(page_numbers) if page_numbers else 1


def get_csfd_movies():
    """
    Gets all the user's rated urls and then
    returns list of tuples with information about every rated movie in format [([titles], year, genre), ...]
    Titles is list of strings - we keep all the movie translations.
    """
    logger.info(f'Getting all rated movies from user {user}...')
    movie_urls = []
    csfd_movies = []
    url_rating = get_user_url(get_csfd_soup(CSFD_SEARCH_URL, {'q': user}))
    soup_rating = get_csfd_soup(url_rating)
    total_rating_pages = get_ratings_page_count(soup_rating)

    # Takes url's of every rated movie across all pages.
    rating_page = 1
    while True:
        for elem in soup_rating.find_all('h3', class_='film-title-inline'):
            movie_urls.append(elem.a['href'])
        print_progress('Fetching csfd ratings pages', rating_page, total_rating_pages)
        next_page = soup_rating.find('a', class_='page-next')
        if next_page is None:
            break
        soup_rating = get_csfd_soup(CSFD_BASE + next_page['href'])
        rating_page += 1
    finish_progress()

    # Takes required information from every rated movie. A film's titles/year/genre
    # never change once scraped, so a permanent cache (keyed by href, shared across
    # all users and all runs) is checked first - only a href never seen before
    # triggers an actual detail-page fetch.
    film_cache = CsfdFilmCache()
    cache_hits = 0
    cache_misses = 0
    for movie in movie_urls:
        if len(csfd_movies) % 10 == 0 and len(csfd_movies) != 0:
            logger.info(f'-- {len(csfd_movies)}th movie is being processed...')
        cached = film_cache.get(movie)
        if cached is not None:
            csfd_movies.append(cached)
            cache_hits += 1
            continue
        try:
            soup = get_csfd_soup(CSFD_BASE + movie)
            titles, year, genre = get_titles(soup), get_year(soup), get_genre(soup)
        except Exception as e:
            logger.warning(f'-- Skipping {movie}, failed to fetch/parse: {e}')
            continue
        # The fetch/parse succeeded, so this movie counts regardless of whether
        # caching it below works - a cache-write failure (e.g. movies.db becoming
        # unwritable mid-run) shouldn't drop an otherwise-good result.
        csfd_movies.append((titles, year, genre))
        cache_misses += 1
        try:
            film_cache.set(movie, titles, year, genre)
        except Exception as e:
            logger.warning(f'-- Fetched {movie} OK but could not cache it, will re-fetch next time: {e}')
    logger.info(
        f'Film metadata cache: {cache_hits} served from cache, '
        f'{cache_misses} freshly fetched.'
    )
    return csfd_movies


def find_csfd_film_url(title, year):
    """
    Searches csfd.cz for a title and returns the URL of the best-guess matching film page,
    or None if no result could be found. Prefers a result whose year matches the given
    year; otherwise falls back to the first film-category result on the search page.
    """
    soup = get_csfd_soup(CSFD_SEARCH_URL, {'q': title})
    candidates = soup.select('h3.film-title-nooverflow')
    first_href = None
    year_match_href = None
    for h3 in candidates:
        link = h3.find('a', class_='film-title-name')
        if link is None or not link.get('href'):
            continue
        if first_href is None:
            first_href = link['href']
        if year and year in h3.get_text(' ', strip=True):
            year_match_href = link['href']
            break
    href = year_match_href or first_href
    return CSFD_BASE + href if href else None


def get_csfd_rating_percentage(url):
    """
    Fetches a csfd film page and returns its aggregate audience-rating percentage as an
    int, or None if no numeric rating could be parsed (e.g. an unrated/obscure title).
    """
    soup = get_csfd_soup(url)
    rating_div = soup.find('div', class_='film-rating-average')
    if rating_div is None:
        return None
    digits = re.sub(r'\D', '', rating_div.get_text())
    return int(digits) if digits else None


def get_rating_for_title(title, year):
    """
    Looks up a title on csfd.cz and returns its aggregate rating percentage, or None if
    no confident match was found, the match has no numeric rating, or the lookup failed.
    """
    try:
        film_url = find_csfd_film_url(title, year)
        if film_url is None:
            logger.warning(f'-- No csfd match found for "{title}", skipping rating.')
            return None
        return get_csfd_rating_percentage(film_url)
    except Exception as e:
        logger.warning(f'-- Could not fetch csfd rating for "{title}": {e}')
        return None


def years_match(year_a, year_b):
    """
    Returns whether two year strings should be treated as matching, for both
    seen/unseen comparison and cache lookups: true if either one is missing/empty
    (TMDB's year can be '' for unannounced titles, and csfd's year-extraction can
    also come back empty for a handful of malformed pages), or if both are present
    and equal. Only an actual year-vs-year disagreement counts as a mismatch.
    """
    if not year_a or not year_b:
        return True
    return year_a == year_b


def _cached_percentage_age_days(percentage_checked_at):
    """
    Returns how many days old an ISO-8601 percentage_checked_at timestamp is, or
    None if it can't be parsed (e.g. malformed/missing data in an older row).
    """
    try:
        checked_at = datetime.fromisoformat(percentage_checked_at)
    except (TypeError, ValueError):
        return None
    if checked_at.tzinfo is None:
        checked_at = checked_at.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - checked_at).total_seconds() / 86400


def _csfd_key_sets(csfd_movies):
    """
    Turns [([titles], year, genre), ...] into [(frozenset_of_match_keys, year), ...]
    once up front, so the per-title matching loop is a set intersection rather
    than a nested substring scan.
    """
    return [(frozenset(_match_keys(*titles)), year) for titles, year, _ in csfd_movies]


def _is_seen(match_keys, year, csfd_key_sets):
    """A Netflix title counts as seen if any of its normalized title keys matches
    one of a rated csfd film's keys and their years don't disagree."""
    if not match_keys:
        return False
    return any(
        match_keys & csfd_keys and years_match(year, csfd_year)
        for csfd_keys, csfd_year in csfd_key_sets
    )


def compare_and_save(netflix_titles, csfd_movies):
    """
    Compares each Netflix title against the user's rated csfd films and writes the
    outcome to the database and (for not-yet-seen titles) the csv.

    `netflix_titles` items are (display_title, year, category[, match_keys]) as
    produced by get_netflix_titles; the match_keys frozenset is optional and a
    key derived from display_title is used in its absence.
    """
    result = []
    movie = Movies()
    csfd_key_sets = _csfd_key_sets(csfd_movies)
    rows = []

    # Persist rows to the db in batches during the loop rather than in one write at
    # the very end, so a crash partway through keeps the csfd lookups already done
    # (the slow part) instead of discarding them. Only possible with the
    # UNIQUE(title, year) index that lets a batch be upserted without wiping the
    # table; a legacy duplicate-rows database can't do this and falls back to a
    # single end-of-run create_and_insert_table call (see my_database).
    incremental = movie.can_upsert
    pending = []

    # The csv is written to a sibling temp file and swapped into place with
    # os.replace only once the comparison loop has finished, so an interrupted run
    # leaves the previous run's csv fully intact instead of a truncated/empty one.
    tmp = tempfile.NamedTemporaryFile(
        mode='w', dir=os.path.dirname(csv_result) or '.',
        prefix='.movies_not_seen_', suffix='.csv.tmp',
        delete=False, encoding=encoding, newline='',
    )
    tmp_path = tmp.name
    try:
        csv_writer = csv.writer(tmp)
        csv_writer.writerow(['title', 'year', 'category', 'percentage'])
        for item in netflix_titles:
            # Flush at the start of an iteration once a full batch has accumulated,
            # so `pending` never holds more than DB_COMMIT_BATCH_SIZE rows.
            if incremental and len(pending) >= my_database.DB_COMMIT_BATCH_SIZE:
                movie.upsert_rows(pending)
                pending = []

            title, year, category = item[:3]
            match_keys = item[3] if len(item) > 3 else _match_keys(title)

            if _is_seen(match_keys, year, csfd_key_sets):
                row = (title, year, category, True, None, None)
                rows.append(row)
                pending.append(row)
                continue

            percentage = None
            percentage_checked_at = None
            cached = movie.get_cached_percentage(title, year)
            if cached is not None:
                cached_percentage, cached_checked_at = cached
                age_days = _cached_percentage_age_days(cached_checked_at)
                if age_days is not None and age_days < PERCENTAGE_CACHE_MAX_AGE_DAYS:
                    logger.info(
                        f'-- Using cached csfd rating for "{title}" '
                        f'({age_days:.1f} days old).'
                    )
                    percentage = cached_percentage
                    percentage_checked_at = cached_checked_at

            if percentage_checked_at is None:
                logger.info(f'Looking up csfd rating for "{title}"...')
                percentage = get_rating_for_title(title, year)
                if percentage is not None:
                    percentage_checked_at = datetime.now(timezone.utc).isoformat()

            row = (title, year, category, False, percentage, percentage_checked_at)
            rows.append(row)
            pending.append(row)
            result.append(title)
            csv_writer.writerow([title, year, category, percentage])

        tmp.close()
    except BaseException:
        # Any exit before the loop completes (network error, Ctrl-C, ...) must not
        # disturb the existing csv - drop the half-written temp file, best-effort,
        # and let whatever db batches already committed stand.
        tmp.close()
        try:
            os.remove(tmp_path)
        except OSError:
            pass
        raise

    if incremental:
        # Flush the final partial batch, then prune once against the full run.
        if pending:
            movie.upsert_rows(pending)
        movie.prune_missing_titles(rows)
    else:
        movie.create_and_insert_table(rows)

    os.replace(tmp_path, csv_result)
    return result


# Main
if __name__ == '__main__':
    user = parse_args().user or input('What csfd user would you like to compare movies to? ')
    start = time()
    try:
        netflix_tuples = get_netflix_titles()
        csfd_tuples = get_csfd_movies()
        res = compare_and_save(netflix_tuples, csfd_tuples)
        logger.info('\nNot seen movies:\n-- ' + '\n-- '.join(res))
        logger.info(f'\nTotal time of run is: {(time() - start)/60} minutes.')
    except Exception:
        logger.error('Fatal error, run did not complete successfully.', exc_info=True)
        raise
