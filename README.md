# Indie Logbook

Indie Logbook is a small, file-backed Flask website for a farm journal and local field log. It serves:

- a home page with recent notes and quick weather/bird summaries
- a blog archive of Markdown-based posts
- a weather log page
- a birds log page
- simple ingest endpoints for appending new weather or bird records

The site is intentionally lightweight: there is no database. Content lives in flat files under the `content/` directory.

## Project structure

- `app.py` – Flask app, content loaders, markdown rendering, routes, and ingest endpoints
- `templates/` – Jinja templates for the home page, blog pages, weather page, birds page, and error pages
- `static/` – CSS and other static assets
- `content/blog/posts/` – blog posts stored as folders containing `index.md`
- `content/weather/weather.jsonl` – weather observations in JSON Lines format
- `content/birds/birds.jsonl` – bird observations in JSON Lines format
- `test_app.py` – integration-style tests for the main routes and ingest flow

## Requirements

- Python 3.10+
- pip
- a local virtual environment is recommended

## Setup

1. Change into the project directory:

   ```bash
   cd /path/to/martim
   ```

2. Create and activate a virtual environment:

   ```bash
   python -m venv .venv
   source .venv/bin/activate
   ```

   On Windows PowerShell, use:

   ```powershell
   .\.venv\Scripts\Activate.ps1
   ```

3. Install dependencies:

   ```bash
   pip install -r requirements.txt
   ```

4. Create a local environment file:

   ```bash
   cp .env.example .env
   ```

   Then edit `.env` and set at least:

   ```env
   INGEST_TOKEN=replace-with-a-secret-token
   PORT=8000
   HOST=127.0.0.1
   ```

5. Run the app:

   ```bash
   python app.py
   ```

   The site will be available at:

   - http://127.0.0.1:8000/

## Content model

### Blog posts

Each post is a folder inside `content/blog/posts/` with an `index.md` file. The app reads the folder name as the slug, unless the Markdown frontmatter provides a different title/date/tags.

Example post structure:

```text
content/blog/posts/2026-08-02-harvest-update/
  index.md
  images/
```

Example `index.md`:

```md
---
title: Harvest update
date: 2026-08-02
tags: orchard, harvest
thumbnail: images/01-harvest.jpg
---

The orchard is producing well this week.

![Harvest view](images/01-harvest.jpg)
```

Supported frontmatter keys:

- `title`
- `date` (expected format `YYYY-MM-DD`)
- `tags` (comma-separated)
- `thumbnail` (for example `images/01-harvest.jpg`)

### Weather data

Weather observations are stored in JSON Lines format in `content/weather/weather.jsonl`.

Each line should be a JSON object like:

```json
{"date": "2026-08-02", "precip_mm": 1.2, "temp_c_min": 10.5, "temp_c_max": 21.0, "conditions": "Cloudy afternoon"}
```

### Bird data

Bird sightings are stored in `content/birds/birds.jsonl`.

Each line should be a JSON object like:

```json
{"date": "2026-08-02", "species": ["Eurasian Blackbird", "European Robin"], "count": 2}
```

## Updating data with curl

The app exposes authenticated ingest endpoints for weather and birds.

Set the token from your `.env` file in the shell:

```bash
export INGEST_TOKEN=$(grep INGEST_TOKEN .env | cut -d= -f2-)
```

### Ingest weather data

```bash
curl -X POST http://127.0.0.1:8000/ingest/weather \
  -H "Content-Type: application/json" \
  -H "X-Auth-Token: $INGEST_TOKEN" \
  -d '{"date":"2026-08-02","precip_mm":1.2,"temp_c_min":10.5,"temp_c_max":21.0,"conditions":"Cloudy afternoon"}'
```

### Ingest bird data

```bash
curl -X POST http://127.0.0.1:8000/ingest/birds \
  -H "Content-Type: application/json" \
  -H "X-Auth-Token: $INGEST_TOKEN" \
  -d '{"date":"2026-08-02","species":["Eurasian Blackbird","European Robin"]}'
```

The birds endpoint also accepts plain text bodies for simple checklist uploads, but the JSON form above is the most explicit.

## Routes and pages

The app exposes these main routes:

- `/` – home page
- `/blog` – blog archive list with tag/year filtering
- `/blog/<slug>` – individual blog post page
- `/weather` – weather log page
- `/birds` – bird log and analytics page
- `/robots.txt`, `/llms.txt`, `/sitemap.xml` – machine-readable site metadata
- `/ingest/weather` – authenticated weather ingest endpoint
- `/ingest/birds` – authenticated bird ingest endpoint

## Testing

The test suite uses the built-in `unittest` runner:

```bash
python -m unittest -v test_app.py
```

## Production-style run

For a more production-oriented start, the app can be served with Gunicorn:

```bash
gunicorn app:app --bind 0.0.0.0:8000
```

This project does not require a build step. The Flask app serves the templates and static files directly.

## Notes

- The app is intentionally simple and file-based.
- If you want to change the site layout or add new sections, start with the templates in `templates/` and the route logic in `app.py`.
- If you want to add content, prefer editing the flat files in `content/` rather than touching the application logic.
