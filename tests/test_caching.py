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
import csv
import os
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import ANY, patch

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

    def test_inserting_multiple_titles_in_one_call_keeps_all_of_them(self):
        movie = Movies()
        movie.create_and_insert_table([
            ('First Movie', '2020', 'movie', False, 60, 'ts-1'),
            ('Second Movie', '2021', 'movie', False, 70, 'ts-2'),
        ])

        all_rows = movie.get_data('1=1')
        titles = {row[1] for row in all_rows}
        self.assertEqual(titles, {'First Movie', 'Second Movie'})
        first_row = self._row_for(movie, 'First Movie')
        self.assertEqual(first_row[5], 60)
        self.assertEqual(first_row[6], 'ts-1')

    def test_title_absent_from_a_later_call_is_deleted(self):
        # create_and_insert_table is called once per run with the FULL current
        # catalogue, so each call represents "this is the complete truth" - a title
        # missing from a later call (e.g. one that left Netflix) must be deleted,
        # not left behind looking like it's still there.
        movie = Movies()
        movie.create_and_insert_table([
            ('Staying Movie', '2020', 'movie', False, 60, 'ts-1'),
            ('Leaving Movie', '2019', 'movie', True, None, None),
        ])
        movie.create_and_insert_table([
            ('Staying Movie', '2020', 'movie', False, 60, 'ts-1'),
        ])

        all_rows = movie.get_data('1=1')
        titles = {row[1] for row in all_rows}
        self.assertEqual(titles, {'Staying Movie'})

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


class UpsertRowsAndPruneTests(TempDbTestCase):
    """
    Covers the incremental-persistence API split out of create_and_insert_table:
    upsert_rows (batch write, commit, no pruning) and prune_missing_titles
    (end-of-run delete of titles that left the catalogue).
    """

    def _titles(self, movie):
        return {row[1] for row in movie.get_data('1=1')}

    def test_can_upsert_is_true_on_a_fresh_database(self):
        # A fresh db gets the UNIQUE(title, year) index, so incremental commits
        # are available - the property must also populate the flag itself rather
        # than returning the un-migrated default.
        self.assertTrue(Movies().can_upsert)

    def test_upsert_rows_persists_a_batch_visible_to_a_second_instance(self):
        Movies().upsert_rows([
            ('First Movie', '2020', 'movie', False, 60, 'ts-1'),
            ('Second Movie', '2021', 'movie', False, 70, 'ts-2'),
        ])

        movie = Movies()
        self.assertEqual(self._titles(movie), {'First Movie', 'Second Movie'})
        self.assertEqual(movie.get_cached_percentage('First Movie', '2020'), (60, 'ts-1'))

    def test_upsert_rows_updates_in_place_and_never_prunes(self):
        movie = Movies()
        movie.upsert_rows([('Kept Movie', '2020', 'movie', False, 10, 'ts-old')])
        # A second batch that omits 'Kept Movie' must NOT delete it (a batch is a
        # slice of the run, not the whole catalogue), and must update a row it
        # does carry rather than duplicate it.
        movie.upsert_rows([('Kept Movie', '2020', 'tv', True, 99, 'ts-new')])
        movie.upsert_rows([('Later Movie', '2022', 'movie', False, 50, 'ts-2')])

        self.assertEqual(self._titles(movie), {'Kept Movie', 'Later Movie'})
        kept = next(r for r in movie.get_data('1=1') if r[1] == 'Kept Movie')
        self.assertEqual((kept[3], kept[4], kept[5], kept[6]), ('tv', 1, 99, 'ts-new'))

    def test_prune_missing_titles_drops_absent_rows_and_keeps_present_ones(self):
        movie = Movies()
        movie.upsert_rows([
            ('Staying Movie', '2020', 'movie', False, 60, 'ts-1'),
            ('Leaving Movie', '2019', 'movie', True, None, None),
        ])

        movie.prune_missing_titles([('Staying Movie', '2020', 'movie', False, 60, 'ts-1')])

        self.assertEqual(self._titles(movie), {'Staying Movie'})


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
        # cache should short-circuit the lookup entirely.
        with patch.object(main, 'get_rating_for_title') as mock_rating_second:
            result = main.compare_and_save([('Movie D', '2021', 'movie')], [])

        mock_rating_second.assert_not_called()
        self.assertEqual(result, ['Movie D'])
        row = self._seen_flag('Movie D')
        self.assertEqual(row[5], 70)

    def test_match_is_accent_and_case_insensitive_with_year_guard(self):
        # Netflix stores the ascii-folded English title; the user's csfd film is
        # listed only under its accented Czech title. Same year -> seen, and no
        # rating lookup happens.
        netflix_titles = [('Klub rvacu', '1999', 'movie', frozenset({'klub rvacu', 'fight club'}))]
        csfd_movies = [(['Klub rváčů', 'Fight Club'], '1999', 'Drama')]

        with patch.object(main, 'get_rating_for_title') as mock_rating:
            result = main.compare_and_save(netflix_titles, csfd_movies)

        mock_rating.assert_not_called()
        self.assertEqual(result, [])
        self.assertEqual(self._seen_flag('Klub rvacu')[4], 1)

    def test_same_normalized_title_but_wrong_year_is_not_a_match(self):
        netflix_titles = [('Klub rvacu', '2010', 'movie', frozenset({'klub rvacu'}))]
        csfd_movies = [(['Klub rváčů'], '1999', 'Drama')]

        with patch.object(main, 'get_rating_for_title', return_value=90) as mock_rating:
            result = main.compare_and_save(netflix_titles, csfd_movies)

        mock_rating.assert_called_once_with('Klub rvacu', '2010')
        self.assertEqual(result, ['Klub rvacu'])

    def _read_csv(self):
        with open(self.csv_path, encoding=main.encoding, newline='') as f:
            return list(csv.reader(f))

    def test_committed_batches_survive_a_mid_run_crash(self):
        # Shrink the batch to 2 rows so a short title list still spans several
        # commits, then blow up on the 3rd rating lookup - by then the first
        # batch ('Movie 1' + 'Movie 2') has been committed, the rest has not.
        netflix_titles = [(f'Movie {n}', '2020', 'movie') for n in range(1, 6)]

        with patch.object(my_database, 'DB_COMMIT_BATCH_SIZE', 2), \
                patch.object(main, 'get_rating_for_title',
                             side_effect=[61, 62, RuntimeError('csfd went down')]):
            with self.assertRaises(RuntimeError):
                main.compare_and_save(netflix_titles, [])

        movie = Movies()
        titles = {row[1] for row in movie.get_data('1=1')}
        self.assertEqual(titles, {'Movie 1', 'Movie 2'})
        self.assertEqual(movie.get_cached_percentage('Movie 1', '2020'), (61, ANY))

    def test_csv_is_written_atomically_on_a_normal_run(self):
        with patch.object(main, 'get_rating_for_title', return_value=75):
            main.compare_and_save([('Movie X', '2020', 'movie')], [])

        self.assertTrue(os.path.exists(self.csv_path))
        self.assertEqual(
            self._read_csv(),
            [['title', 'year', 'category', 'percentage'], ['Movie X', '2020', 'movie', '75']],
        )
        # The temp file it was built in must not be left behind.
        leftovers = [n for n in os.listdir(self._tmpdir.name) if n.endswith('.csv.tmp')]
        self.assertEqual(leftovers, [])

    def test_a_crashing_run_leaves_the_existing_csv_untouched(self):
        with open(self.csv_path, 'wb') as f:
            f.write(b'PREVIOUS RUN CSV - MUST NOT BE TRUNCATED')

        with patch.object(main, 'get_rating_for_title', side_effect=RuntimeError('boom')):
            with self.assertRaises(RuntimeError):
                main.compare_and_save([('Movie Y', '2020', 'movie')], [])

        with open(self.csv_path, 'rb') as f:
            self.assertEqual(f.read(), b'PREVIOUS RUN CSV - MUST NOT BE TRUNCATED')
        leftovers = [n for n in os.listdir(self._tmpdir.name) if n.endswith('.csv.tmp')]
        self.assertEqual(leftovers, [])


if __name__ == '__main__':
    unittest.main()
