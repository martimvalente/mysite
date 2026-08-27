# CapitalDoTrabalho

CapitalDoTrabalho is a small, file-backed Flask website for a farm-and-forge journal at Póvoa de Baixo, Beduído, Estarreja, Portugal.

## What this is

There is no database, no build step, no JS framework, no CSS library, and no 3rd-party scripts anywhere on the site — not even for analytics. Everything the site shows lives in one of two places:

- **Flat files under `content/`** — Markdown blog posts, a hand-logged rain-gauge diary, an auto-saved weather station archive, and a bird species catalog. These are what you edit to change what the site says.
- **Plain Python data in `app.py`** — the agricultural services grid and the forge products row (`SERVICES` and `FORGE_PRODUCTS`), since they change rarely enough that a template loop over a Python list is simpler than a content file.

The one thing that isn't a flat file is live weather: `/weather` and the home page pull real numbers from the free [Open-Meteo](https://open-meteo.com/) API. Every request polls the API for only the newest point and folds it into `content/weather/station_daily.jsonl` / `station_hourly.jsonl` — everything older on the charts and tables is read back out of those files, not re-fetched. See [Weather data](#weather-data) below for the full picture.

The front end is one hand-written stylesheet (`static/style.css`) plus a handful of small, inline, vanilla-JS snippets (a mobile menu built from a checkbox and CSS, a lazy-loaded data table, an image skeleton placeholder). Nothing is bundled or transpiled — what you see in the template files is exactly what ships.

## Quick start

**Requirements:** Python 3.10+, pip. A virtual environment is recommended. Outbound internet access is needed for live weather data; everything else works fully offline.

1. ```bash
   cd /path/to/mysite
   python -m venv .venv
   source .venv/bin/activate      # Windows: .venv\Scripts\Activate.ps1
   pip install -r requirements.txt
   ```

2. Create your local environment file and edit it:

   ```bash
   cp .env.example .env
   ```

   At minimum, set a real `INGEST_TOKEN` — without one, the ingest endpoints refuse all writes (see [Security](#security)):

   ```env
   INGEST_TOKEN=some-long-random-string
   PORT=8000
   HOST=127.0.0.1
   # CONTACT_PHONE=+351 912 345 678   # optional, shows a clickable tel: link
   ```

3. Run it:

   ```bash
   python app.py
   ```

   The site is now at **http://127.0.0.1:8000/**.

## Project structure

```
app.py                          Flask app: routes, content loaders, markdown renderer,
                                 Open-Meteo integration, validation, ingest endpoints
templates/                      Jinja templates (see below)
static/style.css                the entire stylesheet
static/                         images, favicon, author mark
content/blog/posts/<slug>/      one folder per blog post
content/weather/weather.jsonl   hand-logged rain-gauge readings (you write this)
content/weather/station_*.jsonl auto-saved Open-Meteo archive (the app writes this)
content/birds/birds.jsonl       bird species catalog (you write this)
content/analytics/pageviews.jsonl  first-party pageview log (the app writes this)
```

Templates, one per page, all extending `base.html` (the shared header/sidebar/footer shell):

| Template | Used for |
|---|---|
| `base.html` | Shared layout: nav, sidebar, footer. Not rendered directly. |
| `home.html` | `/` |
| `blog_list.html` | `/blog` |
| `blog_post.html` | `/blog/<slug>` |
| `weather.html` | `/weather` |
| `birds.html` | `/birds` |
| `thank_you.html` | `/thank-you` |
| `404.html`, `500.html`, `error.html` | Error pages (see [Security](#security)) |

## How to add a new blog post

1. Create a folder under `content/blog/posts/`. The folder name becomes the URL slug, so keep it lowercase with hyphens, e.g. `content/blog/posts/2026-09-01-second-cut/`.
2. Add an `index.md` file inside it with frontmatter and a Markdown body:

   ```md
   ---
   title: Second cut
   date: 2026-09-01
   category: Field Notes
   thumbnail: images/01-field.jpg
   ---

   The second cut went faster than the first — drier ground, fewer stops.

   - Started Tuesday morning
   - Finished by Thursday
   - One dripper still needs replacing

   > Worth remembering for next year: don't cut right after the tank refill.
   ```

3. (Optional) Add an `images/` subfolder next to `index.md` for any photos. Reference them in Markdown as `![alt text](images/filename.jpg)` — the app rewrites that path automatically. If you set `thumbnail: images/filename.jpg` in the frontmatter, that image is used as the post's thumbnail everywhere (home, blog list, sidebar). If you don't set one, the app uses the first image it finds in `images/`; if there's no `images/` folder at all, the post just has no thumbnail.
4. Restart isn't needed if you're running with the reloader (`python app.py` in debug mode); otherwise refresh after the process picks up the new file. Posts are discovered automatically — there's no index to update by hand.

**Frontmatter fields:**

| Field | Required | Notes |
|---|---|---|
| `title` | No | Falls back to the slug, title-cased, if omitted. |
| `date` | No | `YYYY-MM-DD`. Falls back to a `YYYY-MM-DD-` prefix on the folder name, then to `1970-01-01`. Controls sort order and the year archive. |
| `category` | No | A single category (e.g. `Field Notes`, `Forge`, `Weather Watch`). Falls back to `Field Notes`. Drives the sidebar category filter and `/blog?category=`. |
| `thumbnail` | No | A path starting with `images/`, relative to the post folder. |

**Markdown support** is a small, hand-written converter (`markdown_to_html` in `app.py`), not a full CommonMark implementation. It handles:

- Headers `#` through `######`
- Bold `**text**`, italic `*text*`, inline code `` `code` ``
- Links `[text](url)` and images `![alt](url)`
- Unordered (`-` or `*`) and ordered (`1.`) lists — one level deep, no nesting
- Blockquotes (`>`)
- Fenced code blocks (` ``` `)

It does **not** support tables, nested lists, or footnotes. Since post bodies are rendered with Jinja's `| safe` filter, you can also drop raw HTML directly into a post if you need something the converter doesn't handle — just remember that anything in `content/blog/posts/` is trusted, unescaped output.

Posts are grouped by year in the sidebar and on `/blog` (each year is collapsible), and filterable with `/blog?category=Forge` or `/blog?year=2026`.

## Weather data

There are two separate weather datasets, and they mean different things:

### 1. Your rain gauge (`content/weather/weather.jsonl`) — you enter this

This is the manually-logged table on `/weather`. It ships empty. Add a reading with `POST /ingest/weather` (see [API reference](#api-reference)):

```bash
export INGEST_TOKEN=$(grep INGEST_TOKEN .env | cut -d= -f2-)

curl -X POST http://127.0.0.1:8000/ingest/weather \
  -H "Content-Type: application/json" \
  -H "X-Auth-Token: $INGEST_TOKEN" \
  -d '{"date":"2026-09-01","precip_mm":1.2,"temp_c_min":10.5,"temp_c_max":21.0,"conditions":"Cloudy afternoon"}'
```

There's no bulk-import tool — if you have a backlog of paper readings, the fastest way in is a short loop of `curl` calls, or appending well-formed JSON lines to the file directly (each line is one independent JSON object).

### 2. The station archive (`content/weather/station_daily.jsonl`, `station_hourly.jsonl`) — the app writes this

This is what powers the temperature/precipitation charts and the "detailed station data" table. **Don't hand-edit these** — every request to `/weather` or `/weather/meteo.json` polls Open-Meteo for just the newest point and appends it here automatically; anything older is simply read back from the file rather than re-fetched. If you want to change *where* the station reads from, edit `METEO_LAT` / `METEO_LON` / `METEO_TIMEZONE` near the top of `app.py` — the current coordinates (40.76427, -8.5611) resolve to Beduído, the closest point a free geocoder could match to Póvoa de Baixo.

## How to add a bird species

Species are stored one-per-line in `content/birds/birds.jsonl`. Add one with `POST /ingest/birds`:

```bash
curl -X POST http://127.0.0.1:8000/ingest/birds \
  -H "Content-Type: application/json" \
  -H "X-Auth-Token: $INGEST_TOKEN" \
  -d '{"common_name":"European Robin","scientific_name":"Erithacus rubecula","description":"Turns up around the compost heap.","wikipedia_url":"https://en.wikipedia.org/wiki/European_robin"}'
```

Drop a photo into `static/birds/` first if you have one, then pass its path relative to `static/` as `"image":"birds/robin.jpg"`. No `image` field means the catalog just shows a plain placeholder instead of a photo. `date_added` defaults to today if you leave it out.

## API reference

All routes are unauthenticated `GET` requests unless noted otherwise. All responses are HTML unless noted otherwise.

| Route | Method | Auth | Description |
|---|---|---|---|
| `/` | GET | — | Home page. |
| `/blog` | GET | — | Blog archive. Query params: `category`, `year` (either, not both meaningfully combined). |
| `/blog/<slug>` | GET | — | A single post. 404 if the slug doesn't exist. |
| `/blog/<slug>/images/<filename>` | GET | — | Serves an image from that post's `images/` folder. |
| `/weather` | GET | — | Rain gauge table, temperature/precipitation charts, station data. |
| `/weather/meteo.json` | GET | — | JSON used by the lazy-loaded detailed table. Not meant for direct browsing; disallowed in `robots.txt`. |
| `/birds` | GET | — | Bird species catalog. |
| `/thank-you` | GET | — | Standalone thank-you page. Not linked from anywhere yet. |
| `/robots.txt`, `/sitemap.xml`, `/llms.txt` | GET | — | Machine-readable metadata. |
| `/ingest/weather` | POST | `X-Auth-Token` header | Append a rain-gauge reading. See below. |
| `/ingest/birds` | POST | `X-Auth-Token` header | Append a species entry. See below. |

Every non-GET, non-listed path, or bad method returns a branded 404/405 rather than a default server error page.

### `POST /ingest/weather`

Header: `X-Auth-Token: <INGEST_TOKEN>`. Body: JSON object.

| Field | Type | Required | Constraint |
|---|---|---|---|
| `date` | string | yes | `YYYY-MM-DD` |
| `precip_mm` | number | yes | `0`–`500` |
| `temp_c_min` | number | no | `-30`–`55`; if both set, must be ≤ `temp_c_max` |
| `temp_c_max` | number | no | `-30`–`55` |
| `conditions` | string | no | ≤ 120 characters |

Any field not in this table is silently dropped, not stored. Responses: `201` on success, `400` on a missing/invalid field, `401` on a missing/wrong token, `503` if `INGEST_TOKEN` isn't configured at all.

### `POST /ingest/birds`

Header: `X-Auth-Token: <INGEST_TOKEN>`. Body: JSON object.

| Field | Type | Required | Constraint |
|---|---|---|---|
| `common_name` | string | yes | ≤ 100 characters |
| `scientific_name` | string | no | ≤ 100 characters |
| `description` | string | no | ≤ 500 characters |
| `image` | string | no | ≤ 200 characters, path relative to `static/` |
| `wikipedia_url` | string | no | must start with `http://` or `https://` |
| `date_added` | string | no | `YYYY-MM-DD`; defaults to today |

Same response codes as above.

## Security

- **No default token.** `INGEST_TOKEN` has no fallback value. If it's not set, both ingest endpoints return `503` rather than accept writes against a guessable default.
- **Field validation.** Both ingest endpoints allow-list every field and validate its type, range, and length (see the tables above). Unknown fields are dropped, not stored; malformed values are rejected with `400`.
- **No stack traces reach the browser.** Flask's debug mode is off unless `FLASK_DEBUG=1` is explicitly set — never do that anywhere but your own machine, since it enables an interactive, code-executing debugger in the browser. Every error path returns a short, generic message; the real exception goes to the server log only.
- **Custom error pages** for 400/401/403/404/405/413/429/500, all with the same safe, generic copy — none of them echo back request details.
- **No secrets in git history.** Checked: this repo has one commit, and `.env` was never in it. `.gitignore` covers `.env`, virtualenvs, caches, and OS/editor cruft.
- **What's deliberately not here:** there's a cosmetic `console.log` greeting on every page, but no devtools-blocking or devtools-detection. There's no reliable, unbypassable way to do that in a browser — every known trick degrades the page for legitimate users (including you, debugging your own site) and is trivially defeated.

## Analytics

No 3rd-party analytics, no external script, no tracking pixel, no cookies. `app.py` logs a `{date, path}` line to `content/analytics/pageviews.jsonl` for each successful page view (static assets, the JSON endpoint, and the ingest endpoints are excluded). It's a plain local file — tail it, grep it, or load it into a spreadsheet; nothing leaves the server.

## Contact info

The footer's email is a clickable `mailto:` link. The phone number is a placeholder (`CONTACT_PHONE` in `.env`) — swap it for a real one whenever you have it; leave the variable unset entirely to hide the phone row rather than show a fake number.

## Editing services and forge products

The agricultural services grid and the forge products row are plain Python lists near the top of `app.py` (`SERVICES` and `FORGE_PRODUCTS`). Edit those lists directly to change titles, descriptions, or images — no template changes required.

## Testing

There's no automated test suite yet. If you add one, `python -m unittest -v test_app.py` is the obvious convention to follow given the rest of the stack.

## Production-style run

```bash
gunicorn app:app --bind 0.0.0.0:8000
```

No build step is required either way — Flask serves the templates and static files directly.

## Notes

- The app is intentionally simple and file-based; if in doubt, prefer editing a content file over touching `app.py`.
- Layout and styling live in `templates/` and `static/style.css`; route and data logic live in `app.py`.
