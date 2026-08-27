# Netflix CSFD Finder

A small CLI script that finds movies and TV shows currently on Netflix (Czech Republic) that a given csfd.cz user hasn't rated yet, and looks up csfd's audience rating for each of them.

## How it works

1. Fetches the full Netflix CZ catalogue (movies + TV, `flatrate` availability only) from TMDB's Discover API, paging through all results.
2. Scrapes the given csfd.cz user's rated ("seen") titles, across all pages of their ratings, including every alternate title translation listed for each film. A title only counts as seen if the title string matches AND the release year matches (when both are known) - this avoids mistaking a same-titled remake from a different year for one the user already rated. The list of rated films itself is always re-fetched (it can grow between runs), but each individual film's title/year/genre is cached permanently in `movies.db` the first time it's scraped - by any user, in any run - since that data never changes, so re-running the script (even for a different csfd username) doesn't re-scrape films it already knows about.
3. For every Netflix title that isn't found among the user's rated titles, searches csfd.cz for it and scrapes csfd's aggregate audience-rating percentage - unless a percentage for that exact title/year was already fetched within the last 180 days, in which case the cached value in `movies.db` is reused instead of hitting csfd again (ratings drift over time as more people rate a film, so anything older than that is refetched).
4. Saves the results to a SQLite database and a CSV file.

csfd.cz is scraped with `curl_cffi` impersonating Chrome's TLS fingerprint (plain `requests` gets blocked by csfd's bot detection), combined with BeautifulSoup for HTML parsing. A short random delay is added between csfd requests to avoid hammering the site.

## Requirements

- Python 3
- A free TMDB API key: https://www.themoviedb.org/settings/api

## Setup

```bash
pip install -r requirements.txt
export TMDB_API_KEY=your_tmdb_api_key
```

## Usage

```bash
python main.py <csfd_username>
```

The csfd.cz username can also be omitted, in which case you'll be prompted for it interactively:

```bash
python main.py
```

Passing it as an argument makes the script non-interactive, so it can be run unattended - e.g. from cron, to periodically re-check for newly-added Netflix titles and refresh any percentage older than 180 days:

```cron
0 9 * * 0 cd /path/to/Netflix_Csfd_Finder && TMDB_API_KEY=... /path/to/python main.py your_csfd_username >> cron.log 2>&1
```

The run can take a while, since every Netflix title not already rated by the user requires its own csfd.cz search-and-lookup request (unless its percentage is still cached - see below). Progress is logged to the console as it goes.

## Output

- **`movies.db`** (SQLite) — two tables:
  - `netflix`: every Netflix CZ title found (movies and TV), each row containing `title`, `year`, `category`, a `seen` flag (whether the given user has rated it), `percentage` (csfd's rating, only populated for unseen titles), and `percentage_checked_at` (when that percentage was last fetched, used for the 180-day cache). Rows persist across runs - an existing row is updated in place rather than the table being wiped, which is what makes the percentage cache possible.
  - `csfd_films`: a permanent cache of `title`/`year`/`genre` per csfd film URL (see point 2 above) - unlike `netflix`, this has no staleness window and is never expected to need refreshing.
- **`movies_not_seen_on_csfd.csv`** (UTF-16 encoded) — only the titles the user hasn't rated yet, with columns `title`, `year`, `category`, `percentage`.
- **`netflix_csfd_finder.log`** — a log of the run (also printed to the console), including titles skipped because no confident csfd match was found, cached-percentage reuse, and a full traceback if the run fails.

### Querying `movies.db`

`movies.db` is a plain SQLite database, so it can be inspected with the `sqlite3` CLI (usually already installed) or any SQLite browser/library, without running the script again. For example:

```bash
# Open an interactive shell on the database
sqlite3 movies.db

# Unseen titles, best-rated first
sqlite3 movies.db "SELECT title, year, percentage FROM netflix WHERE seen = 0 ORDER BY percentage DESC;"

# Everything the given user has already rated
sqlite3 movies.db "SELECT title, year FROM netflix WHERE seen = 1;"

# How stale each cached percentage is
sqlite3 movies.db "SELECT title, percentage, percentage_checked_at FROM netflix WHERE percentage IS NOT NULL;"
```

Or from Python, using the standard library directly (no need to go through `Movies` in `my_database.py`, which is just a thin insert/upsert helper used by `main.py`):

```python
import sqlite3
conn = sqlite3.connect('movies.db')
rows = conn.execute('SELECT title, year, percentage FROM netflix WHERE seen = 0 ORDER BY percentage DESC').fetchall()
```

Since the `netflix` table is rewritten (upserted) on every run based on the current TMDB catalogue, a title that leaves Netflix will stop being updated but its old row won't be deleted automatically - if you want the table to only ever reflect the current catalogue, delete `movies.db` before a run to start fresh. Note that this also wipes both caches: the next run will refetch every unseen title's percentage, *and* re-scrape every rated film's detail page from scratch (the `csfd_films` cache is normally the bigger time-saver of the two, since it covers the user's entire rated-titles list, not just the unseen ones).

## Testing

```bash
python -m unittest discover -v
```

Regression tests under `tests/` lock in how the csfd.cz/TMDB HTML and JSON parsing functions behave today (`test_parsing.py`, using real trimmed HTML fixtures under `tests/fixtures/`), so a future change to either site's markup is more likely to show up as a failing test instead of a silent break, plus the caching/matching/upsert logic (`test_caching.py`) against a temporary SQLite database.

## Known limitations

- This script scrapes both TMDB (via its official API) and csfd.cz (via HTML scraping). csfd.cz has no public API and its bot detection and page structure can change at any time, which can break the scraping logic without warning - that's what the test suite above is there to catch.
- A csfd film title that starts with `(` (csfd's convention for some alternate/original titles) is deliberately ignored when matching, per csfd's own listing quirks.
- Title matching between TMDB and csfd requires an exact string match against csfd's listed title translations (plus a year check when both sides know the year), so a differing translation/transliteration can still produce a false negative (a title showing up as "not seen" even though the user rated it under a different translation).
