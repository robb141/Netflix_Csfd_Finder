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

    def test_includes_header_h1_when_not_repeated_in_film_names(self):
        # Real-world case (e.g. Fight Club): the primary Czech title lives only in
        # the <h1>; film-names lists just the foreign alternates. The <h1> title
        # must still come through, first, so it can be matched against TMDB.
        soup = BeautifulSoup(
            '<div class="film-header-name"><h1>\n Klub rváčů \n</h1>'
            '<ul class="film-names">'
            '<li><img class="flag" alt="USA"/> Fight Club </li>'
            '<li><img class="flag" alt="Slovensko"/> Klub bitkárov </li>'
            '</ul></div>',
            'html.parser',
        )
        self.assertEqual(
            main.get_titles(soup), ['Klub rváčů', 'Fight Club', 'Klub bitkárov']
        )

    def test_header_h1_not_duplicated_when_also_in_film_names(self):
        soup = BeautifulSoup(
            '<div class="film-header-name"><h1>Matrix</h1>'
            '<ul class="film-names"><li>Matrix</li><li>The Matrix</li></ul></div>',
            'html.parser',
        )
        self.assertEqual(main.get_titles(soup), ['Matrix', 'The Matrix'])


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


class GetRatingsPageCountTests(unittest.TestCase):
    """
    Fixture: tests/fixtures/csfd_ratings_pagination.html - the real `div.pagination`
    block from a csfd user's ratings-list page with many pages (numbered "1 2 3 ...
    17", with "..." gaps for skipped ranges), used to show progress as a real
    percentage instead of just a running count.
    """

    def test_reads_last_page_number_from_pagination(self):
        soup = load_fixture('csfd_ratings_pagination.html')
        self.assertEqual(main.get_ratings_page_count(soup), 17)

    def test_missing_pagination_block_returns_one(self):
        soup = BeautifulSoup('<html><body>only one page, no pagination shown</body></html>', 'html.parser')
        self.assertEqual(main.get_ratings_page_count(soup), 1)


class GetCsfdSoupRetryTests(unittest.TestCase):
    """
    get_csfd_soup() retries transient csfd failures with exponential backoff and
    re-raises once the attempts run out. csfd_requests.get and sleep are patched
    so nothing hits the network or actually waits.
    """

    def _ok_response(self, html='<html><body><p>hi</p></body></html>'):
        response = MagicMock()
        response.raise_for_status.return_value = None
        response.text = html
        return response

    def test_returns_soup_on_first_try_without_retrying(self):
        response = self._ok_response('<html><body><h1>Matrix</h1></body></html>')
        with patch.object(main.csfd_requests, 'get', return_value=response) as mock_get, \
             patch.object(main, 'sleep') as mock_sleep:
            soup = main.get_csfd_soup('https://www.csfd.cz/film/x/')
        self.assertEqual(mock_get.call_count, 1)
        self.assertEqual(soup.find('h1').get_text(), 'Matrix')
        # Only the pre-request politeness sleep - no backoff sleep.
        self.assertEqual(mock_sleep.call_count, 1)

    def test_passes_explicit_timeout(self):
        with patch.object(main.csfd_requests, 'get', return_value=self._ok_response()) as mock_get, \
             patch.object(main, 'sleep'):
            main.get_csfd_soup('https://www.csfd.cz/film/x/', {'q': 'a'})
        self.assertEqual(mock_get.call_args.kwargs['timeout'], main.CSFD_REQUEST_TIMEOUT)

    def test_retries_then_succeeds(self):
        err = main.csfd_requests.RequestsError('timed out')
        with patch.object(main.csfd_requests, 'get',
                          side_effect=[err, err, self._ok_response()]) as mock_get, \
             patch.object(main, 'sleep') as mock_sleep:
            soup = main.get_csfd_soup('https://www.csfd.cz/film/x/')
        self.assertEqual(mock_get.call_count, 3)
        self.assertIsNotNone(soup)
        # 1 politeness sleep + 2 backoff sleeps.
        self.assertEqual(mock_sleep.call_count, 3)

    def test_reraises_last_error_after_exhausting_attempts(self):
        err = main.csfd_requests.RequestsError('still down')
        with patch.object(main.csfd_requests, 'get', side_effect=err) as mock_get, \
             patch.object(main, 'sleep'):
            with self.assertRaises(main.csfd_requests.RequestsError):
                main.get_csfd_soup('https://www.csfd.cz/film/x/')
        self.assertEqual(mock_get.call_count, main.CSFD_MAX_ATTEMPTS)


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


class IsLatinScriptTests(unittest.TestCase):

    def test_latin_titles_are_latin_script(self):
        for title in ('The Shawshank Redemption', 'Vykoupení z věznice Shawshank', 'Amélie', "Léon: The Professional"):
            self.assertTrue(main.is_latin_script(title), title)

    def test_non_latin_scripts_are_rejected(self):
        for title in ('ப்யார் ப்ரேமா கல்யாணம்', '新世紀エヴァンゲリオン', 'Москва слезам не верит'):
            self.assertFalse(main.is_latin_script(title), title)

    def test_empty_string_counts_as_latin(self):
        self.assertTrue(main.is_latin_script(''))


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

    @staticmethod
    def _display(titles):
        """(display_title, year, category) triples - drops the match_keys set."""
        return [entry[:3] for entry in titles]

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
        self.assertIn(('Ready Player One', '2018', 'movie'), self._display(titles))
        self.assertIn(("Marley's Ghost", '2019', 'movie'), self._display(titles))
        self.assertIn(('Some Show', '2021', 'tv'), self._display(titles))
        # Curly apostrophe / parentheticals must not survive into any display title.
        for title, _year, _category in self._display(titles):
            self.assertNotIn('’', title)
            self.assertNotIn('(', title)
            self.assertNotIn(')', title)

    def test_raises_without_api_key(self):
        with patch.object(main, 'TMDB_API_KEY', None):
            with self.assertRaises(Exception):
                main.get_netflix_titles()

    def test_non_latin_original_title_falls_back_to_english(self):
        # No Czech translation was available for this title, so the discover
        # result comes back with the original (Tamil) title - get_netflix_titles
        # must notice that and fetch the English title instead, via a follow-up
        # call to /movie/{id}.
        movie_response = self._tmdb_response([
            {'id': 42, 'title': 'ப்யார் ப்ரேமா கல்யாணம்', 'release_date': '2020-01-01'},
        ])
        tv_response = self._tmdb_response([])
        english_fallback_response = MagicMock()
        english_fallback_response.raise_for_status.return_value = None
        english_fallback_response.json.return_value = {'title': 'Love Prema Kalyanam'}

        with patch.object(main, 'TMDB_API_KEY', 'fake-test-key'), \
             patch.object(main.requests, 'get',
                           side_effect=[movie_response, english_fallback_response, tv_response]) as mock_get, \
             patch.object(main, 'sleep', return_value=None):
            titles = main.get_netflix_titles()

        self.assertEqual(mock_get.call_count, 3)
        # The follow-up call must ask for the English title of the same movie id.
        fallback_call = mock_get.call_args_list[1]
        self.assertEqual(fallback_call.args[0], f'{main.TMDB_BASE}/movie/42')
        self.assertEqual(fallback_call.kwargs['params']['language'], 'en-US')
        self.assertIn(('Love Prema Kalyanam', '2020', 'movie'), self._display(titles))

    def test_czech_and_slovak_originals_keep_their_original_title(self):
        # For a cs/sk-origin film the English `title` is ignored in favour of
        # `original_title`, kept exactly as-is (accents and all).
        movie_response = self._tmdb_response([
            {'id': 1, 'title': 'The Ear', 'original_title': 'Ucho',
             'original_language': 'cs', 'release_date': '1970-01-01'},
            {'id': 2, 'title': 'The Teacher', 'original_title': 'Učiteľka',
             'original_language': 'sk', 'release_date': '2016-07-21'},
        ])
        tv_response = self._tmdb_response([])

        with patch.object(main, 'TMDB_API_KEY', 'fake-test-key'), \
             patch.object(main.requests, 'get', side_effect=[movie_response, tv_response]) as mock_get, \
             patch.object(main, 'sleep', return_value=None):
            titles = main.get_netflix_titles()

        self.assertEqual(mock_get.call_count, 2)  # no per-title detail lookups
        self.assertIn(('Ucho', '1970', 'movie'), self._display(titles))
        self.assertIn(('Učiteľka', '2016', 'movie'), self._display(titles))

    def test_match_keys_cover_both_english_and_original_title(self):
        # The displayed title is the English one, but a match must also be able to
        # land on the (accent-folded) original title csfd is more likely to list.
        movie_response = self._tmdb_response([
            {'id': 3, 'title': 'Fight Club', 'original_title': 'Fight Club',
             'original_language': 'en', 'release_date': '1999-10-15'},
            {'id': 4, 'title': 'Cosy Dens', 'original_title': 'Pelíšky',
             'original_language': 'cs', 'release_date': '1999-04-08'},
        ])
        tv_response = self._tmdb_response([])

        with patch.object(main, 'TMDB_API_KEY', 'fake-test-key'), \
             patch.object(main.requests, 'get', side_effect=[movie_response, tv_response]), \
             patch.object(main, 'sleep', return_value=None):
            titles = main.get_netflix_titles()

        fight = next(e for e in titles if e[0] == 'Fight Club')
        self.assertIn('fight club', fight[3])
        # cs film: displays the original title, matches on original AND English.
        cosy = next(e for e in titles if e[0] == 'Pelíšky')
        self.assertIn('pelisky', cosy[3])
        self.assertIn('cosy dens', cosy[3])

    def test_non_latin_with_no_english_available_is_stored_as_is(self):
        movie_response = self._tmdb_response([
            {'id': 7, 'title': 'חיים אחרים', 'release_date': '2010-01-01'},
        ])
        no_english_response = MagicMock()
        no_english_response.raise_for_status.return_value = None
        no_english_response.json.return_value = {}  # TMDB has no en title either
        tv_response = self._tmdb_response([])

        with patch.object(main, 'TMDB_API_KEY', 'fake-test-key'), \
             patch.object(main.requests, 'get',
                          side_effect=[movie_response, no_english_response, tv_response]), \
             patch.object(main, 'sleep', return_value=None):
            titles = main.get_netflix_titles()

        self.assertIn(('חיים אחרים', '2010', 'movie'), self._display(titles))


class MatchKeyTests(unittest.TestCase):

    def test_folds_accents_case_and_whitespace(self):
        self.assertEqual(main._match_key('  Klub   Rváčů '), main._match_key('klub rvacu'))
        self.assertEqual(main._match_key('Amélie'), 'amelie')
        self.assertEqual(main._match_key('LÉON'), 'leon')

    def test_normalizes_apostrophes_and_dashes(self):
        self.assertEqual(main._match_key('Marley’s Ghost'), main._match_key("Marley's Ghost"))
        self.assertEqual(main._match_key('Spider–Man'), main._match_key('Spider-Man'))

    def test_blank_or_symbol_only_titles_give_empty_key(self):
        self.assertEqual(main._match_key(''), '')
        self.assertEqual(main._match_key(None), '')

    def test_match_keys_skips_none_and_empty(self):
        self.assertEqual(
            main._match_keys('The Matrix', None, '', 'the  matrix'),
            {'the matrix'},
        )


if __name__ == '__main__':
    unittest.main()
