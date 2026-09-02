import os
import re
import json
import time
import hmac
import logging
import urllib.request
import urllib.parse
from pathlib import Path
from functools import wraps
from datetime import datetime, timezone
from flask import Flask, render_template, request, jsonify, send_from_directory, abort, url_for, Response
from werkzeug.exceptions import HTTPException
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 256 * 1024  # 256 KB max payload

# Debug mode (the interactive Werkzeug debugger + full tracebacks) is OFF
# unless explicitly turned on for local development. Never run this with
# FLASK_DEBUG=1 anywhere reachable by anyone but you — the debugger allows
# arbitrary code execution from the browser.
DEBUG = os.environ.get("FLASK_DEBUG", "0") == "1"

logging.basicConfig(level=logging.INFO)

BASE_DIR = Path(__file__).parent.resolve()
CONTENT_DIR = BASE_DIR / "content"
BLOG_DIR = CONTENT_DIR / "blog" / "posts"
WEATHER_FILE = CONTENT_DIR / "weather" / "weather.jsonl"
STATION_DAILY_FILE = CONTENT_DIR / "weather" / "station_daily.jsonl"
STATION_HOURLY_FILE = CONTENT_DIR / "weather" / "station_hourly.jsonl"
BIRDS_FILE = CONTENT_DIR / "birds" / "birds.jsonl"
ANALYTICS_FILE = CONTENT_DIR / "analytics" / "pageviews.jsonl"

# No hardcoded fallback: a guessable default token defeats the whole point
# of auth. If it's not set, the ingest endpoints refuse to accept writes.
INGEST_TOKEN = os.environ.get("INGEST_TOKEN", "").strip()

# --- Site identity ---
BRAND = "CapitalDoTrabalho"
BRAND_TAGLINE = "Field & forge log — Beduído, Estarreja"
SITE_LOCATION = "Póvoa de Baixo, Beduído, Estarreja, Portugal"
CONTACT_EMAIL = "hello@capitaldotrabalho.pt"
# Left blank on purpose — no real phone number was provided yet. Set this to
# a real number (e.g. "+351 912 345 678") to show a clickable tel: link in
# the footer; until then the footer just omits the phone row entirely.
CONTACT_PHONE = os.environ.get("CONTACT_PHONE", "").strip()

# Beduído, Estarreja (Distrito de Aveiro, Portugal) — resolved via Open-Meteo geocoding.
METEO_LAT = 40.76427
METEO_LON = -8.5611
METEO_TIMEZONE = "Europe/Lisbon"
OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"

SLUG_RE = re.compile(r"^[a-z0-9-]+$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n(.*)$", re.DOTALL)
KV_RE = re.compile(r"^(\w+):\s*(.*)$", re.MULTILINE)

DEFAULT_CATEGORY = "Field Notes"

# Services offered — edit this list to change the home page grid.
SERVICES = [
    {"title": "Soil Analysis & Testing", "image": "services/soil-testing.jpg",
     "description": "Lab-grade soil sampling to guide fertiliser and irrigation decisions."},
    {"title": "Crop Spraying & Protection", "image": "services/crop-spraying.jpg",
     "description": "Timed spraying rounds for pest and disease control across the growing season."},
    {"title": "Irrigation & Water Systems", "image": "services/irrigation.jpg",
     "description": "Design, installation and upkeep of drip and sprinkler lines."},
    {"title": "Machinery & Tractor Rental", "image": "services/machinery-rental.jpg",
     "description": "Tractors and implements available by the day or by the season."},
    {"title": "Fence & Field Maintenance", "image": "services/fencing-maintenance.jpg",
     "description": "Boundary fencing, gates and hedge lines kept stock-tight."},
    {"title": "Harvest & Transport Logistics", "image": "services/harvest-logistics.jpg",
     "description": "Coordinated harvest crews and transport to the mill or the market."},
]

# Forge products — edit this list to change the home page row.
FORGE_PRODUCTS = [
    {"name": "Agricultural Hand Tools",
     "description": "Hoes, sickles and pruning tools, forged and tempered for daily field use."},
    {"name": "Gates & Railings",
     "description": "Custom ironwork gates and railings, measured and fitted on site."},
    {"name": "Hearth & Fire Tools",
     "description": "Pokers, stands and braziers, shaped on the anvil."},
    {"name": "Bespoke Commissions",
     "description": "One-off pieces, made to a drawing or a description."},
]

WMO_CODES = {
    0: "Clear sky", 1: "Mainly clear", 2: "Partly cloudy", 3: "Overcast",
    45: "Fog", 48: "Freezing fog",
    51: "Light drizzle", 53: "Drizzle", 55: "Dense drizzle",
    56: "Freezing drizzle", 57: "Freezing drizzle",
    61: "Light rain", 63: "Rain", 65: "Heavy rain",
    66: "Freezing rain", 67: "Freezing rain",
    71: "Light snow", 73: "Snow", 75: "Heavy snow", 77: "Snow grains",
    80: "Light showers", 81: "Showers", 82: "Violent showers",
    85: "Snow showers", 86: "Snow showers",
    95: "Thunderstorm", 96: "Thunderstorm with hail", 99: "Thunderstorm with hail",
}


def weather_code_to_text(code):
    try:
        return WMO_CODES.get(int(code), "—")
    except (TypeError, ValueError):
        return "—"


# --- Auth decorator ---
def require_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not INGEST_TOKEN:
            # Refuse to accept writes rather than fall back to a guessable
            # default token. Set INGEST_TOKEN in .env to enable ingest.
            return jsonify({"error": "Ingest is disabled: no INGEST_TOKEN configured on the server."}), 503
        auth_header = request.headers.get("X-Auth-Token", "")
        if not auth_header or not hmac.compare_digest(auth_header, INGEST_TOKEN):
            return jsonify({"error": "Unauthorized: invalid or missing X-Auth-Token"}), 401
        return f(*args, **kwargs)
    return decorated


# --- Field validation helpers (block tampering: allow-list, type-check,
# range-check every field written by the two ingest endpoints; anything
# outside these bounds is rejected with 400 rather than silently accepted) ---
class ValidationError(ValueError):
    pass


def _clean_number(value, field, minimum, maximum, required=True):
    if value is None:
        if required:
            raise ValidationError(f"Missing required field '{field}'")
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValidationError(f"Field '{field}' must be a number")
    value = float(value)
    if not (minimum <= value <= maximum):
        raise ValidationError(f"Field '{field}' must be between {minimum} and {maximum}")
    return value


def _clean_text(value, field, max_length, required=False):
    if value is None:
        value = ""
    if not isinstance(value, str):
        raise ValidationError(f"Field '{field}' must be a string")
    value = value.strip()
    if required and not value:
        raise ValidationError(f"Missing required field '{field}'")
    if len(value) > max_length:
        raise ValidationError(f"Field '{field}' is too long (max {max_length} characters)")
    return value


def _clean_date(value, field, required=True):
    value = _clean_text(value, field, 10, required=required)
    if value and not DATE_RE.match(value):
        raise ValidationError(f"Field '{field}' must be a date in YYYY-MM-DD format")
    return value


def _clean_url(value, field, max_length=300):
    value = _clean_text(value, field, max_length)
    if value and not (value.startswith("http://") or value.startswith("https://")):
        raise ValidationError(f"Field '{field}' must be a full http(s) URL")
    return value


# --- Simple, robust zero-dependency Markdown to HTML converter ---
def markdown_to_html(md_text, slug=""):
    if not md_text:
        return ""

    # 1. Code blocks
    code_blocks = []

    def save_code_block(match):
        code_blocks.append(match.group(1))
        return f"<!--CODE_BLOCK_{len(code_blocks)-1}-->"

    md = re.sub(r"```.*?\n(.*?)```", save_code_block, md_text, flags=re.DOTALL)

    lines = md.split("\n")
    html_lines = []
    in_list = False
    list_type = None

    for line in lines:
        stripped = line.strip()

        ul_match = re.match(r"^[\*\-]\s+(.*)$", stripped)
        ol_match = re.match(r"^\d+\.\s+(.*)$", stripped)

        if ul_match or ol_match:
            item_content = ul_match.group(1) if ul_match else ol_match.group(1)
            target_list_type = "ul" if ul_match else "ol"
            if not in_list:
                in_list = True
                list_type = target_list_type
                html_lines.append(f"<{list_type}>")
            elif list_type != target_list_type:
                html_lines.append(f"</{list_type}>")
                list_type = target_list_type
                html_lines.append(f"<{list_type}>")
            html_lines.append(f"<li>{item_content}</li>")
            continue
        else:
            if in_list:
                html_lines.append(f"</{list_type}>")
                in_list = False
                list_type = None

        if not stripped:
            continue

        h_match = re.match(r"^(#{1,6})\s+(.*)$", stripped)
        if h_match:
            level = len(h_match.group(1))
            html_lines.append(f"<h{level}>{h_match.group(2)}</h{level}>")
            continue

        if stripped.startswith(">"):
            quote_text = stripped[1:].strip()
            html_lines.append(f"<blockquote><p>{quote_text}</p></blockquote>")
            continue

        html_lines.append(f"<p>{stripped}</p>")

    if in_list:
        html_lines.append(f"</{list_type}>")

    html = "\n".join(html_lines)

    def replace_img(m):
        alt, src = m.group(1), m.group(2)
        if src.startswith("images/") and slug:
            src = f"/blog/{slug}/{src}"
        return f'<img src="{src}" alt="{alt}" class="markdown-image" loading="lazy" />'

    html = re.sub(r"!\[(.*?)\]\((.*?)\)", replace_img, html)
    html = re.sub(r"\[(.*?)\]\((.*?)\)", r'<a href="\2" target="_blank" rel="noopener">\1</a>', html)
    html = re.sub(r"\*\*(.*?)\*\*", r"<strong>\1</strong>", html)
    html = re.sub(r"\*(.*?)\*", r"<em>\1</em>", html)
    html = re.sub(r"`(.*?)`", r"<code>\1</code>", html)

    for i, code in enumerate(code_blocks):
        escaped_code = code.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        html = html.replace(f"<!--CODE_BLOCK_{i}-->", f"<pre><code>{escaped_code}</code></pre>")

    return html


# --- Blog parsing ---
def get_all_posts():
    posts = []
    if not BLOG_DIR.exists():
        return posts

    for post_dir in BLOG_DIR.iterdir():
        if not post_dir.is_dir():
            continue
        slug = post_dir.name
        if not SLUG_RE.match(slug):
            continue

        index_file = post_dir / "index.md"
        if not index_file.exists():
            continue

        try:
            content = index_file.read_text(encoding="utf-8")
        except Exception:
            continue

        fm_match = FRONTMATTER_RE.match(content)
        fm = {}
        body = content
        if fm_match:
            fm_str = fm_match.group(1)
            body = fm_match.group(2)
            for k, v in KV_RE.findall(fm_str):
                fm[k.lower()] = v.strip()

        title = fm.get("title", slug.replace("-", " ").title())
        date_str = fm.get("date", "")
        if not date_str:
            date_prefix_match = re.match(r"^(\d{4}-\d{2}-\d{2})-", slug)
            date_str = date_prefix_match.group(1) if date_prefix_match else "1970-01-01"

        category = fm.get("category", "").strip() or DEFAULT_CATEGORY

        thumbnail = fm.get("thumbnail", "")
        images_dir = post_dir / "images"
        if thumbnail and thumbnail.startswith("images/"):
            thumbnail_url = f"/blog/{slug}/{thumbnail}"
        elif images_dir.exists() and images_dir.is_dir():
            valid_imgs = sorted([f.name for f in images_dir.iterdir() if f.suffix.lower() in [".jpg", ".jpeg", ".png", ".webp"]])
            thumbnail_url = f"/blog/{slug}/images/{valid_imgs[0]}" if valid_imgs else ""
        else:
            thumbnail_url = ""

        rendered_html = markdown_to_html(body, slug=slug)

        plain_text = re.sub(r"<[^>]+>", "", rendered_html)
        excerpt = plain_text[:160] + "..." if len(plain_text) > 160 else plain_text

        posts.append({
            "slug": slug,
            "title": title,
            "date": date_str,
            "category": category,
            "thumbnail": thumbnail_url,
            "html": rendered_html,
            "excerpt": excerpt,
            "raw_body": body
        })

    posts.sort(key=lambda p: p["date"], reverse=True)
    return posts


def get_post_by_slug(slug):
    if not SLUG_RE.match(slug):
        return None
    for post in get_all_posts():
        if post["slug"] == slug:
            return post
    return None


def get_post_archive_tree(posts):
    """Flat year -> posts map, most recent year first, for the collapsible sidebar/archive."""
    archive = {}
    for p in posts:
        date_str = p.get("date", "1970-01-01")
        year = date_str[:4] if DATE_RE.match(date_str) else "Undated"
        archive.setdefault(year, []).append(p)
    return dict(sorted(archive.items(), reverse=True))


# --- Manual rain-gauge log (weather.jsonl) ---
def get_weather_records():
    records = {}
    if not WEATHER_FILE.exists():
        return []

    with open(WEATHER_FILE, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
                date = data.get("date")
                if date and DATE_RE.match(date):
                    records[date] = data
            except Exception:
                continue

    return [records[k] for k in sorted(records.keys(), reverse=True)]


# --- Bird species catalog (birds.jsonl) ---
def get_bird_catalog():
    catalog = []
    if not BIRDS_FILE.exists():
        return catalog

    with open(BIRDS_FILE, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
            except Exception:
                continue
            if not data.get("common_name"):
                continue
            catalog.append(data)

    catalog.sort(key=lambda b: (b.get("date_added") or ""), reverse=True)
    return catalog


# --- Open-Meteo integration (stdlib-only HTTP, cached) ---
_METEO_CACHE = {}


def _cached(key, ttl_seconds, fetch_fn):
    now = time.time()
    entry = _METEO_CACHE.get(key)
    if entry and (now - entry[0]) < ttl_seconds:
        return entry[1]
    fresh = fetch_fn()
    if fresh is not None:
        _METEO_CACHE[key] = (now, fresh)
        return fresh
    return entry[1] if entry else None


def _http_get_json(url, timeout=6):
    try:
        req = urllib.request.Request(url, headers={"User-Agent": f"{BRAND}/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None


# --- Local station archive ------------------------------------------------
# Every value shown on the weather page comes live from Open-Meteo. Anything
# that has actually happened (a past day, an already-elapsed 3-hour slot) is
# also saved here as it's fetched, so the site keeps a real, local record of
# confirmed readings rather than only ever showing a rolling forecast window.
# Rows that are still in the future are forecasts and are never persisted.

def _upsert_jsonl_records(path, records, key_field):
    if not records:
        return
    existing = {}
    if path.exists():
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except Exception:
                    continue
                k = rec.get(key_field)
                if k:
                    existing[k] = rec
    for rec in records:
        k = rec.get(key_field)
        if k:
            existing[k] = rec
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for k in sorted(existing.keys()):
            f.write(json.dumps(existing[k]) + "\n")


def _read_jsonl_dict(path, key_field):
    records = {}
    if not path.exists():
        return records
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except Exception:
                continue
            k = rec.get(key_field)
            if k:
                records[k] = rec
    return records


def get_station_daily_archive():
    """Locally saved, confirmed daily readings (never includes forecast rows)."""
    records = _read_jsonl_dict(STATION_DAILY_FILE, "date")
    return [records[k] for k in sorted(records.keys())]


def get_station_hourly_archive():
    """Locally saved, confirmed 3-hourly readings (never includes forecast rows)."""
    records = _read_jsonl_dict(STATION_HOURLY_FILE, "time")
    return [records[k] for k in sorted(records.keys())]


def _fetch_live_meteo():
    params = urllib.parse.urlencode({
        "latitude": METEO_LAT,
        "longitude": METEO_LON,
        "current": "temperature_2m,precipitation,weather_code,relative_humidity_2m,wind_speed_10m",
        "daily": "precipitation_sum,temperature_2m_max,temperature_2m_min",
        "timezone": METEO_TIMEZONE,
        "forecast_days": 1,
    })
    data = _http_get_json(f"{OPEN_METEO_URL}?{params}")
    if not data:
        return None
    current = data.get("current", {}) or {}
    daily = data.get("daily", {}) or {}
    return {
        "temp_now": current.get("temperature_2m"),
        "condition": weather_code_to_text(current.get("weather_code")),
        "humidity": current.get("relative_humidity_2m"),
        "wind": current.get("wind_speed_10m"),
        "rain_today": (daily.get("precipitation_sum") or [None])[0],
        "temp_max_today": (daily.get("temperature_2m_max") or [None])[0],
        "temp_min_today": (daily.get("temperature_2m_min") or [None])[0],
        "fetched_at": current.get("time"),
    }


def _poll_latest_daily():
    """Pull just today's running daily summary from Open-Meteo and fold it
    into the local archive. This is the only API call the daily chart/table
    ever needs — every earlier day is already sitting in station_daily.jsonl
    from a previous poll, so it's read from disk, not re-fetched."""
    params = urllib.parse.urlencode({
        "latitude": METEO_LAT,
        "longitude": METEO_LON,
        "daily": "precipitation_sum,temperature_2m_max,temperature_2m_min",
        "timezone": METEO_TIMEZONE,
        "forecast_days": 1,
    })
    data = _http_get_json(f"{OPEN_METEO_URL}?{params}")
    if not data:
        return None
    d = data.get("daily", {}) or {}
    times = d.get("time", [])
    if not times:
        return None
    record = {
        "date": times[0],
        "precip": (d.get("precipitation_sum") or [None])[0],
        "temp_max": (d.get("temperature_2m_max") or [None])[0],
        "temp_min": (d.get("temperature_2m_min") or [None])[0],
    }
    _upsert_jsonl_records(STATION_DAILY_FILE, [record], "date")
    return record


def _poll_latest_hourly():
    """Pull the current day's 3-hourly slots that have already elapsed and
    fold them into the local archive. Same idea as above: one small request
    for what's new, everything older is read back from the saved file."""
    params = urllib.parse.urlencode({
        "latitude": METEO_LAT,
        "longitude": METEO_LON,
        "hourly": "temperature_2m,precipitation,relative_humidity_2m,wind_speed_10m,weather_code,surface_pressure,cloud_cover",
        "timezone": METEO_TIMEZONE,
        "forecast_days": 1,
    })
    data = _http_get_json(f"{OPEN_METEO_URL}?{params}")
    if not data:
        return None
    h = data.get("hourly", {}) or {}
    times = h.get("time", [])

    def col(name):
        return h.get(name, [])

    temp, precip = col("temperature_2m"), col("precipitation")
    humidity, wind = col("relative_humidity_2m"), col("wind_speed_10m")
    pressure, clouds, code = col("surface_pressure"), col("cloud_cover"), col("weather_code")

    # Comparing UTC "now" against timestamps expressed in METEO_TIMEZONE can be
    # off by an hour around the DST boundary — an acceptable trade-off for a
    # personal station log, kept stdlib-only rather than adding a tz database.
    cutoff = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:00")

    rows = []
    for i, t in enumerate(times):
        try:
            hour = int(t[11:13])
        except (ValueError, IndexError):
            continue
        if hour % 3 != 0 or t > cutoff:
            continue
        rows.append({
            "time": t,
            "temp": temp[i] if i < len(temp) else None,
            "precip": precip[i] if i < len(precip) else None,
            "humidity": humidity[i] if i < len(humidity) else None,
            "wind": wind[i] if i < len(wind) else None,
            "pressure": pressure[i] if i < len(pressure) else None,
            "clouds": clouds[i] if i < len(clouds) else None,
            "condition": weather_code_to_text(code[i]) if i < len(code) else "—",
        })

    _upsert_jsonl_records(STATION_HOURLY_FILE, rows, "time")
    return rows


# --- SVG chart geometry helpers (server-rendered, no client-side libraries) ---
# Charts are drawn entirely from the local archive (station_daily.jsonl) —
# real, confirmed readings, no forecast guesswork mixed in.
def build_temp_chart(daily_series, width=640, height=180, pad=28):
    if not daily_series:
        return None
    valid = [d for d in daily_series if d.get("temp_max") is not None and d.get("temp_min") is not None]
    if len(valid) < 2:
        return None

    all_vals = [d["temp_max"] for d in valid] + [d["temp_min"] for d in valid]
    vmin, vmax = min(all_vals), max(all_vals)
    if vmax == vmin:
        vmax = vmin + 1

    n = len(valid)
    step = (width - 2 * pad) / max(n - 1, 1)

    def y(v):
        return round(height - pad - ((v - vmin) / (vmax - vmin)) * (height - 2 * pad), 1)

    xs = [round(pad + i * step, 1) for i in range(n)]
    max_pts = [f"{xs[i]},{y(valid[i]['temp_max'])}" for i in range(n)]
    min_pts = [f"{xs[i]},{y(valid[i]['temp_min'])}" for i in range(n)]

    labels = [{
        "x": xs[i], "date": valid[i]["date"][5:],
        "show": (i % 3 == 0) or (i == n - 1),
    } for i in range(n)]

    return {
        "width": width, "height": height, "pad": pad,
        "max_line": " ".join(max_pts),
        "min_line": " ".join(min_pts),
        "labels": labels,
        "vmax": round(vmax, 1), "vmin": round(vmin, 1),
        "baseline_y": height - pad,
    }


def build_precip_chart(daily_series, width=640, height=180, pad=28):
    if not daily_series:
        return None
    valid = [d for d in daily_series if d.get("precip") is not None]
    if not valid:
        return None

    n = len(valid)
    maxv = max([d["precip"] for d in valid] + [5.0])
    slot = (width - 2 * pad) / n
    bar_w = max(slot - 4, 2)

    bars = []
    for i, d in enumerate(valid):
        x = round(pad + i * slot + (slot - bar_w) / 2, 1)
        val = d["precip"] or 0
        raw_h = (val / maxv) * (height - 2 * pad - 16) if maxv else 0
        bar_h = round(max(raw_h, 1.5) if val > 0 else 1, 1)
        bars.append({
            "x": x, "width": round(bar_w, 1), "height": bar_h,
            "y": round(height - pad - bar_h, 1),
            "date": d["date"][5:], "value": val,
            "show_label": (i % 3 == 0) or (i == n - 1),
        })

    return {
        "width": width, "height": height, "pad": pad,
        "bars": bars, "max": round(maxv, 1),
        "baseline_y": height - pad,
    }


@app.context_processor
def inject_global_sidebar():
    posts = get_all_posts()
    archive_tree = get_post_archive_tree(posts)

    category_counts = {}
    for p in posts:
        category_counts[p["category"]] = category_counts.get(p["category"], 0) + 1
    sidebar_categories = sorted(category_counts.items())

    weather_records = get_weather_records()
    latest_weather = weather_records[0] if weather_records else None

    # Cached site-wide (900s TTL, shared with anywhere else that wants it) so
    # the sidebar's "Right Now" panel can show live conditions on every page
    # without hitting Open-Meteo on every request.
    live_meteo = _cached("live_meteo", 900, _fetch_live_meteo)

    return {
        "brand_name": BRAND,
        "brand_tagline": BRAND_TAGLINE,
        "site_location": SITE_LOCATION,
        "contact_email": CONTACT_EMAIL,
        "contact_phone": CONTACT_PHONE,
        "contact_phone_dial": re.sub(r"[^0-9+]", "", CONTACT_PHONE) if CONTACT_PHONE else "",
        "current_year": datetime.now(timezone.utc).year,
        "sidebar_archive_tree": archive_tree,
        "sidebar_categories": sidebar_categories,
        "sidebar_latest_weather": latest_weather,
        "sidebar_live_weather": live_meteo,
        "sidebar_species_count": len(get_bird_catalog()),
        "total_posts_count": len(posts),
    }


# --- First-party analytics -------------------------------------------
# No 3rd party, no script, no cookies, no client-side tracking at all: just
# a page-view counter appended server-side to a local file. Static assets,
# the JSON endpoint and the ingest endpoints are excluded.
_ANALYTICS_SKIP_PREFIXES = ("/static/", "/weather/meteo.json", "/ingest/")


@app.after_request
def _log_pageview(response):
    try:
        path = request.path
        if (request.method == "GET"
                and response.status_code < 400
                and not any(path.startswith(p) for p in _ANALYTICS_SKIP_PREFIXES)):
            ANALYTICS_FILE.parent.mkdir(parents=True, exist_ok=True)
            with open(ANALYTICS_FILE, "a", encoding="utf-8") as f:
                f.write(json.dumps({
                    "date": datetime.now(timezone.utc).date().isoformat(),
                    "path": path,
                }) + "\n")
    except Exception:
        app.logger.exception("pageview logging failed")
    return response


# --- ROUTES ---

@app.route("/")
def home():
    posts = get_all_posts()
    latest_posts = posts[:4]
    bird_count = len(get_bird_catalog())

    return render_template(
        "home.html",
        latest_posts=latest_posts,
        bird_count=bird_count,
        services=SERVICES,
        forge_products=FORGE_PRODUCTS,
    )


@app.route("/blog")
def blog_list():
    category_filter = request.args.get("category", "").strip()
    year_filter = request.args.get("year", "").strip()
    posts = get_all_posts()

    if category_filter:
        posts = [p for p in posts if p["category"].lower() == category_filter.lower()]

    if year_filter:
        posts = [p for p in posts if p["date"].startswith(year_filter)]

    grouped_posts = {}
    for p in posts:
        yr = p["date"][:4] if p.get("date") else "Undated"
        grouped_posts.setdefault(yr, []).append(p)
    grouped_posts = dict(sorted(grouped_posts.items(), reverse=True))

    return render_template(
        "blog_list.html",
        posts=posts,
        grouped_posts=grouped_posts,
        selected_category=category_filter,
        selected_year=year_filter,
    )


@app.route("/blog/<slug>")
def blog_post(slug):
    post = get_post_by_slug(slug)
    if not post:
        abort(404)
    return render_template("blog_post.html", post=post)


@app.route("/blog/<slug>/images/<filename>")
def blog_image(slug, filename):
    if not SLUG_RE.match(slug):
        abort(404)
    post_img_dir = BLOG_DIR / slug / "images"
    if not post_img_dir.exists():
        abort(404)
    return send_from_directory(post_img_dir, filename)


@app.route("/weather")
def weather():
    records = get_weather_records()

    # Poll Open-Meteo for just the newest point and fold it into the local
    # archive (throttled by _cached so we're not hitting the API on every
    # request). Everything actually shown comes back out of the archive file.
    _cached("poll_daily", 600, _poll_latest_daily)
    daily_series = get_station_daily_archive()

    temp_chart = build_temp_chart(daily_series) if daily_series else None
    precip_chart = build_precip_chart(daily_series) if daily_series else None

    return render_template(
        "weather.html",
        records=records,
        temp_chart=temp_chart,
        precip_chart=precip_chart,
        meteo_available=bool(daily_series),
    )


@app.route("/weather/meteo.json")
def weather_meteo_json():
    _cached("poll_hourly", 600, _poll_latest_hourly)
    rows = get_station_hourly_archive()[-56:]
    if not rows:
        return jsonify({
            "rows": [],
            "error": "No station readings saved yet — check the connection and reload.",
        })
    return jsonify({"rows": rows})


@app.route("/birds")
def birds():
    catalog = get_bird_catalog()
    return render_template("birds.html", catalog=catalog)


@app.route("/robots.txt")
def robots_txt():
    content = (
        "User-agent: *\n"
        "Allow: /\n"
        "Disallow: /ingest/\n"
        "Disallow: /weather/meteo.json\n"
        "Disallow: /thank-you\n"
        f"Sitemap: {request.host_url}sitemap.xml\n"
    )
    return Response(content, mimetype="text/plain")


@app.route("/llms.txt")
def llms_txt():
    content = (
        f"# {BRAND}\n\n"
        f"> Monolith, file-backed website for a small farm and forge in {SITE_LOCATION}: blog posts, "
        "meteo readings and a catalog of birds seen on the land.\n\n"
        "## Site Structure\n"
        "- [/](http://localhost:8000/): Home — intro, live weather snapshot, agricultural services, forge products, latest posts.\n"
        "- [/blog](http://localhost:8000/blog): Blog posts archive, organised by category and by year.\n"
        "- [/weather](http://localhost:8000/weather): Rain-gauge log plus a detailed 3-hourly meteo station table.\n"
        "- [/birds](http://localhost:8000/birds): Catalog of bird species observed on site.\n\n"
        "## Technical Architecture\n"
        "Monolithic Python Flask server. No database. Content lives in content/ as Markdown and JSON Lines files. "
        "Live weather data is fetched from the free Open-Meteo API.\n"
    )
    return Response(content, mimetype="text/plain")


@app.route("/sitemap.xml")
def sitemap_xml():
    posts = get_all_posts()
    base_url = request.host_url.rstrip('/')

    xml_lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
    ]

    for route in ['/', '/blog', '/weather', '/birds']:
        xml_lines.append(f'  <url><loc>{base_url}{route}</loc><changefreq>daily</changefreq><priority>0.8</priority></url>')

    for p in posts:
        xml_lines.append(f'  <url><loc>{base_url}/blog/{p["slug"]}</loc><lastmod>{p["date"]}</lastmod><changefreq>weekly</changefreq><priority>0.6</priority></url>')

    xml_lines.append('</urlset>')
    return Response("\n".join(xml_lines), mimetype="application/xml")


@app.route("/thank-you")
def thank_you():
    return render_template("thank_you.html")


# --- Error handling ---------------------------------------------------
# Every branch here renders a template with a short, safe, human-readable
# message. None of them ever include str(exception), a traceback, a file
# path, or any other internal detail — those go to the server log only.
_ERROR_COPY = {
    400: ("Bad Request", "That request wasn't formatted the way this page expects."),
    401: ("Unauthorized", "You need a valid token to do that."),
    403: ("Forbidden", "You don't have permission to view that."),
    404: ("Page Not Found", "The page or entry you requested doesn't exist, or may have been moved."),
    405: ("Method Not Allowed", "That address doesn't support this kind of request."),
    413: ("Payload Too Large", "That request was too large to accept."),
    429: ("Too Many Requests", "Slow down a little and try again shortly."),
    500: ("Internal Error", "Something went wrong while processing that request. Try again shortly."),
}


def _render_error(code):
    title, message = _ERROR_COPY.get(code, ("Error", "Something went wrong."))
    return render_template("error.html", error_code=code, error_title=title, error_message=message), code


@app.errorhandler(404)
def not_found(e):
    return render_template("404.html"), 404


@app.errorhandler(400)
def bad_request(e):
    return _render_error(400)


@app.errorhandler(401)
def unauthorized(e):
    return _render_error(401)


@app.errorhandler(403)
def forbidden(e):
    return _render_error(403)


@app.errorhandler(405)
def method_not_allowed(e):
    return _render_error(405)


@app.errorhandler(413)
def payload_too_large(e):
    return _render_error(413)


@app.errorhandler(429)
def too_many_requests(e):
    return _render_error(429)


@app.errorhandler(500)
def server_error(e):
    return render_template("500.html"), 500


@app.errorhandler(Exception)
def unhandled_exception(e):
    # Anything not already an HTTPException is a genuine bug — log the real
    # detail server-side and show the visitor a plain 500 with nothing
    # sensitive in it.
    if isinstance(e, HTTPException):
        return _render_error(e.code or 500)
    app.logger.exception("Unhandled exception")
    return render_template("500.html"), 500


# --- INGEST ENDPOINTS ---

@app.route("/ingest/weather", methods=["POST"])
@require_auth
def ingest_weather():
    try:
        data = request.get_json(force=True, silent=True)
        if not data or not isinstance(data, dict):
            return jsonify({"error": "Invalid payload, JSON object required"}), 400

        # Allow-listed, validated fields only — anything else in the payload
        # (extra keys, wrong types, out-of-range numbers) is rejected outright
        # rather than written verbatim to the archive.
        record = {
            "date": _clean_date(data.get("date"), "date", required=True),
            "precip_mm": _clean_number(data.get("precip_mm"), "precip_mm", 0, 500, required=True),
            "temp_c_min": _clean_number(data.get("temp_c_min"), "temp_c_min", -30, 55, required=False),
            "temp_c_max": _clean_number(data.get("temp_c_max"), "temp_c_max", -30, 55, required=False),
            "conditions": _clean_text(data.get("conditions"), "conditions", 120, required=False),
        }
        if (record["temp_c_min"] is not None and record["temp_c_max"] is not None
                and record["temp_c_min"] > record["temp_c_max"]):
            raise ValidationError("'temp_c_min' cannot be greater than 'temp_c_max'")

        WEATHER_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(WEATHER_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")

        return jsonify({"status": "success", "message": f"Weather record for {record['date']} appended"}), 201
    except ValidationError as e:
        return jsonify({"error": str(e)}), 400
    except Exception:
        app.logger.exception("ingest_weather failed")
        return jsonify({"error": "Internal error while saving the reading."}), 500


@app.route("/ingest/birds", methods=["POST"])
@require_auth
def ingest_birds():
    try:
        data = request.get_json(force=True, silent=True)
        if not data or not isinstance(data, dict):
            return jsonify({"error": "Invalid payload, JSON object required"}), 400

        date_added = _clean_date(data.get("date_added"), "date_added", required=False)
        if not date_added:
            date_added = datetime.now(timezone.utc).date().isoformat()

        record = {
            "common_name": _clean_text(data.get("common_name"), "common_name", 100, required=True),
            "scientific_name": _clean_text(data.get("scientific_name"), "scientific_name", 100),
            "description": _clean_text(data.get("description"), "description", 500),
            "image": _clean_text(data.get("image"), "image", 200),
            "wikipedia_url": _clean_url(data.get("wikipedia_url"), "wikipedia_url"),
            "date_added": date_added,
        }

        BIRDS_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(BIRDS_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

        return jsonify({"status": "success", "message": f"Added {record['common_name']} to the species log"}), 201
    except ValidationError as e:
        return jsonify({"error": str(e)}), 400
    except Exception:
        app.logger.exception("ingest_birds failed")
        return jsonify({"error": "Internal error while saving the species entry."}), 500


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    host = os.environ.get("HOST", "127.0.0.1")
    if not INGEST_TOKEN:
        app.logger.warning(
            "INGEST_TOKEN is not set — /ingest/weather and /ingest/birds will refuse all writes "
            "until it's configured in .env."
        )
    app.run(host=host, port=port, debug=DEBUG)
