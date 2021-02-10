'''
Get movies from flixwatch.co with specified RATING on imdb that are on Netflix for Czech republic.
Look for these movies on csfd and save all movies that USER didnt see to database and generates csv file.

To specify:
- rating on IMDB
- user on csfd

Exceptions:
- Movie on csfd must not start with '(' or that movie title will be ignored
'''
from bs4 import BeautifulSoup
import requests
import csv
from time import sleep, time
from random import randint
import re
from My_Database import Movies

user = input('What csfd user would you like to compare movies to?')
imdb_score = int(input('Above which percentage would you like to fetch movies?'))


def get_soup(url_soup, params={}):
    # Sleep some time before making a request to not overwhelm the websites
    sleep(randint(1, 3))
    return BeautifulSoup(requests.get(url_soup, headers=headers, params=params).text, parser)


def get_next_page(soup_page):
    try:
        return soup_page.find('a', class_='next page-numbers')['href']
    except TypeError:
        return None


def get_flix_movies():
    """
    Get all netflix movies for czech republic that has imdb score more than what is in the imdb_score variable.
    Returns list of tuples with information about movie.
    Information consists of:
    - title
    - year
    - category
    """
    movie_urls = []
    flix_movies = []
    print(f'Processing data from www.flixwatch.co with imdb score greater or equal to {imdb_score}%...')
    url_flix = r'https://www.flixwatch.co/catalogue/netflix-czech-republic/?region%5B%5D=83158&region_operator=IN&ctype_' \
               r'operator=IN&audio_operator=IN&genre_operator=IN&agenre_operator=IN&age_operator=IN&imdb={}%3B100&' \
               r'metascore=0%3B100&release=1920%3B2021&sort=default'.format(imdb_score)

    # Takes url's of movies across all pages.
    while True:
        soup = get_soup(url_flix)
        movies = soup.find_all('div', class_='catalogue-item')
        for movie in movies:
            movie_urls.append(movie.find('a')['href'])
        url_flix = get_next_page(soup)
        if url_flix is None:
            break

    # Takes required information from every movie.
    for url in movie_urls:
        soup = get_soup(url)
        for line in soup.find('div', class_='grid-single-child'):
            if line.b.text == 'Year:':
                # Stores title, year and category.
                # In title it removes everything between parenthesis. Category (movies/tvshows) is taken from the url.
                flix_movies.append((re.sub(r"[\(].*?[\)]", "", soup.find('h1', class_='h1class').text.replace("’", "'")),
                                    line.text.split()[1],
                                    url.split('/')[-3]))
    return flix_movies


def get_user_url(soup):
    """
    Searches user on website.
    Returns error if the first search result on website is not equal to searched user, otherwise returns user rating url.
    """
    try:
        first_user = soup.find('section', class_='box striped-articles main-users').a['title'].lower()
    except:
        raise Exception(f'User {user} doesn\'t exist!')
    if user.lower() != first_user:
        raise Exception(f'Could not find user {user}. The first user found is {first_user}')
    url = url_csfd + soup.find('section', class_='box striped-articles main-users').a['href'] + 'hodnoceni'
    return url


def get_csfd_movies():
    """
    Gets all of the user's rated urls and then
    returns list of tuples with information about every rated movie in format [([titles], year, genre), ...]
    Titles is list of strings - we keep all of the movie translations.
    """
    print(f'Getting all rated movies from user {user}...')
    movie_urls = []
    csfd_movies = []
    url_rating = get_user_url(get_soup(url_csfd_search, user_parameters))
    soup_rating = get_soup(url_rating)

    # Takes url's of every rated movie across all pages.
    while True:
        for elem in soup_rating.find_all('h3', class_='film-title-nooverflow'):
            movie_urls.append(elem.a['href'])
        try:
            soup_rating = get_soup(url_csfd + soup_rating.find('a', class_='page-next')['href'])
        except TypeError:
            break

    # Takes required information from every rated movie.
    for movie in movie_urls:
        if len(csfd_movies) % 10 == 0 and len(csfd_movies) != 0:
            print(f'-- {len(csfd_movies)}th movie is being processed...')
        soup = get_soup(url_csfd + movie)
        movie_par = soup.find('div', class_='film-header-name').text
        a = movie_par.replace('\t', '').split('\n')
        movie_titles = []
        for s in a:
            if s != '' and not s.startswith('(') and s not in movie_titles:
                movie_titles.append(s)
        year = soup.find('span', itemprop='dateCreated').text
        if '(' in year:
            year = year.replace('(', '').replace(')', '')
        genre = soup.find('div', class_='genres').text
        csfd_movies.append((movie_titles, year, genre))
    return csfd_movies


def compare_and_save(flix_movies, csfd_movies):
    """
    Compare movies and saves it into database and csv.
    """
    result = []
    with open(csv_result, 'w', encoding=encoding) as f:
        csv_writer = csv.writer(f)
        csv_writer.writerow(['title', 'year', 'category', 'genre'])
        for i in range(len(flix_movies)):
            flag_seen = False
            for j in range(len(csfd_movies)):
                if flix_movies[i][0] in csfd_movies[j][0]:
                    flix_movies[i] += (True, )
                    flag_seen = True
                    break
            if not flag_seen:
                flix_movies[i] += (False, )
                result.append(flix_movies[i][0])
                csv_writer.writerow([flix_movies[i][0], flix_movies[i][1], flix_movies[i][2], csfd_movies[j][2]])
        movie = Movies()
        movie.create_and_insert_table(flix_movies)
    return result


url_csfd = 'https://new.csfd.cz'
url_csfd_search = url_csfd + '/hledat'
headers = {'User-Agent': 'Chrome/39.0.2171.95'}
user_parameters = {'q': user}
parser = 'html.parser'
encoding = 'utf-8'
csv_flix = 'flix_movies.csv'
csv_csfd = 'csfd_movies.csv'
csv_result = f'movies_with_{imdb_score}_percent.csv'

# Main
if __name__ == '__main__':
    start = time()
    flix_tuples = get_flix_movies()
    csfd_tuples = get_csfd_movies()
    res = compare_and_save(flix_tuples, csfd_tuples)
    print('\nNot seen movies:\n-- ' + '\n-- '.join(res))
    print(f'\nTotal time of run is: {(time() - start)/60} minutes.')
