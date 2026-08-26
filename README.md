# Netflix CSFD Finder

A small CLI script that finds movies and TV shows currently on Netflix (Czech Republic) that a given csfd.cz user hasn't rated yet, and looks up csfd's audience rating for each of them.

## How it works

1. Fetches the full Netflix CZ catalogue (movies + TV, `flatrate` availability only) from TMDB's Discover API, paging through all results.
2. Scrapes the given csfd.cz user's rated ("seen") titles, across all pages of their ratings, including every alternate title translation listed for each film.
3. For every Netflix title that isn't found among the user's rated titles, searches csfd.cz for it and scrapes csfd's aggregate audience-rating percentage.
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
python main.py
```

The script will prompt you for one thing:

- **csfd user**: the csfd.cz username to compare the Netflix catalogue against.

The run can take a while, since every Netflix title not already rated by the user requires its own csfd.cz search-and-lookup request. Progress is logged to the console as it goes.

## Output

- **`movies.db`** (SQLite) — a `netflix` table with every Netflix CZ title found (movies and TV), each row containing `title`, `year`, `category`, a `seen` flag (whether the given user has rated it), and `percentage` (csfd's rating, only populated for unseen titles). The table is dropped and recreated on every run.
- **`movies_not_seen_on_csfd.csv`** (UTF-16 encoded) — only the titles the user hasn't rated yet, with columns `title`, `year`, `category`, `percentage`.
- **`netflix_csfd_finder.log`** — a log of the run (also printed to the console), including titles skipped because no confident csfd match was found, and a full traceback if the run fails.

## Known limitations

- This script scrapes both TMDB (via its official API) and csfd.cz (via HTML scraping). csfd.cz has no public API and its bot detection and page structure can change at any time, which can break the scraping logic without warning.
- A csfd film title that starts with `(` (csfd's convention for some alternate/original titles) is deliberately ignored when matching, per csfd's own listing quirks.
- Title matching between TMDB and csfd is done by exact string comparison against csfd's listed title translations, so differing translations/transliterations can produce false negatives (a title showing up as "not seen" even though the user rated it under a different translation).
