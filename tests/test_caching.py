"""
Tests for the percentage-caching and year-aware matching logic added on top of
the stable parsing helpers already covered by test_parsing.py:

- main.years_match / main._cached_percentage_age_days (pure functions)
- my_database.Movies.get_cached_percentage and the create_and_insert_table
  upsert/migration behavior
- main.compare_and_save's orchestration of all of the above, with
  get_rating_for_title mocked so nothing ever hits the network

my_database.Movies.__init__ hardcodes sqlite3.connect('movies.db') relative to
the CWD, with no way to inject a path. Rather than chdir for the whole test
run, TempDbTestCase below monkeypatches my_database.sqlite3.connect so that
any connection request for 'movies.db' is transparently redirected to a fresh
temp-directory file per test - still a real, file-backed sqlite3 connection
(not ':memory:'), which matters here because several tests need data written
by one Movies()/compare_and_save() call to still be there for a second one.
"""
import os
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import my_database
from my_database import Movies
from tests._import_main import main


class YearsMatchTests(unittest.TestCase):

    def test_same_year_matches(self):
        self.assertTrue(main.years_match('2020', '2020'))

    def test_different_years_do_not_match(self):
        self.assertFalse(main.years_match('2020', '2021'))

    def test_missing_year_a_is_treated_as_unknown_and_matches(self):
        # An empty/unknown year on either side means we can't prove a mismatch,
        # so it must not be used to reject an otherwise-good title match.
        self.assertTrue(main.years_match('', '2020'))
        self.assertTrue(main.years_match(None, '2020'))

    def test_missing_year_b_is_treated_as_unknown_and_matches(self):
        self.assertTrue(main.years_match('2020', ''))
        self.assertTrue(main.years_match('2020', None))

    def test_both_years_missing_matches(self):
        self.assertTrue(main.years_match('', ''))


class CachedPercentageAgeDaysTests(unittest.TestCase):

    def test_recent_timestamp_returns_small_positive_age(self):
        timestamp = datetime.now(timezone.utc).isoformat()
        age = main._cached_percentage_age_days(timestamp)
        self.assertIsNotNone(age)
        self.assertGreaterEqual(age, 0)
        self.assertLess(age, 0.01)  # well under a second's worth of days

    def test_timestamp_n_days_ago_returns_approximately_n(self):
        timestamp = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
        age = main._cached_percentage_age_days(timestamp)
        self.assertAlmostEqual(age, 30, delta=0.01)

    def test_naive_timestamp_is_treated_as_utc(self):
        # A timestamp with no tzinfo (e.g. from an older row written before UTC
        # awareness was added) should still be usable rather than raising.
        timestamp = (datetime.now(timezone.utc) - timedelta(days=5)).replace(tzinfo=None).isoformat()
        age = main._cached_percentage_age_days(timestamp)
        self.assertAlmostEqual(age, 5, delta=0.01)

    def test_unparseable_value_returns_none(self):
        self.assertIsNone(main._cached_percentage_age_days('not-a-timestamp'))

    def test_none_value_returns_none(self):
        self.assertIsNone(main._cached_percentage_age_days(None))


class TempDbTestCase(unittest.TestCase):
    """
    Redirects every my_database.sqlite3.connect('movies.db') call in a test to
    a fresh file inside a per-test temp directory, so tests never touch the
    real movies.db in the repo root and don't leak state between each other.
    """

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self.db_path = os.path.join(self._tmpdir.name, 'movies.db')

        real_connect = sqlite3.connect

        def fake_connect(name, *args, **kwargs):
            return real_connect(self.db_path, *args, **kwargs)

        patcher = patch.object(my_database.sqlite3, 'connect', side_effect=fake_connect)
        patcher.start()
        self.addCleanup(patcher.stop)


class GetCachedPercentageTests(TempDbTestCase):

    def test_no_matching_row_returns_none(self):
        movie = Movies()
        self.assertIsNone(movie.get_cached_percentage('Nonexistent Movie', '2020'))

    def test_row_with_null_percentage_returns_none(self):
        # A title marked "seen" stores NULL for both percentage and
        # percentage_checked_at - that must read back as "no cache", not as a
        # cached (None, None) pair.
        movie = Movies()
        movie.create_and_insert_table([('Seen Movie', '2020', 'movie', True, None, None)])
        self.assertIsNone(movie.get_cached_percentage('Seen Movie', '2020'))

    def test_row_with_percentage_returns_tuple(self):
        movie = Movies()
        checked_at = datetime.now(timezone.utc).isoformat()
        movie.create_and_insert_table([('Rated Movie', '2020', 'movie', False, 77, checked_at)])
        self.assertEqual(movie.get_cached_percentage('Rated Movie', '2020'), (77, checked_at))


class CreateAndInsertTableUpsertTests(TempDbTestCase):

    def _row_for(self, movie, title):
        return next(row for row in movie.get_data('1=1') if row[1] == title)

    def test_inserting_different_title_leaves_first_untouched(self):
        movie = Movies()
        movie.create_and_insert_table([('First Movie', '2020', 'movie', False, 60, 'ts-1')])
        movie.create_and_insert_table([('Second Movie', '2021', 'movie', False, 70, 'ts-2')])

        all_rows = movie.get_data('1=1')
        titles = {row[1] for row in all_rows}
        # Both rows must exist - the second insert must not have wiped the first.
        self.assertEqual(titles, {'First Movie', 'Second Movie'})
        first_row = self._row_for(movie, 'First Movie')
        self.assertEqual(first_row[5], 60)
        self.assertEqual(first_row[6], 'ts-1')

    def test_reinserting_same_title_and_year_updates_in_place(self):
        movie = Movies()
        movie.create_and_insert_table([('Same Movie', '2020', 'movie', False, 10, 'ts-old')])
        movie.create_and_insert_table([('Same Movie', '2020', 'tv', True, 99, 'ts-new')])

        all_rows = [row for row in movie.get_data('1=1') if row[1] == 'Same Movie']
        # Must be exactly one row (updated), not two (duplicated).
        self.assertEqual(len(all_rows), 1)
        row = all_rows[0]
        self.assertEqual(row[3], 'tv')
        self.assertEqual(row[4], 1)
        self.assertEqual(row[5], 99)
        self.assertEqual(row[6], 'ts-new')

    def test_legacy_schema_migration_adds_missing_column(self):
        # Simulate a database created before percentage caching existed: no
        # percentage_checked_at column at all. Built with a plain sqlite3
        # connection directly against self.db_path (bypassing my_database
        # entirely), since the patched my_database.sqlite3.connect isn't
        # involved until a Movies() instance is created below.
        raw_connection = sqlite3.connect(self.db_path)
        raw_connection.execute(
            'CREATE TABLE netflix (movie_id INTEGER PRIMARY KEY, title TEXT, year INTEGER, '
            'category TEXT, seen BOOLEAN NOT NULL default 0, percentage INTEGER)'
        )
        raw_connection.execute(
            'INSERT INTO netflix (title, year, category, seen, percentage) VALUES (?,?,?,?,?)',
            ('Old Movie', 2001, 'movie', 0, 42),
        )
        raw_connection.commit()
        raw_connection.close()

        # A fresh Movies() against the same db file must migrate the schema
        # without raising, even though percentage_checked_at didn't exist yet.
        movie = Movies()

        # The pre-existing row has a percentage but (pre-migration) no
        # percentage_checked_at, so it must read back as "no cache" rather
        # than raising or returning a stale/garbage percentage.
        self.assertIsNone(movie.get_cached_percentage('Old Movie', 2001))

        # Confirm the migrated column is genuinely writable/readable now.
        movie.create_and_insert_table([('Old Movie', 2001, 'movie', False, 42, '2026-01-01T00:00:00+00:00')])
        self.assertEqual(
            movie.get_cached_percentage('Old Movie', 2001),
            (42, '2026-01-01T00:00:00+00:00'),
        )


class CompareAndSaveTests(TempDbTestCase):
    """
    Exercises main.compare_and_save end-to-end against a real (temp) sqlite
    db and csv file, with main.get_rating_for_title mocked so no test ever
    hits csfd.cz.
    """

    def setUp(self):
        super().setUp()
        self.csv_path = os.path.join(self._tmpdir.name, 'movies_not_seen_on_csfd.csv')
        csv_patcher = patch.object(main, 'csv_result', self.csv_path)
        csv_patcher.start()
        self.addCleanup(csv_patcher.stop)

    def _seen_flag(self, title):
        movie = Movies()
        row = next(r for r in movie.get_data('1=1') if r[1] == title)
        return row  # (movie_id, title, year, category, seen, percentage, percentage_checked_at)

    def test_title_matching_on_title_and_year_is_saved_as_seen_without_rating_lookup(self):
        netflix_titles = [('Movie A', '2020', 'movie')]
        csfd_movies = [(['Movie A'], '2020', 'Drama')]

        with patch.object(main, 'get_rating_for_title') as mock_rating:
            result = main.compare_and_save(netflix_titles, csfd_movies)

        mock_rating.assert_not_called()
        self.assertEqual(result, [])
        row = self._seen_flag('Movie A')
        self.assertEqual(row[4], 1)
        self.assertIsNone(row[5])
        self.assertIsNone(row[6])

    def test_same_title_different_year_is_treated_as_unseen(self):
        # Proves the year check is wired into compare_and_save's matching
        # loop, not just correct in years_match() taken in isolation.
        netflix_titles = [('Movie B', '2020', 'movie')]
        csfd_movies = [(['Movie B'], '1999', 'Drama')]

        with patch.object(main, 'get_rating_for_title', return_value=88) as mock_rating:
            result = main.compare_and_save(netflix_titles, csfd_movies)

        mock_rating.assert_called_once_with('Movie B', '2020')
        self.assertEqual(result, ['Movie B'])
        row = self._seen_flag('Movie B')
        self.assertEqual(row[4], 0)
        self.assertEqual(row[5], 88)

    def test_unseen_title_triggers_exactly_one_rating_lookup_on_fresh_db(self):
        netflix_titles = [('Movie C', '2021', 'movie')]

        with patch.object(main, 'get_rating_for_title', return_value=55) as mock_rating:
            result = main.compare_and_save(netflix_titles, [])

        mock_rating.assert_called_once_with('Movie C', '2021')
        self.assertEqual(result, ['Movie C'])

    def test_second_run_reuses_cached_percentage_and_skips_rating_lookup(self):
        # First run: nothing cached yet, so get_rating_for_title must be
        # called once and its result persisted.
        with patch.object(main, 'get_rating_for_title', return_value=70) as mock_rating_first:
            main.compare_and_save([('Movie D', '2021', 'movie')], [])
        mock_rating_first.assert_called_once_with('Movie D', '2021')

        # Second run for the same still-unseen title: the freshly-written
        # cache should short-circuit the lookup entirely. A fresh list of
        # netflix_titles is passed both times since compare_and_save extends
        # each tuple in place.
        with patch.object(main, 'get_rating_for_title') as mock_rating_second:
            result = main.compare_and_save([('Movie D', '2021', 'movie')], [])

        mock_rating_second.assert_not_called()
        self.assertEqual(result, ['Movie D'])
        row = self._seen_flag('Movie D')
        self.assertEqual(row[5], 70)


if __name__ == '__main__':
    unittest.main()
