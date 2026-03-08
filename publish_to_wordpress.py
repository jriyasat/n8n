import os, glob, re, json, requests, yaml
import markdown as mdlib

WP_BASE_URL = os.environ["WP_BASE_URL"].rstrip("/")
WP_USERNAME = os.environ["WP_USERNAME"]
WP_APP_PASSWORD = os.environ["WP_APP_PASSWORD"]
PEXELS_API_KEY = os.environ["PEXELS_API_KEY"]

def find_latest(prefix, ext):
    files = sorted(glob.glob(f"{prefix}/*.{ext}"))
    if not files:
        raise SystemExit(f"No {ext} files found in {prefix}/")
    return files[-1]

def parse_frontmatter(md_text):
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n(.*)$", md_text, re.S)
    if not m:
        return {}, md_text
    fm = yaml.safe_load(m.group(1)) or {}
    body = m.group(2)
    return fm, body

def md_to_html(markdown_text: str) -> str:
    return mdlib.markdown(markdown_text, extensions=["extra", "sane_lists"])

# ---------- Pexels ----------
def pexels_search_image(query: str):
    url = "https://api.pexels.com/v1/search"
    headers = {"Authorization": PEXELS_API_KEY}
    params = {
        "query": query,
        "per_page": 10,
        "orientation": "landscape",
        "size": "large",
    }
    r = requests.get(url, headers=headers, params=params, timeout=30)
    r.raise_for_status()
    data = r.json()
    photos = data.get("photos", [])
    if not photos:
        return None
    return photos[0]

def download_image_bytes(image_url: str):
    r = requests.get(image_url, timeout=60)
    r.raise_for_status()
    return r.content, r.headers.get("Content-Type", "image/jpeg")

# ---------- WordPress ----------
def wp_upload_media(filename: str, file_bytes: bytes, mime: str, alt_text: str = ""):
    url = f"{WP_BASE_URL}/wp-json/wp/v2/media"
    headers = {
        "Content-Disposition": f'attachment; filename="{filename}"',
        "Content-Type": mime,
    }
    r = requests.post(
        url,
        auth=(WP_USERNAME, WP_APP_PASSWORD),
        headers=headers,
        data=file_bytes,
        timeout=60,
    )
    r.raise_for_status()
    media = r.json()

    if alt_text:
        media_id = media["id"]
        requests.post(
            f"{WP_BASE_URL}/wp-json/wp/v2/media/{media_id}",
            auth=(WP_USERNAME, WP_APP_PASSWORD),
            json={"alt_text": alt_text},
            timeout=30,
        ).raise_for_status()

    return media

def wp_create_post(title: str, html_content: str, featured_media=None, status="publish"):
    url = f"{WP_BASE_URL}/wp-json/wp/v2/posts"
    payload = {"title": title, "content": html_content, "status": status}
    if featured_media:
        payload["featured_media"] = featured_media

    r = requests.post(
        url,
        auth=(WP_USERNAME, WP_APP_PASSWORD),
        json=payload,
        timeout=30,
    )
    r.raise_for_status()
    return r.json()

def main():
    md_path = find_latest("drafts", "md")
    json_path = md_path[:-3] + ".json"
    if not os.path.exists(json_path):
        json_path = find_latest("drafts", "json")

    raw_md = open(md_path, "r", encoding="utf-8").read()
    fm, markdown_body = parse_frontmatter(raw_md)

    package = json.loads(open(json_path, "r", encoding="utf-8").read())
    post_obj = package.get("post", {}) if isinstance(package, dict) else {}

    title = fm.get("title") or post_obj.get("title") or "Post"
    source_url = fm.get("source_url", "")

    # Markdown -> HTML for WordPress
    html_body = md_to_html(markdown_body)
    if source_url:
        html_body += f'\n<hr>\n<p><strong>Source:</strong> <a href="{source_url}">{source_url}</a></p>\n'

    # Featured image (stock)
    image_query = post_obj.get("image_query") or "flat illustration vector technology business abstract"
    image_alt = post_obj.get("image_alt") or "Flat illustration related to the article topic"

    featured_media_id = None
    try:
        photo = pexels_search_image(image_query)
        if photo:
            src = (photo.get("src") or {})
            image_url = src.get("large2x") or src.get("large") or src.get("original")
            if image_url:
                img_bytes, mime = download_image_bytes(image_url)
                filename = f"featured-{os.path.basename(md_path).replace('.md','')}.jpg"
                media = wp_upload_media(filename, img_bytes, mime, alt_text=image_alt)
                featured_media_id = media.get("id")
                print("Uploaded featured image media_id:", featured_media_id)
    except Exception as e:
        print("Image step failed (continuing without featured image):", str(e))

    post = wp_create_post(
        title=title,
        html_content=html_body,
        featured_media=featured_media_id,
        status="publish",
    )
    print("Created WP post:", post.get("link"))

if __name__ == "__main__":
    main()
