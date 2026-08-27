import json
import logging
import sqlite3

logger = logging.getLogger(__name__)


class Movies:
    def __init__(self):
        self.__db_name = 'netflix'
        self.__connection = sqlite3.connect('movies.db')
        self.__c = self.__connection.cursor()
        # Whether a UNIQUE(title, year) index is in place, which is required for the
        # upsert (ON CONFLICT) used by create_and_insert_table to work. It may be False
        # for a pre-existing database that already has duplicate (title, year) rows.
        self.__can_upsert = False

    def __del__(self):
        self.__connection.close()

    def __ensure_schema(self):
        """
        Creates the netflix table if it doesn't exist yet, and migrates an older table
        (from before percentage caching existed) to have the percentage_checked_at
        column and a UNIQUE(title, year) index, tolerating a table/column/index that
        already exists.
        """
        try:
            self.__c.execute(
                f'CREATE TABLE {self.__db_name} ('
                'movie_id INTEGER PRIMARY KEY, '
                'title TEXT, '
                'year INTEGER, '
                'category TEXT, '
                'seen BOOLEAN NOT NULL default 0, '
                'percentage INTEGER, '
                'percentage_checked_at TEXT)'
            )
        except sqlite3.OperationalError:
            logger.info('Table already exists!')

        # Existing databases created before percentage caching was added won't have
        # this column - add it, tolerating it already being there.
        try:
            self.__c.execute(f'ALTER TABLE {self.__db_name} ADD COLUMN percentage_checked_at TEXT')
        except sqlite3.OperationalError:
            pass

        # An upsert keyed on (title, year) requires a unique index on those columns.
        # A pre-existing database may already contain duplicate (title, year) rows
        # (from before this constraint existed), in which case creating the index
        # fails - fall back to the old delete-and-reinsert behavior in that case.
        try:
            self.__c.execute(
                f'CREATE UNIQUE INDEX IF NOT EXISTS ux_{self.__db_name}_title_year '
                f'ON {self.__db_name}(title, year)'
            )
            self.__can_upsert = True
        except sqlite3.IntegrityError:
            logger.warning(
                'Could not create a UNIQUE(title, year) index on the existing netflix '
                'table (duplicate rows present) - falling back to wiping the table on '
                'every run, so cached percentages will not persist this time.'
            )
            self.__can_upsert = False

        self.__connection.commit()

    def create_and_insert_table(self, movies):
        """
        Ensures the table/schema exists, then either upserts each row keyed on
        (title, year) - preserving any previously-stored percentage/timestamp for rows
        not present in `movies`, and updating rows that are - or, if upserting isn't
        possible (see __ensure_schema), falls back to wiping and reinserting everything.

        `movies` is an iterable of tuples:
        (title, year, category, seen, percentage, percentage_checked_at)
        """
        self.__ensure_schema()
        movies = list(movies)

        if self.__can_upsert:
            self.__c.executemany(
                f'INSERT INTO {self.__db_name}'
                '(title, year, category, seen, percentage, percentage_checked_at) '
                'VALUES (?,?,?,?,?,?) '
                'ON CONFLICT(title, year) DO UPDATE SET '
                'category=excluded.category, '
                'seen=excluded.seen, '
                'percentage=excluded.percentage, '
                'percentage_checked_at=excluded.percentage_checked_at',
                movies,
            )
            self.__delete_removed_titles(movies)
        else:
            self.__c.execute(f'DELETE FROM {self.__db_name}')
            self.__c.executemany(
                f'INSERT INTO {self.__db_name}'
                '(title, year, category, seen, percentage, percentage_checked_at) '
                'VALUES (?,?,?,?,?,?)',
                movies,
            )
        self.__connection.commit()

    def __delete_removed_titles(self, movies):
        """
        Removes rows for titles no longer present in the current run's catalogue (e.g.
        a movie that has left Netflix) - upserting alone only adds/updates rows, it
        never removes ones that stopped being returned by TMDB. Without this, a removed
        title would linger in the database forever, still looking like it's on Netflix.
        """
        self.__c.execute('CREATE TEMP TABLE IF NOT EXISTS current_titles (title TEXT, year INTEGER)')
        self.__c.execute('DELETE FROM current_titles')
        self.__c.executemany(
            'INSERT INTO current_titles (title, year) VALUES (?, ?)',
            [(title, year) for title, year, *_ in movies],
        )
        self.__c.execute(
            f'DELETE FROM {self.__db_name} WHERE NOT EXISTS ('
            'SELECT 1 FROM current_titles '
            f'WHERE current_titles.title = {self.__db_name}.title '
            f'AND current_titles.year = {self.__db_name}.year)'
        )
        self.__c.execute('DROP TABLE current_titles')

    def get_cached_percentage(self, title, year):
        """
        Looks up a previously-stored (percentage, percentage_checked_at) pair for a
        title, matched on (title, year) the same way rows are keyed for upserting.
        Returns None if there's no row, or no percentage/timestamp was ever recorded
        for it (e.g. the title was marked seen, or the rating lookup failed).
        """
        self.__ensure_schema()
        self.__c.execute(
            f'SELECT percentage, percentage_checked_at FROM {self.__db_name} '
            'WHERE title = ? AND year = ?',
            (title, year),
        )
        row = self.__c.fetchone()
        if row is None:
            return None
        percentage, checked_at = row
        if percentage is None or checked_at is None:
            return None
        return percentage, checked_at

    def get_data(self, condition):
        self.__c.execute(f'SELECT * FROM {self.__db_name} WHERE {condition}')
        all_movies = self.__c.fetchall()
        return all_movies


class CsfdFilmCache:
    """
    Permanent (no expiry) cache of csfd film-page metadata - titles, year, genre -
    keyed by the film's href/URL, stored in the same movies.db sqlite file as the
    `netflix` table (in its own `csfd_films` table).

    Unlike the separate percentage cache on Movies (which has a 180-day staleness
    window because audience-rating percentages change over time), a film's
    titles/year/genre never change once scraped. So this cache has no age/expiry
    logic at all: once a href is stored, it is considered valid forever, for every
    user, across every run.
    """

    def __init__(self):
        self.__table_name = 'csfd_films'
        self.__connection = sqlite3.connect('movies.db')
        self.__c = self.__connection.cursor()
        self.__ensure_schema()

    def __del__(self):
        self.__connection.close()

    def __ensure_schema(self):
        try:
            self.__c.execute(
                f'CREATE TABLE {self.__table_name} '
                '(href TEXT PRIMARY KEY, titles TEXT NOT NULL, year TEXT, genre TEXT)'
            )
            self.__connection.commit()
        except sqlite3.OperationalError:
            # Table already exists (e.g. an existing movies.db from before this
            # cache was introduced, or a previous run already created it).
            pass

    def get(self, href):
        """
        Looks up a cached (titles, year, genre) tuple for the given film href.
        Returns None if this href has never been cached. `titles` is deserialized
        back into a list of strings.
        """
        self.__c.execute(
            f'SELECT titles, year, genre FROM {self.__table_name} WHERE href = ?', (href,)
        )
        row = self.__c.fetchone()
        if row is None:
            return None
        titles_json, year, genre = row
        return json.loads(titles_json), year, genre

    def set(self, href, titles, year, genre):
        """
        Stores (titles, year, genre) for the given film href. A given href always
        maps to the same permanent metadata, so there's no meaningful "conflict" to
        resolve - INSERT OR REPLACE is used to handle both the first write and any
        harmless repeat write of identical data.
        """
        self.__c.execute(
            f'INSERT OR REPLACE INTO {self.__table_name}(href, titles, year, genre) '
            'VALUES (?,?,?,?)',
            (href, json.dumps(titles), year, genre)
        )
        self.__connection.commit()
