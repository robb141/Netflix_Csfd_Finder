'''
Get movies from flixwatch.co with specified RATING on imdb that are on Netflix for Czech republic.
Look for these movies on csfd and save all movies that USER didnt see to csv file.

To specify:
- rating on IMDB
- user on csfd

Exceptions:
- Movie on csfd must not start with '(' or that movie title will be ignored
'''
from bs4 import BeautifulSoup
import requests
import csv
from time import sleep
from random import randint
import re


def get_soup(url_soup, params={}):
    # Sleep some time before making a request to not overwhelm the websites
    sleep(randint(1, 3))
    return BeautifulSoup(requests.get(url_soup, headers=headers, params=params).text, parser)


def get_next_page(soup_page):
    try:
        return soup_page.find('a', class_='next page-numbers')['href']
    except:
        return None


def get_flix_movies():
    """
    Get all netflix movies for czech republic that has imdb score more than what is in the imdb_score variable.
    Returns list of dictionaries with information about movie.
    Information consists of:
    - title
    - year
    - category
    """
    movie_urls = []
    url_flix = r'https://www.flixwatch.co/catalogue/netflix-czech-republic/?region%5B%5D=83158&region_operator=IN&ctype_' \
               r'operator=IN&audio_operator=IN&genre_operator=IN&agenre_operator=IN&age_operator=IN&imdb={}%3B100&' \
               r'metascore=0%3B100&release=1920%3B2021&sort=default'.format(imdb_score)
    while True:
        soup = get_soup(url_flix)
        movies = soup.find_all('div', class_='catalogue-item')
        for movie in movies:
            movie_urls.append(movie.find('a')['href'])
        url_flix = get_next_page(soup)
        if url_flix is None:
            break
    return get_flix_dicts(movie_urls)


def get_flix_dicts(urls):
    """
    Returns list of dictionaries and saves information about the movies to csv file.
    """
    dicts_flix = []
    with open(csv_flix, 'w', encoding=encoding) as f:
        csv_writer = csv.writer(f)
        csv_writer.writerow(['title', 'year', 'category'])
        for url in urls:
            soup = get_soup(url)
            for line in soup.find('div', class_='grid-single-child'):
                if line.b.text == 'Year:':
                    # In title removes everything between parenthesis. Category (movies/tvshows) is taken from the url.
                    dicts_flix.append({
                        'title': re.sub(r"[\(].*?[\)]", "", soup.find('h1', class_='h1class').text.replace("’", "'")),
                        'year': line.text.split()[1],
                        'category': url.split('/')[-3]
                    })
                    csv_writer.writerow([soup.find('h1', class_='h1class').text,
                                         line.text.split()[1],
                                         url.split('/')[-3]])
    return dicts_flix


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
    Gets all of the user's rated urls and then with function get_user_movies returns list of dictionaries with information about every rated movie.
    """
    rated_movies = []
    url_rating = get_user_url(get_soup(url_csfd_search, user_parameters))
    soup_rating = get_soup(url_rating)
    while True:
        # a = soup_rating.find('tbody')
        for elem in soup_rating.find_all('h3', class_='film-title-nooverflow'):
            rated_movies.append(elem.a['href'])
        try:
            soup_rating = get_soup(url_csfd + soup_rating.find('a', class_='page-next')['href'])
        except:
            break
    return get_user_movies(rated_movies)


def get_user_movies(urls):
    """
    Returns list of dictionaries and saves information about the movies to csv file.
    Title is list of strings, we keep all of the movie translations.
    """
    list_movies = []
    with open(csv_csfd, 'w', encoding=encoding) as f:
        csv_writer = csv.writer(f)
        csv_writer.writerow(['title', 'year', 'genre'])
        for movie in urls:
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
            d_movies = {
                'title': movie_titles,
                'year': year,
                'genre': genre
            }
            list_movies.append(d_movies)
            csv_writer.writerow([movie_titles, year, genre])
    return list_movies


def compare(f_dict, c_dict):
    """
    Compare lists of dictionaries.

    """
    result = []
    with open(csv_result, 'w', encoding=encoding) as f:
        csv_writer = csv.writer(f)
        csv_writer.writerow(['title', 'year', 'category', 'genre'])
        for i in f_dict:
            seen = False
            for j in c_dict:
                if i['title'] in j['title']:
                    seen = True
                    break
            if not seen:
                result.append(i['title'])
                csv_writer.writerow([i['title'], i['year'], i['category'], j['genre']])
    return result


imdb_score = 80
user = 'sentienpin'
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
    flix_dicts = get_flix_movies()
    csfd_dicts = get_csfd_movies()
    res = compare(flix_dicts, csfd_dicts)
    print('Not seen movies:\n-- ' + '\n-- '.join(res))
