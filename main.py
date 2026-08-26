'''
Get all movies and TV shows currently available on Netflix in the Czech Republic from TMDB.
Look for these titles on csfd.cz and save all titles that USER hasn't rated (seen) yet
to a sqlite3 database and a csv file.

To specify:
- user on csfd

Requires the environment variable TMDB_API_KEY (a free key from
https://www.themoviedb.org/settings/api).

Exceptions:
- Movie on csfd must not start with '(' or that movie title will be ignored
'''
import os
import re
import csv
import logging
from datetime import datetime, timezone
from time import sleep, time
from random import randint

import requests
from curl_cffi import requests as csfd_requests
from bs4 import BeautifulSoup

from my_database import Movies

# Configured on the root logger (not this module's own logger) so that every module's
# `logging.getLogger(__name__)` - including my_database.py's - propagates up to these
# same console/file handlers instead of going nowhere.
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler('netflix_csfd_finder.log', encoding='utf-8'),
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

encoding = 'utf-16'
csv_result = 'movies_not_seen_on_csfd.csv'

# csfd ratings can drift as more people rate a film, so a cached percentage older
# than this is treated as stale and refetched instead of reused.
PERCENTAGE_CACHE_MAX_AGE_DAYS = 180

user = input('What csfd user would you like to compare movies to? ')


def get_csfd_soup(url, params=None):
    # Sleep some time before making a request to not overwhelm the website
    sleep(randint(1, 3))
    # csfd.cz sits behind a bot-detection challenge that plain `requests` can't pass,
    # so impersonate a real browser's TLS fingerprint.
    response = csfd_requests.get(url, headers=CSFD_HEADERS, params=params, impersonate='chrome')
    response.raise_for_status()
    return BeautifulSoup(response.text, 'html.parser')


def get_tmdb_page(endpoint, page):
    response = requests.get(f'{TMDB_BASE}/discover/{endpoint}', params={
        'api_key': TMDB_API_KEY,
        'watch_region': 'CZ',
        'with_watch_providers': NETFLIX_PROVIDER_ID,
        'with_watch_monetization_types': 'flatrate',
        'language': 'cs-CZ',
        'sort_by': 'popularity.desc',
        'page': page,
    })
    response.raise_for_status()
    return response.json()


def get_netflix_titles():
    """
    Get all movies and TV shows currently available on Netflix in the Czech Republic from TMDB.
    Returns list of tuples with information about every title.
    Information consists of:
    - title
    - year
    - category (movie/tv)
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
                title = result.get('title') or result.get('name')
                if not title:
                    continue
                title = re.sub(r'[\(].*?[\)]', '', title).replace('’', "'").strip()
                date = result.get(date_field) or ''
                year = date[:4]
                titles.append((title, year, category))
            page += 1
            sleep(0.2)
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
    Returns list of all title translations for a movie, taken from the film-names list.
    Strips the site's "more/less" toggle labels that are mixed into the same list items.
    """
    name_list = soup.select_one('.film-header-name ul.film-names')
    titles = []
    if name_list is None:
        return titles
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

    # Takes url's of every rated movie across all pages.
    while True:
        for elem in soup_rating.find_all('h3', class_='film-title-inline'):
            movie_urls.append(elem.a['href'])
        next_page = soup_rating.find('a', class_='page-next')
        if next_page is None:
            break
        soup_rating = get_csfd_soup(CSFD_BASE + next_page['href'])

    # Takes required information from every rated movie.
    for movie in movie_urls:
        if len(csfd_movies) % 10 == 0 and len(csfd_movies) != 0:
            logger.info(f'-- {len(csfd_movies)}th movie is being processed...')
        try:
            soup = get_csfd_soup(CSFD_BASE + movie)
            csfd_movies.append((get_titles(soup), get_year(soup), get_genre(soup)))
        except Exception as e:
            logger.warning(f'-- Skipping {movie}, failed to parse: {e}')
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


def compare_and_save(netflix_titles, csfd_movies):
    """
    Compare movies and saves it into database and csv.
    """
    result = []
    movie = Movies()
    with open(csv_result, 'w', encoding=encoding, newline='') as f:
        csv_writer = csv.writer(f)
        csv_writer.writerow(['title', 'year', 'category', 'percentage'])
        for i in range(len(netflix_titles)):
            flag_seen = False
            for j in range(len(csfd_movies)):
                csfd_titles, csfd_year, _ = csfd_movies[j]
                if netflix_titles[i][0] in csfd_titles and years_match(netflix_titles[i][1], csfd_year):
                    netflix_titles[i] += (True, None, None)
                    flag_seen = True
                    break
            if not flag_seen:
                title, year, category = netflix_titles[i]

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

                netflix_titles[i] += (False, percentage, percentage_checked_at)
                result.append(title)
                csv_writer.writerow([title, year, category, percentage])
        movie.create_and_insert_table(netflix_titles)
    return result


# Main
if __name__ == '__main__':
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
