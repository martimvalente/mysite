import os
import re
import json
import hmac
from pathlib import Path
from functools import wraps
from datetime import datetime, timezone
from flask import Flask, render_template, request, jsonify, send_from_directory, abort, url_for, Response
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 256 * 1024  # 256 KB max payload

BASE_DIR = Path(__file__).parent.resolve()
CONTENT_DIR = BASE_DIR / "content"
BLOG_DIR = CONTENT_DIR / "blog" / "posts"
WEATHER_FILE = CONTENT_DIR / "weather" / "weather.jsonl"
BIRDS_FILE = CONTENT_DIR / "birds" / "birds.jsonl"
BIRDS_DIR = CONTENT_DIR / "birds"

INGEST_TOKEN = os.environ.get("INGEST_TOKEN", "secret_indie_token_2026")

SLUG_RE = re.compile(r"^[a-z0-9-]+$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n(.*)$", re.DOTALL)
KV_RE = re.compile(r"^(\w+):\s*(.*)$", re.MULTILINE)

# Auth Decorator
def require_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        auth_header = request.headers.get("X-Auth-Token", "")
        if not auth_header or not hmac.compare_digest(auth_header, INGEST_TOKEN):
            return jsonify({"error": "Unauthorized: Invalid or missing X-Auth-Token"}), 401
        return f(*args, **kwargs)
    return decorated

# Simple, robust zero-dependency Markdown to HTML converter
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
        
        # Lists
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
            
        # Headers
        h_match = re.match(r"^(#{1,6})\s+(.*)$", stripped)
        if h_match:
            level = len(h_match.group(1))
            html_lines.append(f"<h{level}>{h_match.group(2)}</h{level}>")
            continue
            
        # Blockquotes
        if stripped.startswith(">"):
            quote_text = stripped[1:].strip()
            html_lines.append(f"<blockquote><p>{quote_text}</p></blockquote>")
            continue
            
        # Paragraphs
        html_lines.append(f"<p>{stripped}</p>")

    if in_list:
        html_lines.append(f"</{list_type}>")
        
    html = "\n".join(html_lines)
    
    # Inline formatting
    # Images: ![alt](url) -> if url starts with images/, rewrite to /blog/<slug>/images/...
    def replace_img(m):
        alt, src = m.group(1), m.group(2)
        if src.startswith("images/") and slug:
            src = f"/blog/{slug}/{src}"
        return f'<img src="{src}" alt="{alt}" class="markdown-image" loading="lazy" />'
        
    html = re.sub(r"!\[(.*?)\]\((.*?)\)", replace_img, html)
    
    # Links: [text](url)
    html = re.sub(r"\[(.*?)\]\((.*?)\)", r'<a href="\2" target="_blank" rel="noopener">\1</a>', html)
    
    # Bold **text**
    html = re.sub(r"\*\*(.*?)\*\*", r"<strong>\1</strong>", html)
    
    # Italic *text*
    html = re.sub(r"\*(.*?)\*", r"<em>\1</em>", html)
    
    # Inline code `code`
    html = re.sub(r"`(.*?)`", r"<code>\1</code>", html)
    
    # Restore Code blocks
    for i, code in enumerate(code_blocks):
        escaped_code = code.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        html = html.replace(f"<!--CODE_BLOCK_{i}-->", f"<pre><code>{escaped_code}</code></pre>")

    return html

# Blog Parsing Helper
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
            if date_prefix_match:
                date_str = date_prefix_match.group(1)
            else:
                date_str = "1970-01-01"
                
        tags_raw = fm.get("tags", "")
        tags = [t.strip() for t in tags_raw.split(",") if t.strip()] if tags_raw else []
        
        # Thumbnail selection
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
        
        # Excerpt from body
        plain_text = re.sub(r"<[^>]+>", "", rendered_html)
        excerpt = plain_text[:160] + "..." if len(plain_text) > 160 else plain_text
        
        posts.append({
            "slug": slug,
            "title": title,
            "date": date_str,
            "tags": tags,
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

# Weather Reading Helper
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
                
    sorted_records = [records[k] for k in sorted(records.keys(), reverse=True)]
    return sorted_records

# Birds Reading Helper
def get_bird_records():
    records = {}
    
    # 1. Read JSON Lines if available
    if BIRDS_FILE.exists():
        with open(BIRDS_FILE, "r", encoding="utf-8") as f:
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
                    
    # 2. Read fallback daily .txt files if any exist
    if BIRDS_DIR.exists():
        for txt_file in BIRDS_DIR.glob("*.txt"):
            date_name = txt_file.stem
            if DATE_RE.match(date_name) and date_name not in records:
                try:
                    species = [l.strip() for l in txt_file.read_text(encoding="utf-8").split("\n") if l.strip()]
                    records[date_name] = {
                        "date": date_name,
                        "species": species,
                        "count": len(species)
                    }
                except Exception:
                    continue

    sorted_records = [records[k] for k in sorted(records.keys(), reverse=True)]
    return sorted_records

# Species Frequency Analytics Helper
def get_bird_analytics(bird_records):
    species_counts = {}
    for rec in bird_records:
        for sp in rec.get("species", []):
            species_counts[sp] = species_counts.get(sp, 0) + 1
    sorted_species = sorted(species_counts.items(), key=lambda x: x[1], reverse=True)
    return sorted_species

# Blogger-style Year/Month Archive Tree Helper
def get_post_archive_tree(posts):
    archive = {}
    for p in posts:
        date_str = p.get("date", "1970-01-01")
        try:
            dt = datetime.strptime(date_str, "%Y-%m-%d")
            year = str(dt.year)
            month = dt.strftime("%B")
            month_num = dt.month
        except Exception:
            year = "Archive"
            month = "Posts"
            month_num = 1
            
        if year not in archive:
            archive[year] = {}
        if month not in archive[year]:
            archive[year][month] = []
        archive[year][month].append(p)
    return archive

@app.context_processor
def inject_global_sidebar():
    posts = get_all_posts()
    archive_tree = get_post_archive_tree(posts)
    all_tags = sorted(list(set(t for p in posts for t in p.get("tags", []))))
    weather_records = get_weather_records()
    latest_weather = weather_records[0] if weather_records else None
    bird_records = get_bird_records()
    latest_birds = bird_records[0] if bird_records else None
    
    return {
        "sidebar_archive_tree": archive_tree,
        "sidebar_all_tags": all_tags,
        "sidebar_latest_weather": latest_weather,
        "sidebar_latest_birds": latest_birds,
        "total_posts_count": len(posts)
    }

# --- ROUTES ---

@app.route("/")
def home():
    posts = get_all_posts()
    recent_post = posts[0] if posts else None
    
    weather_records = get_weather_records()
    latest_weather = weather_records[0] if weather_records else None
    
    bird_records = get_bird_records()
    latest_birds = bird_records[0] if bird_records else None
    
    return render_template(
        "home.html",
        recent_post=recent_post,
        latest_weather=latest_weather,
        latest_birds=latest_birds
    )

@app.route("/blog")
def blog_list():
    tag_filter = request.args.get("tag", "").strip().lower()
    year_filter = request.args.get("year", "").strip()
    posts = get_all_posts()
    
    if tag_filter:
        posts = [p for p in posts if tag_filter in [t.lower() for t in p["tags"]]]
        
    if year_filter:
        posts = [p for p in posts if p["date"].startswith(year_filter)]
        
    archive_tree = get_post_archive_tree(posts)
    
    # Group posts by year for collapsible sections
    grouped_posts = {}
    for p in posts:
        yr = p["date"][:4] if p.get("date") else "Archive"
        if yr not in grouped_posts:
            grouped_posts[yr] = []
        grouped_posts[yr].append(p)
    
    return render_template(
        "blog_list.html",
        posts=posts,
        grouped_posts=grouped_posts,
        archive_tree=archive_tree,
        selected_tag=tag_filter,
        selected_year=year_filter
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
    
    # Generate SVG precipitation bar chart data
    chart_records = list(reversed(records[:14]))  # last 14 entries in chronological order
    max_precip = max([r.get("precip_mm", 0.0) for r in chart_records] + [10.0])
    
    chart_data = []
    for r in chart_records:
        val = r.get("precip_mm", 0.0)
        # Height normalized to max 120px
        bar_h = round((val / max_precip) * 110, 1) if max_precip > 0 else 0
        chart_data.append({
            "date": r.get("date", ""),
            "precip": val,
            "bar_height": max(bar_h, 4) if val > 0 else 2,
            "temp_min": r.get("temp_c_min"),
            "temp_max": r.get("temp_c_max")
        })
        
    return render_template(
        "weather.html",
        records=records,
        chart_data=chart_data,
        max_precip=max_precip
    )

@app.route("/birds")
def birds():
    records = get_bird_records()
    analytics = get_bird_analytics(records)
    total_sightings = sum([r.get("count", len(r.get("species", []))) for r in records])
    
    return render_template(
        "birds.html",
        records=records,
        analytics=analytics,
        total_sightings=total_sightings
    )

@app.route("/robots.txt")
def robots_txt():
    content = f"User-agent: *\nAllow: /\nSitemap: {request.host_url}sitemap.xml\n"
    return Response(content, mimetype="text/plain")

@app.route("/llms.txt")
def llms_txt():
    content = (
        "# Indie Logbook\n\n"
        "> Monolith, file-backed website tracking daily farm notes, micro-climate weather, and bird species in Canelas.\n\n"
        "## Site Structure\n"
        "- [/](http://localhost:8000/): Main feed, weather cards, birds spotted today, and essential agricultural services.\n"
        "- [/blog](http://localhost:8000/blog): Archive of field notes stored as flat Markdown files.\n"
        "- [/weather](http://localhost:8000/weather): Daily weather measurements (min/max temperature, precipitation, conditions).\n"
        "- [/birds](http://localhost:8000/birds): Daily bird species checklists and frequency analytics.\n\n"
        "## Technical Architecture\n"
        "Monolithic Python Flask web server. Zero database. All state lives in content/ flat files (Markdown, JSON Lines).\n"
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
    
    static_routes = ['/', '/blog', '/weather', '/birds']
    for route in static_routes:
        xml_lines.append(f'  <url><loc>{base_url}{route}</loc><changefreq>daily</changefreq><priority>0.8</priority></url>')
        
    for p in posts:
        xml_lines.append(f'  <url><loc>{base_url}/blog/{p["slug"]}</loc><lastmod>{p["date"]}</lastmod><changefreq>weekly</changefreq><priority>0.6</priority></url>')
        
    xml_lines.append('</urlset>')
    return Response("\n".join(xml_lines), mimetype="application/xml")

@app.errorhandler(404)
def page_not_found(e):
    return render_template("404.html"), 404

@app.errorhandler(500)
def server_error(e):
    return render_template("500.html"), 500

# --- INGEST ENDPOINTS ---

@app.route("/ingest/weather", methods=["POST"])
@require_auth
def ingest_weather():
    try:
        data = request.get_json(force=True, silent=True)
        if not data or not isinstance(data, dict):
            return jsonify({"error": "Invalid payload, JSON object required"}), 400
            
        date = data.get("date")
        if not date or not DATE_RE.match(str(date)):
            return jsonify({"error": "Missing or invalid 'date' field (YYYY-MM-DD required)"}), 400
            
        WEATHER_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(WEATHER_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(data) + "\n")
            
        return jsonify({"status": "success", "message": f"Weather record for {date} appended"}), 201
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/ingest/birds", methods=["POST"])
@require_auth
def ingest_birds():
    try:
        # Check if JSON payload or plain text payload
        data = request.get_json(silent=True)
        date_str = ""
        species_list = []

        if data and isinstance(data, dict):
            date_str = str(data.get("date", ""))
            species_list = data.get("species", [])
            if isinstance(species_list, str):
                species_list = [s.strip() for s in species_list.split("\n") if s.strip()]
        else:
            # Plain text body via curl --data-binary
            date_str = request.args.get("date", "")
            raw_text = request.get_data(as_text=True)
            species_list = [s.strip() for s in raw_text.split("\n") if s.strip()]

        if not date_str or not DATE_RE.match(date_str):
            return jsonify({"error": "Missing or invalid date (specify in JSON 'date' or query parameter ?date=YYYY-MM-DD)"}), 400
            
        if not species_list:
            return jsonify({"error": "Empty species list"}), 400
            
        record = {
            "date": date_str,
            "species": species_list,
            "count": len(species_list),
            "ingested_at": datetime.now(timezone.utc).isoformat()
        }
        
        BIRDS_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(BIRDS_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")
            
        return jsonify({"status": "success", "message": f"Bird checklist for {date_str} appended ({len(species_list)} species)"}), 201
    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    host = os.environ.get("HOST", "127.0.0.1")
    app.run(host=host, port=port, debug=True)
