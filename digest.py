"""
AI & tech news digest.

Fetches recent articles from a list of RSS feeds, asks a free-tier LLM
(Gemini) to pick and summarize the most significant stories, then
delivers the result via Telegram or email.

Designed to run twice a day inside GitHub Actions (see
.github/workflows/news-digest.yml) at zero cost.
"""

import os
import json
import time
import html
import smtplib
import calendar
from email.mime.text import MIMEText
from datetime import datetime, timezone, timedelta

import feedparser
import requests

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

FEEDS = [
    "https://techcrunch.com/category/artificial-intelligence/feed/",
    "https://www.theverge.com/rss/index.xml",
    "https://feeds.arstechnica.com/arstechnica/technology-lab",
    "https://www.technologyreview.com/feed/",
    "https://venturebeat.com/category/ai/feed/",
    "https://hnrss.org/frontpage",
    "http://export.arxiv.org/rss/cs.AI",
    "https://www.wired.com/feed/tag/ai/latest/rss",
]

# How far back to look for articles. Set a bit longer than half a day so a
# slow-running workflow or a late-posting feed doesn't cause a gap.
LOOKBACK_HOURS = 14

# Cap how many candidate articles we send to the LLM (keeps prompts small
# and free-tier-friendly) and how many stories end up in the final digest.
MAX_CANDIDATES = 60
MAX_STORIES = 10

SEEN_FILE = "seen.json"
SEEN_RETENTION_DAYS = 3

GEMINI_MODEL = "gemini-2.5-flash"
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")

DELIVERY_METHOD = os.environ.get("DELIVERY_METHOD", "telegram").lower()


# ---------------------------------------------------------------------------
# Fetching
# ---------------------------------------------------------------------------

def fetch_articles():
    cutoff = datetime.now(timezone.utc) - timedelta(hours=LOOKBACK_HOURS)
    articles = []

    for feed_url in FEEDS:
        try:
            parsed = feedparser.parse(feed_url)
        except Exception as e:
            print(f"Failed to fetch {feed_url}: {e}")
            continue

        source_name = parsed.feed.get("title", feed_url)

        for entry in parsed.entries:
            published_struct = entry.get("published_parsed") or entry.get("updated_parsed")
            if published_struct:
                published_dt = datetime.fromtimestamp(
                    calendar.timegm(published_struct), tz=timezone.utc
                )
                if published_dt < cutoff:
                    continue
            # If a feed doesn't expose a date, include it anyway rather
            # than silently dropping it.

            link = entry.get("link", "")
            title = entry.get("title", "").strip()
            summary = entry.get("summary", "") or entry.get("description", "")
            summary = html.unescape(_strip_tags(summary))[:300]

            if not title or not link:
                continue

            articles.append({
                "title": title,
                "link": link,
                "source": source_name,
                "excerpt": summary,
            })

    return articles


def _strip_tags(text):
    out = []
    in_tag = False
    for ch in text:
        if ch == "<":
            in_tag = True
        elif ch == ">":
            in_tag = False
        elif not in_tag:
            out.append(ch)
    return "".join(out)


def dedupe_against_seen(articles):
    seen_links = set()
    if os.path.exists(SEEN_FILE):
        try:
            with open(SEEN_FILE, "r") as f:
                seen_data = json.load(f)
            seen_links = {item["link"] for item in seen_data}
        except Exception:
            pass

    fresh = [a for a in articles if a["link"] not in seen_links]

    # De-dupe within this batch too (same story from two feeds)
    unique = {}
    for a in fresh:
        unique[a["link"]] = a
    return list(unique.values())[:MAX_CANDIDATES]


def update_seen(stories):
    existing = []
    if os.path.exists(SEEN_FILE):
        try:
            with open(SEEN_FILE, "r") as f:
                existing = json.load(f)
        except Exception:
            existing = []

    cutoff = datetime.now(timezone.utc) - timedelta(days=SEEN_RETENTION_DAYS)
    existing = [
        item for item in existing
        if datetime.fromisoformat(item["seen_at"]) > cutoff
    ]

    now_iso = datetime.now(timezone.utc).isoformat()
    for s in stories:
        existing.append({"link": s["link"], "seen_at": now_iso})

    with open(SEEN_FILE, "w") as f:
        json.dump(existing, f, indent=2)


# ---------------------------------------------------------------------------
# Summarization (Gemini free tier)
# ---------------------------------------------------------------------------

def summarize_with_gemini(articles):
    if not GEMINI_API_KEY:
        raise RuntimeError("GEMINI_API_KEY is not set")

    listing = "\n\n".join(
        f"[{i}] Title: {a['title']}\nSource: {a['source']}\nExcerpt: {a['excerpt']}\nLink: {a['link']}"
        for i, a in enumerate(articles)
    )

    prompt = f"""You are curating a personal AI & technology news brief.

Below are candidate articles from the last several hours. Each has an
index, title, source, a short excerpt, and a link.

1. Select the {MAX_STORIES} most significant and interesting stories for
   someone who wants to stay current on AI and technology. Prioritize
   real news, launches, research results, and business developments over
   opinion pieces, listicles, or rumors.
2. If multiple articles cover the same event, keep only the best one.
3. Write a concise 1-2 sentence summary of each selected story in your
   own words. Do not copy sentences from the excerpt.
4. Return ONLY a JSON array (no markdown formatting, no commentary)
   in exactly this shape:
[{{"title": "...", "source": "...", "summary": "...", "link": "..."}}]

Articles:
{listing}
"""

    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/"
        f"{GEMINI_MODEL}:generateContent?key={GEMINI_API_KEY}"
    )
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"responseMimeType": "application/json"},
    }

    for attempt in range(2):
        try:
            resp = requests.post(url, json=payload, timeout=60)
            resp.raise_for_status()
            data = resp.json()
            text = data["candidates"][0]["content"]["parts"][0]["text"]
            text = text.strip().strip("`")
            if text.startswith("json"):
                text = text[4:].strip()
            stories = json.loads(text)
            return stories[:MAX_STORIES]
        except Exception as e:
            print(f"Gemini call failed (attempt {attempt + 1}): {e}")
            time.sleep(3)

    return None  # caller falls back to an unsummarized list


# ---------------------------------------------------------------------------
# Formatting + delivery
# ---------------------------------------------------------------------------

def format_message(stories, fallback=False):
    today = datetime.now(timezone.utc).strftime("%B %d, %Y")
    lines = [f"<b>AI &amp; tech digest — {today}</b>", ""]

    if fallback:
        lines.append("(Summarizer unavailable this run — showing raw headlines)")
        lines.append("")

    for s in stories:
        title = html.escape(s["title"])
        source = html.escape(s.get("source", ""))
        link = s["link"]
        summary = html.escape(s.get("summary", s.get("excerpt", "")))
        lines.append(f'<b><a href="{link}">{title}</a></b>')
        lines.append(f"<i>{source}</i>")
        if summary:
            lines.append(summary)
        lines.append("")

    return "\n".join(lines).strip()


def send_telegram(message):
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    chat_id = os.environ["TELEGRAM_CHAT_ID"]
    url = f"https://api.telegram.org/bot{token}/sendMessage"

    # Telegram caps messages at 4096 characters; split on blank lines if needed.
    chunks = _chunk_message(message, 4000)
    for chunk in chunks:
        resp = requests.post(url, json={
            "chat_id": chat_id,
            "text": chunk,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }, timeout=30)
        if not resp.ok:
            print(f"Telegram send failed: {resp.status_code} {resp.text}")
        time.sleep(1)


def _chunk_message(message, limit):
    if len(message) <= limit:
        return [message]
    chunks, current = [], ""
    for block in message.split("\n\n"):
        if len(current) + len(block) + 2 > limit:
            chunks.append(current.strip())
            current = ""
        current += block + "\n\n"
    if current.strip():
        chunks.append(current.strip())
    return chunks


def send_email(message):
    html_body = message.replace("\n", "<br>")
    msg = MIMEText(html_body, "html")
    msg["Subject"] = f"AI & tech digest — {datetime.now(timezone.utc):%B %d, %Y}"
    msg["From"] = os.environ["EMAIL_FROM"]
    msg["To"] = os.environ["EMAIL_TO"]

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(os.environ["EMAIL_FROM"], os.environ["EMAIL_APP_PASSWORD"])
        server.send_message(msg)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    all_articles = fetch_articles()
    print(f"Fetched {len(all_articles)} raw articles")

    fresh = dedupe_against_seen(all_articles)
    print(f"{len(fresh)} unseen articles after dedupe")

    if not fresh:
        print("No new articles this run — skipping delivery.")
        return

    stories = summarize_with_gemini(fresh)
    fallback = stories is None
    if fallback:
        # LLM call failed twice — deliver raw headlines so the run isn't wasted.
        stories = fresh[:MAX_STORIES]

    message = format_message(stories, fallback=fallback)

    if DELIVERY_METHOD == "telegram":
        send_telegram(message)
    elif DELIVERY_METHOD == "email":
        send_email(message)
    else:
        raise ValueError(f"Unknown DELIVERY_METHOD: {DELIVERY_METHOD}")

    update_seen(stories)
    print(f"Delivered {len(stories)} stories via {DELIVERY_METHOD}")


if __name__ == "__main__":
    main()
