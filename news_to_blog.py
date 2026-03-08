import os, json, re, datetime
import requests
from bs4 import BeautifulSoup
import trafilatura
from openai import OpenAI

client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])

NOW = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=-5)))  # America/New_York approx
DATE_STR = NOW.strftime("%Y-%m-%d")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; NewsToBlogBot/1.0; +https://solveditbilling.com/dev/)"
}

def fetch(url: str) -> str:
    r = requests.get(url, headers=HEADERS, timeout=30)
    r.raise_for_status()
    return r.text

def absolute_url(base, href):
    if not href:
        return href
    if href.startswith("http"):
        return href
    if href.startswith("//"):
        return "https:" + href
    if href.startswith("/"):
        return base.rstrip("/") + href
    return base.rstrip("/") + "/" + href

def extract_article_text(url: str) -> str:
    html = fetch(url)
    text = trafilatura.extract(html, include_comments=False, include_tables=False)
    return (text or "").strip()

# --- Site-specific "top story" extractors (heuristics) ---
def top_story_techcrunch():
    html = fetch("https://techcrunch.com")
    soup = BeautifulSoup(html, "html.parser")
    link = soup.select_one("a.post-block__title__link, a.wp-block-post-title__link, h2 a")
    if not link or not link.get("href"):
        raise RuntimeError("Could not find TechCrunch top story link")
    return {"source": "TechCrunch", "title": link.get_text(strip=True), "url": link["href"]}

def top_story_itpro():
    html = fetch("https://www.itpro.com/news")
    soup = BeautifulSoup(html, "html.parser")
    link = soup.select_one("a.article-link, h3 a, h2 a")
    if not link or not link.get("href"):
        raise RuntimeError("Could not find ITPro top story link")
    return {"source": "ITPro", "title": link.get_text(strip=True), "url": absolute_url("https://www.itpro.com", link["href"])}

def top_story_techtarget():
    html = fetch("https://www.techtarget.com/news/all")
    soup = BeautifulSoup(html, "html.parser")
    link = soup.select_one("a[href*='/news/'], h3 a, h2 a")
    if not link or not link.get("href"):
        raise RuntimeError("Could not find TechTarget top story link")
    return {"source": "TechTarget", "title": link.get_text(strip=True), "url": absolute_url("https://www.techtarget.com", link["href"])}

def rank_importance(candidates):
    prompt = {
        "task": "Pick the most important story among these candidates for a general tech/business audience.",
        "criteria": [
            "real-world impact (business/users/society)",
            "novelty and significance (not routine)",
            "breadth of relevance (how many people/orgs affected)",
            "time-sensitivity/urgency"
        ],
        "output": "Return strict JSON with winner_index (0-based), scores (0-10) for each candidate, and short reasoning."
    }

    content = "CANDIDATES:\n" + "\n\n".join(
        [f"[{i}] Source: {c['source']}\nTitle: {c['title']}\nURL: {c['url']}\nArticleText:\n{c.get('text','')[:6000]}"
         for i, c in enumerate(candidates)]
    )

    resp = client.chat.completions.create(
        model="gpt-4.1-mini",
        messages=[
            {"role": "system", "content": "You are a careful editor. Do not invent facts. Output MUST be valid JSON only."},
            {"role": "user", "content": json.dumps(prompt)},
            {"role": "user", "content": content},
        ],
        temperature=0.2,
    )
    return json.loads(resp.choices[0].message.content.strip())

def generate_exec_brief(winner, ranking):
    instructions = {
        "task": "Write an executive-brief blog post about the selected story.",
        "style": "Executive brief: concise, decision-oriented, minimal jargon. Avoid hype.",
        "length": "600-900 words",
        "format": "Markdown with headings + bullet points",
        "must_include": [
            "Headline",
            "2-3 sentence executive summary",
            "Key points (bullets)",
            "Why it matters (business impact)",
            "Risks / caveats",
            "What to watch next",
            "Source link"
        ],
        "rules": [
            "Do not invent facts, quotes, or numbers.",
            "If details are missing, say what is unknown rather than guessing.",
            "Clearly separate reported facts (from source) from analysis."
        ],
        "image_rules": [
            "Also provide a stock-image search query for a flat illustration hero image (1200x630 feel).",
            "image_query should be 4-10 keywords, include 'flat illustration' and/or 'vector', and avoid brand names.",
            "image_alt should be plain, descriptive, <= 140 characters, no brand names."
        ],
        "output": "Return JSON with keys: title, slug, summary, markdown, meta_description, tags, image_query, image_alt"
    }

    resp = client.chat.completions.create(
        model="gpt-4.1",
        messages=[
            {"role": "system", "content": "You are a careful executive editor. Be factual. Output MUST be strict JSON."},
            {"role": "user", "content": json.dumps(instructions)},
            {"role": "user", "content": (
                f"Selected story:\nSource: {winner['source']}\nTitle: {winner['title']}\nURL: {winner['url']}\n\n"
                f"Article text:\n{winner.get('text','')[:12000]}\n\n"
                f"Ranking context:\n{json.dumps(ranking)}"
            )},
        ],
        temperature=0.4,
    )
    return json.loads(resp.choices[0].message.content.strip())

def slugify(s):
    s = (s or "").lower()
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return (s[:80] if s else "post")

def main():
    candidates = [top_story_techcrunch(), top_story_itpro(), top_story_techtarget()]

    for c in candidates:
        try:
            c["text"] = extract_article_text(c["url"])
        except Exception as e:
            c["text"] = ""
            c["extract_error"] = str(e)

    ranking = rank_importance(candidates)
    winner = candidates[ranking["winner_index"]]

    post = generate_exec_brief(winner, ranking)
    post["slug"] = post.get("slug") or slugify(post.get("title", ""))

    out_dir = "drafts"
    os.makedirs(out_dir, exist_ok=True)

    base = f"{out_dir}/{DATE_STR}-{post['slug']}"

    with open(base + ".md", "w", encoding="utf-8") as f:
        f.write(
            f"""---
title: "{post['title']}"
date: "{DATE_STR}"
status: "needs_review"
source_title: "{winner['title']}"
source_url: "{winner['url']}"
source_site: "{winner['source']}"
tags: {post.get('tags', [])}
meta_description: "{post.get('meta_description','')}"
---

{post['markdown']}
"""
        )

    with open(base + ".json", "w", encoding="utf-8") as f:
        json.dump(
            {"post": post, "winner": winner, "ranking": ranking, "candidates": candidates},
            f,
            indent=2,
        )

    print("Wrote draft:", base + ".md")
    print("Image query:", post.get("image_query"))
    print("Image alt:", post.get("image_alt"))

if __name__ == "__main__":
    main()
