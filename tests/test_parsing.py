"""
Regression tests for the stable, low-level csfd.cz / TMDB parsing and lookup
helpers in main.py.

These tests intentionally do NOT cover `compare_and_save`, `get_rating_for_title`,
or anything touching the sqlite persistence layer (my_database.py) - those areas
are being actively reworked elsewhere. The goal here is narrower: lock in how we
currently parse csfd.cz's HTML and TMDB's JSON, so that if csfd.cz (or TMDB)
changes shape again, a test fails loudly instead of the scraper silently
returning nothing.

Fixtures under tests/fixtures/ are trimmed-down but REAL HTML fetched live from
csfd.cz with curl_cffi (impersonate='chrome'), the same way main.py fetches it.
See the docstring at the top of each test class for exactly which page each
fixture came from.
"""
import os
import unittest
from unittest.mock import patch, MagicMock

from bs4 import BeautifulSoup

from tests._import_main import main

FIXTURES_DIR = os.path.join(os.path.dirname(__file__), 'fixtures')


def load_fixture(name):
    path = os.path.join(FIXTURES_DIR, name)
    with open(path, encoding='utf-8') as f:
        return BeautifulSoup(f.read(), 'html.parser')


class GetTitlesTests(unittest.TestCase):
    """
    Fixture: tests/fixtures/csfd_film_matrix.html
    A trimmed-but-real snippet of https://www.csfd.cz/film/9499-matrix/prehled/
    containing the `.film-header-name ul.film-names` block with its "vice"/
    "mene" toggle links and several (some duplicate) title translations.
    """

    def setUp(self):
        self.soup = load_fixture('csfd_film_matrix.html')

    def test_returns_deduplicated_titles_in_order(self):
        # The fixture lists (in order): Matrix, Matrix, The Matrix, Matrix,
        # The Matrix, The Matrix - deduplicated that should collapse to just
        # the two distinct translations, in first-seen order.
        self.assertEqual(main.get_titles(self.soup), ['Matrix', 'The Matrix'])

    def test_strips_more_less_toggle_labels(self):
        titles = main.get_titles(self.soup)
        self.assertNotIn('více', titles)
        self.assertNotIn('méně', titles)
        for title in titles:
            self.assertNotIn('více', title)
            self.assertNotIn('méně', title)

    def test_missing_film_names_block_returns_empty_list(self):
        soup = BeautifulSoup('<html><body><div>nothing here</div></body></html>', 'html.parser')
        self.assertEqual(main.get_titles(soup), [])


class GetYearTests(unittest.TestCase):
    """Fixture: tests/fixtures/csfd_film_matrix.html (same film page as above)."""

    def setUp(self):
        self.soup = load_fixture('csfd_film_matrix.html')

    def test_extracts_year_from_origin_div(self):
        self.assertEqual(main.get_year(self.soup), '1999')

    def test_missing_origin_div_returns_empty_string(self):
        soup = BeautifulSoup('<html><body></body></html>', 'html.parser')
        self.assertEqual(main.get_year(soup), '')

    def test_origin_div_without_year_returns_empty_string(self):
        soup = BeautifulSoup('<div class="origin">USA <span class="bullet"></span> 136 min</div>', 'html.parser')
        self.assertEqual(main.get_year(soup), '')


class GetGenreTests(unittest.TestCase):
    """Fixture: tests/fixtures/csfd_film_matrix.html (same film page as above)."""

    def setUp(self):
        self.soup = load_fixture('csfd_film_matrix.html')

    def test_extracts_genre_text(self):
        self.assertEqual(main.get_genre(self.soup), 'Akční Sci-Fi Thriller')

    def test_missing_genres_div_returns_empty_string(self):
        soup = BeautifulSoup('<html><body></body></html>', 'html.parser')
        self.assertEqual(main.get_genre(soup), '')


class GetCsfdRatingPercentageTests(unittest.TestCase):
    """
    Fixture: tests/fixtures/csfd_film_matrix.html - same film page, which also
    contains `div.film-rating-average` ("90%").

    get_csfd_rating_percentage() does its own network fetch via get_csfd_soup,
    so we monkeypatch main.get_csfd_soup to hand back a BeautifulSoup built
    from the saved fixture instead of hitting the network.
    """

    def test_parses_percentage_from_fixture(self):
        soup = load_fixture('csfd_film_matrix.html')
        with patch.object(main, 'get_csfd_soup', return_value=soup) as mock_fetch:
            result = main.get_csfd_rating_percentage('https://www.csfd.cz/film/9499-matrix/prehled/')
        mock_fetch.assert_called_once_with('https://www.csfd.cz/film/9499-matrix/prehled/')
        self.assertEqual(result, 90)

    def test_missing_rating_div_returns_none(self):
        soup = BeautifulSoup('<html><body></body></html>', 'html.parser')
        with patch.object(main, 'get_csfd_soup', return_value=soup):
            result = main.get_csfd_rating_percentage('https://www.csfd.cz/film/whatever/')
        self.assertIsNone(result)

    def test_non_numeric_rating_returns_none(self):
        soup = BeautifulSoup(
            '<div class="film-rating-average">?%</div>', 'html.parser'
        )
        with patch.object(main, 'get_csfd_soup', return_value=soup):
            result = main.get_csfd_rating_percentage('https://www.csfd.cz/film/whatever/')
        self.assertIsNone(result)


class FindCsfdFilmUrlTests(unittest.TestCase):
    """
    Fixture: tests/fixtures/csfd_search_matrix.html - a trimmed-but-real
    snippet of https://www.csfd.cz/hledat/?q=Matrix, containing the first
    three `h3.film-title-nooverflow` search results:
        1. Matrix (1999)              -> /film/9499-matrix/prehled/
        2. Matrix Revolutions (2003)  -> /film/9498-matrix-revolutions/prehled/
        3. Matrix Reloaded (2003)     -> /film/9497-matrix-reloaded/prehled/

    find_csfd_film_url() does its own network fetch via get_csfd_soup, so we
    monkeypatch main.get_csfd_soup to hand back a BeautifulSoup built from the
    saved fixture instead of hitting the network.
    """

    def setUp(self):
        self.soup = load_fixture('csfd_search_matrix.html')

    def test_prefers_year_matching_result_over_first_result(self):
        # The first result on the page is "Matrix (1999)", but asking for
        # year 2003 should skip past it and match "Matrix Revolutions (2003)"
        # instead - proving the function doesn't just take the first hit.
        with patch.object(main, 'get_csfd_soup', return_value=self.soup) as mock_fetch:
            url = main.find_csfd_film_url('Matrix', '2003')
        mock_fetch.assert_called_once_with(main.CSFD_SEARCH_URL, {'q': 'Matrix'})
        self.assertEqual(url, main.CSFD_BASE + '/film/9498-matrix-revolutions/prehled/')

    def test_falls_back_to_first_result_when_no_year_matches(self):
        with patch.object(main, 'get_csfd_soup', return_value=self.soup):
            url = main.find_csfd_film_url('Matrix', '1985')
        self.assertEqual(url, main.CSFD_BASE + '/film/9499-matrix/prehled/')

    def test_no_candidates_returns_none(self):
        empty_soup = BeautifulSoup('<html><body></body></html>', 'html.parser')
        with patch.object(main, 'get_csfd_soup', return_value=empty_soup):
            url = main.find_csfd_film_url('Some Nonexistent Title', '2003')
        self.assertIsNone(url)


class GetUserUrlTests(unittest.TestCase):
    """
    Fixture: tests/fixtures/csfd_search_user.html - a trimmed-but-real
    snippet of https://www.csfd.cz/hledat/?q=David, containing the first
    `a.user-title-name` search result: username "David_",
    href "/uzivatel/954333-david/prehled/".

    get_user_url() reads the module-level `main.user` global, so tests set it
    directly before calling the function.
    """

    def setUp(self):
        self.soup = load_fixture('csfd_search_user.html')
        self._original_user = main.user
        self.addCleanup(setattr, main, 'user', self._original_user)

    def test_matching_username_returns_hodnoceni_url(self):
        main.user = 'David_'
        url = main.get_user_url(self.soup)
        self.assertEqual(url, main.CSFD_BASE + '/uzivatel/954333-david/hodnoceni/')

    def test_matching_username_is_case_insensitive(self):
        main.user = 'david_'
        url = main.get_user_url(self.soup)
        self.assertEqual(url, main.CSFD_BASE + '/uzivatel/954333-david/hodnoceni/')

    def test_non_matching_username_raises(self):
        main.user = 'someone_completely_different'
        with self.assertRaises(Exception):
            main.get_user_url(self.soup)

    def test_no_search_results_raises(self):
        main.user = 'anyone'
        empty_soup = BeautifulSoup('<html><body></body></html>', 'html.parser')
        with self.assertRaises(Exception):
            main.get_user_url(empty_soup)


class GetNetflixTitlesCleanupTests(unittest.TestCase):
    """
    Exercises the title-cleanup logic inline in get_netflix_titles() (stripping
    parenthetical text, e.g. "(Extended Cut)", and normalizing curly
    apostrophes to straight ones) by driving the real function end-to-end with
    `requests.get` mocked to return canned TMDB JSON - no real TMDB_API_KEY or
    network access needed.
    """

    def _tmdb_response(self, results, total_pages=1):
        response = MagicMock()
        response.raise_for_status.return_value = None
        response.json.return_value = {'total_pages': total_pages, 'results': results}
        return response

    def test_strips_parentheticals_and_normalizes_apostrophes(self):
        movie_response = self._tmdb_response([
            {'title': 'Ready Player One (Extended Cut)', 'release_date': '2018-03-28'},
            {'title': 'Marley’s Ghost', 'release_date': '2019-01-01'},
        ])
        tv_response = self._tmdb_response([
            {'name': 'Some Show (US Version)', 'first_air_date': '2021-05-05'},
        ])

        with patch.object(main, 'TMDB_API_KEY', 'fake-test-key'), \
             patch.object(main.requests, 'get', side_effect=[movie_response, tv_response]) as mock_get, \
             patch.object(main, 'sleep', return_value=None):
            titles = main.get_netflix_titles()

        self.assertEqual(mock_get.call_count, 2)
        self.assertIn(('Ready Player One', '2018', 'movie'), titles)
        self.assertIn(("Marley's Ghost", '2019', 'movie'), titles)
        self.assertIn(('Some Show', '2021', 'tv'), titles)
        # Curly apostrophe must not survive into any returned title.
        for title, _year, _category in titles:
            self.assertNotIn('’', title)
            self.assertNotIn('(', title)
            self.assertNotIn(')', title)

    def test_raises_without_api_key(self):
        with patch.object(main, 'TMDB_API_KEY', None):
            with self.assertRaises(Exception):
                main.get_netflix_titles()


if __name__ == '__main__':
    unittest.main()
