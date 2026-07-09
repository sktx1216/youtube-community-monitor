import hashlib
import json
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
from playwright.sync_api import sync_playwright

URL = "https://www.youtube.com/@흐구구구/posts"

STATE_FILE = Path("seen_posts.json")

KEYWORDS = ["스타레일", "원신"]

MAX_POSTS = 10

KST = timezone(timedelta(hours=9))


def make_post_id(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def normalize_youtube_url(href):
    if not href:
        return None

    if href.startswith("/"):
        return f"https://www.youtube.com{href}"

    return href


def get_first_locator_value(locator):
    if locator.count() == 0:
        return None

    element = locator.first

    for attribute in ("title", "aria-label"):
        value = element.get_attribute(attribute)

        if value:
            return value.strip()

    text = element.inner_text().strip()
    return text or None


def extract_post_url(post):
    links = post.locator('a[href*="/post/"]')

    if links.count() == 0:
        return None

    return normalize_youtube_url(links.first.get_attribute("href"))


def extract_published_text(post):
    selectors = [
        "#published-time-text a",
        "#published-time-text",
        'a[href*="/post/"]',
    ]

    for selector in selectors:
        text = get_first_locator_value(post.locator(selector))

        if text:
            return text

    return None


def parse_published_at(text, now=None):
    if not text:
        return None

    now = now or datetime.now(timezone.utc)
    normalized = " ".join(text.strip().split())

    date_match = re.search(
        r"(?P<year>\d{4})[.\-/년]\s*"
        r"(?P<month>\d{1,2})[.\-/월]\s*"
        r"(?P<day>\d{1,2})",
        normalized,
    )

    if date_match:
        return datetime(
            int(date_match.group("year")),
            int(date_match.group("month")),
            int(date_match.group("day")),
            tzinfo=timezone.utc,
        )

    relative_match = re.search(
        r"(?P<value>\d+)\s*(?P<unit>초|분|시간|일|주|개월|달|년)\s*전",
        normalized,
    )

    if not relative_match:
        if "어제" in normalized:
            return now - timedelta(days=1)

        return None

    value = int(relative_match.group("value"))
    unit = relative_match.group("unit")
    delta_by_unit = {
        "초": timedelta(seconds=value),
        "분": timedelta(minutes=value),
        "시간": timedelta(hours=value),
        "일": timedelta(days=value),
        "주": timedelta(weeks=value),
        "개월": timedelta(days=value * 30),
        "달": timedelta(days=value * 30),
        "년": timedelta(days=value * 365),
    }

    return now - delta_by_unit[unit]


def parse_iso_datetime(value):
    if not value:
        return None

    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def extract_json_ld_post(script_text):
    try:
        data = json.loads(script_text)
    except json.JSONDecodeError:
        return None

    items = data if isinstance(data, list) else [data]

    for item in items:
        if not isinstance(item, dict):
            continue

        item_type = item.get("@type") or item.get("type")

        if item_type == "https://schema.org/DiscussionForumPosting":
            return item

    return None


def extract_exact_post_details(page, post_url):
    if not post_url:
        return {}

    page.goto(post_url, wait_until="networkidle", timeout=60000)
    page.wait_for_timeout(1000)

    scripts = page.locator('script[type="application/ld+json"]')

    for i in range(scripts.count()):
        post_data = extract_json_ld_post(scripts.nth(i).inner_text())

        if post_data:
            return {
                "published_at": parse_iso_datetime(
                    post_data.get("datePublished") or post_data.get("publishDate")
                ),
                "text": post_data.get("text"),
            }

    html = page.content()
    date_match = re.search(
        r'"(?:datePublished|publishDate)"\s*:\s*"([^"]+)"',
        html,
    )
    text_match = re.search(r'"text"\s*:\s*"((?:\\.|[^"\\])*)"', html)

    details = {}

    if date_match:
        details["published_at"] = parse_iso_datetime(date_match.group(1))

    if text_match:
        details["text"] = json.loads(f'"{text_match.group(1)}"')

    return details


def datetime_to_state_value(value):
    if not value:
        return None

    return value.astimezone(timezone.utc).isoformat()


def parse_state_datetime(value):
    if not value:
        return None

    return datetime.fromisoformat(value)


def format_published_date(post):
    published_at = parse_state_datetime(post.get("published_at"))
    published_text = post.get("published_text")

    if not published_at:
        return published_text or "확인 불가"

    formatted_date = published_at.astimezone(KST).strftime("%Y-%m-%d %H:%M KST")

    if published_text and published_text != formatted_date:
        return f"{formatted_date} (원문: {published_text})"

    return formatted_date


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
            post = posts.nth(i)
            text = post.inner_text().strip()

            if text:
                post_url = extract_post_url(post)
                published_text = extract_published_text(post)
                published_at = parse_published_at(published_text)

                result.append({
                    "id": post_url or make_post_id(text),
                    "published_at": datetime_to_state_value(published_at),
                    "published_text": published_text,
                    "url": post_url,
                    "text": text,
                })

        for post in result:
            try:
                exact_details = extract_exact_post_details(page, post["url"])
            except Exception:
                exact_details = {}

            if exact_details.get("published_at"):
                post["published_at"] = datetime_to_state_value(
                    exact_details["published_at"]
                )

            if exact_details.get("text"):
                post["text"] = exact_details["text"]

        browser.close()
        return result


def load_state():
    if not STATE_FILE.exists():
        return {
            "latest_published_at": None,
            "seen_post_ids": [],
        }

    with open(STATE_FILE, "r", encoding="utf-8") as f:
        state = json.load(f)

    return {
        "latest_published_at": state.get("latest_published_at"),
        "seen_post_ids": state.get("seen_post_ids", []),
    }


def save_state(recent_posts, previous_latest_published_at=None):
    published_dates = [
        parse_state_datetime(post["published_at"])
        for post in recent_posts
        if post.get("published_at")
    ]
    previous_latest = parse_state_datetime(previous_latest_published_at)

    if previous_latest:
        published_dates.append(previous_latest)

    latest_published_at = (
        datetime_to_state_value(max(published_dates))
        if published_dates
        else previous_latest_published_at
    )

    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(
            {
                "latest_published_at": latest_published_at,
                "seen_post_ids": [post["id"] for post in recent_posts],
            },
            f,
            ensure_ascii=False,
            indent=2,
        )


def find_keywords(text):
    lower_text = text.lower()
    return [kw for kw in KEYWORDS if kw.lower() in lower_text]


def split_discord_message(text, limit=1500):
    return [text[i:i + limit] for i in range(0, len(text), limit)]


def escape_discord_mentions(text):
    return text.replace("@", "@\u200b")


def send_discord(matched_posts):
    webhook_url = os.environ["DISCORD_WEBHOOK_URL"]

    for post in matched_posts:
        keywords = ", ".join(post["keywords"])
        post_text = escape_discord_mentions(post["text"])
        post_chunks = split_discord_message(post_text)

        for chunk_index, post_chunk in enumerate(post_chunks, start=1):
            chunk_label = (
                f" ({chunk_index}/{len(post_chunks)})"
                if len(post_chunks) > 1
                else ""
            )
            post_url = post.get("url") or URL
            published_date = format_published_date(post)
            message = f"""**감지 키워드:** {keywords}
**게시물 날짜:** {published_date}
**게시물 링크:** {post_url}
**게시물 내용{chunk_label}:**

{post_chunk}
"""
            response = requests.post(
                webhook_url,
                json={"content": message},
                timeout=30,
            )
            response.raise_for_status()


def main():
    recent_posts = get_recent_posts()
    state = load_state()
    latest_seen_at = parse_state_datetime(state["latest_published_at"])
    seen_post_ids = state["seen_post_ids"]

    if not latest_seen_at and not seen_post_ids:
        print("첫 실행입니다. 최근 게시물을 저장만 합니다.")
        save_state(recent_posts)
        return

    if not latest_seen_at:
        print("기존 상태 파일을 새 날짜 기준 형식으로 저장만 합니다.")
        save_state(recent_posts)
        return

    new_posts = []

    for post in recent_posts:
        published_at = parse_state_datetime(post["published_at"])

        if latest_seen_at and published_at:
            if published_at > latest_seen_at:
                new_posts.append(post)
        elif post["id"] not in seen_post_ids:
            new_posts.append(post)

    if not new_posts:
        print("새 게시물 없음")
        save_state(recent_posts, state["latest_published_at"])
        return

    print(f"새 게시물 {len(new_posts)}건 감지")

    matched_posts = []

    for post in new_posts:
        found_keywords = find_keywords(post["text"])

        if found_keywords:
            post["keywords"] = found_keywords
            matched_posts.append(post)

    if matched_posts:
        print(f"키워드 포함 게시물 {len(matched_posts)}건 감지")
        send_discord(matched_posts)
    else:
        print("새 게시물은 있으나 키워드 포함 게시물 없음")

    save_state(recent_posts, state["latest_published_at"])


if __name__ == "__main__":
    main()
