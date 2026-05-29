import hashlib
import json
import os
import smtplib
from email.mime.text import MIMEText
from pathlib import Path

from playwright.sync_api import sync_playwright

URL = "https://www.youtube.com/@흐구구구/posts"

STATE_FILE = Path("seen_posts.json")

KEYWORDS = ["스타레일", "원신"]

MAX_POSTS = 20


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
            )
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
                    "text": text
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
            indent=2
        )


def find_keywords(text):
    lower_text = text.lower()
    return [kw for kw in KEYWORDS if kw.lower() in lower_text]


def highlight_keywords(text, keywords):
    result = text

    for keyword in keywords:
        result = result.replace(keyword, f">>> {keyword} <<<")

    return result


def send_email(matched_posts):
    gmail_user = os.environ["GMAIL_USER"]
    gmail_app_password = os.environ["GMAIL_APP_PASSWORD"]
    to_email = os.environ["TO_EMAIL"]

    subject = f"유튜브 새 게시물 키워드 감지: {len(matched_posts)}건"

    body_parts = ["[유튜브 새 게시물 키워드 감지]\n"]

    for idx, post in enumerate(matched_posts, start=1):
        keywords = post["keywords"]
        text = highlight_keywords(post["text"], keywords)

        body_parts.append(f"""
━━━━━━━━━━━━━━━━━━━━
[{idx}] 감지 키워드: {', '.join(keywords)}

게시물 내용:
{text}
""")

    body_parts.append(f"""

게시물 탭:
{URL}
""")

    body = "\n".join(body_parts)

    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"] = gmail_user
    msg["To"] = to_email

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(gmail_user, gmail_app_password)
        server.send_message(msg)


def main():
    recent_posts = get_recent_posts()
    seen_post_ids = load_seen_post_ids()

    if not seen_post_ids:
        print("첫 실행입니다. 최근 게시물을 저장만 합니다.")
        save_seen_post_ids([post["id"] for post in recent_posts])
        return

    new_posts = [
        post for post in recent_posts
        if post["id"] not in seen_post_ids
    ]

    if not new_posts:
        print("새 게시물 없음")
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
        send_email(matched_posts)
    else:
        print("새 게시물은 있으나 키워드 포함 게시물 없음")

    save_seen_post_ids([post["id"] for post in recent_posts])


if __name__ == "__main__":
    main()