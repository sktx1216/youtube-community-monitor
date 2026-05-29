import hashlib
import json
import os
from pathlib import Path

import requests
from playwright.sync_api import sync_playwright

URL = "https://www.youtube.com/@흐구구구/posts"

STATE_FILE = Path("seen_posts.json")

KEYWORDS = ["스타레일", "원신"]

MAX_POSTS = 10


def make_post_id(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def get_recent_posts():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)

        page = browser.new_page(
            locale="ko-KR",
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
        )

        page.goto(URL, wait_until="networkidle", timeout=60000)
        page.wait_for_timeout(5000)

        posts = page.locator("ytd-backstage-post-thread-renderer")

        count = min(posts.count(), MAX_POSTS)

        if count == 0:
            browser.close()
            raise Exception("게시물을 찾지 못했습니다.")

        result = []

        for i in range(count):
            text = posts.nth(i).inner_text().strip()

            if text:
                result.append({
                    "id": make_post_id(text),
                    "text": text,
                })

        browser.close()
        return result


def load_seen_post_ids():
    if not STATE_FILE.exists():
        return []

    with open(STATE_FILE, "r", encoding="utf-8") as f:
        return json.load(f).get("seen_post_ids", [])


def save_seen_post_ids(post_ids):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(
            {"seen_post_ids": post_ids},
            f,
            ensure_ascii=False,
            indent=2,
        )


def find_keywords(text):
    lower_text = text.lower()
    return [kw for kw in KEYWORDS if kw.lower() in lower_text]


def split_discord_message(text, limit=1800):
    return [text[i:i + limit] for i in range(0, len(text), limit)]


def send_discord(matched_posts):
    webhook_url = os.environ["DISCORD_WEBHOOK_URL"]

    for idx, post in enumerate(matched_posts, start=1):
        keywords = ", ".join(post["keywords"])
        post_text = post["text"]

        message = f"""🚨 **유튜브 새 게시물 키워드 감지**

**감지 키워드:** {keywords}
**게시물 탭:** {URL}

```text
{post_text}