import os
import json
import re
import time
from datetime import datetime
from urllib.parse import urlparse, parse_qs, unquote
import feedparser
import requests
from openai import OpenAI

SOURCES_FILE = "sources.json"
NEWS_FILE = "news.json"

def load_json(path, default):
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default

def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def clean_google_url(url):
    if "google." in url and "/url?" in url:
        parsed = urlparse(url)
        qs = parse_qs(parsed.query)
        if "url" in qs:
            return unquote(qs["url"][0])
        if "q" in qs:
            return unquote(qs["q"][0])
    return url

def strip_tracking(url):
    parsed = urlparse(url)
    return f"{parsed.scheme}://{parsed.netloc}{parsed.path}".rstrip("/")

def is_blocked(url, title, blocked_domains, stop_words):
    url_lower = url.lower()
    title_lower = title.lower()
    for domain in blocked_domains:
        if domain.lower() in url_lower:
            return True
    for word in stop_words:
        if word.lower() in title_lower or word.lower() in url_lower:
            return True
    return False

def jaccard_similarity(str1, str2):
    words1 = set(re.findall(r'\w{3,}', str1.lower()))
    words2 = set(re.findall(r'\w{3,}', str2.lower()))
    if not words1 or not words2:
        return 0.0
    intersection = words1.intersection(words2)
    union = words1.union(words2)
    return len(intersection) / len(union)

def fetch_full_text(url, fallback_text):
    try:
        jina_url = f"https://r.jina.ai/{url}"
        resp = requests.get(jina_url, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
        if resp.status_code == 200 and len(resp.text.strip()) > 200:
            return resp.text.strip()[:3500]
    except Exception:
        pass
    clean_fallback = re.sub(r'<[^>]+>', ' ', fallback_text or "")
    return re.sub(r'\s+', ' ', clean_fallback).strip()[:2000]

def process_with_ai(client, title, text, recent_titles, use_spam_filter):
    titles_str = "\n".join([f"- {t}" for t in recent_titles[:10]]) if recent_titles else "Нет недавних новостей"
    spam_rule = (
        "1. Если текст является скрытой рекламой, продажей курсов, гороскопом, поздравлением, "
        "анонсом стрима без сути или пустым кликбейтом без фактов — верни ТОЛЬКО одно слово: SPAM\n"
        if use_spam_filter else ""
    )
    prompt = f"""Ты строгий новостной редактор. Проанализируй текст.

Правила:
{spam_rule}2. Если новость описывает то же самое событие, что уже есть в списке недавних заголовков:
{titles_str}
— верни ТОЛЬКО одно слово: DUPLICATE
3. Если это полезная уникальная новость — переведи суть на русский язык и верни ответ СТРОГО в формате JSON без маркдауна (без ```json) и без пояснений:
{{"title": "Короткий точный заголовок на русском (до 70 символов)", "summary": "Первое предложение сути.\nВторое предложение с фактами.\nТретье предложение с выводом."}}

Заголовок оригинала: {title}
Текст: {text}"""

    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "Ты отвечаешь строго в заданном формате без лишних слов."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.2,
            max_tokens=400
        )
        if isinstance(response, str):
            print(f"AI raw string response: {response[:200]}")
            return None
        content = response.choices[0].message.content.strip()
        content = re.sub(r'^```(?:json)?\s*', '', content)
        content = re.sub(r'\s*```$', '', content).strip()

        if content.upper() in ("SPAM", "DUPLICATE"):
            print(f"Filtered by AI ({content.upper()}): {title}")
            return None

        data = json.loads(content)
        if "title" in data and "summary" in data:
            return data
    except Exception as e:
        print(f"AI error for '{title}': {e}")
    return None

def main():
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        print("Error: GITHUB_TOKEN not found.")
        return

    client = OpenAI(
        base_url="https://models.inference.ai.azure.com",
        api_key=token,
    )

    config = load_json(SOURCES_FILE, {})
    feeds = config.get("feeds", [])
    blocked_domains = config.get("blocked_domains", [])
    stop_words = config.get("stop_words", [])
    settings = config.get("settings", {})

    max_items = settings.get("max_items", 50)
    sim_threshold = settings.get("similarity_threshold", 0.6)
    use_spam_filter = settings.get("ai_spam_filter", True)

    existing_news = load_json(NEWS_FILE, [])
    existing_urls = {strip_tracking(item.get("url", "")) for item in existing_news}
    recent_titles = [item.get("title", "") for item in existing_news[:30]]

    new_entries = []

    for feed_info in feeds:
        feed_name = feed_info.get("name", "Источник")
        feed_url = feed_info.get("url", "")
        feed_type = feed_info.get("type", "social")
        if not feed_url:
            continue

        print(f"Checking feed: {feed_name}")
        parsed_feed = feedparser.parse(feed_url)

        for entry in parsed_feed.entries[:5]:
            raw_link = entry.get("link", "")
            clean_link = clean_google_url(raw_link)
            norm_link = strip_tracking(clean_link)
            raw_title = entry.get("title", "").strip()

            if not clean_link or not raw_title:
                continue

            if norm_link in existing_urls:
                continue

            if is_blocked(clean_link, raw_title, blocked_domains, stop_words):
                print(f"Blocked by filter: {raw_title}")
                continue

            if any(jaccard_similarity(raw_title, t) >= sim_threshold for t in recent_titles):
                print(f"Duplicate title skipped: {raw_title}")
                continue

            summary_raw = entry.get("summary", "") or entry.get("description", "")
            if feed_type == "google_alert":
                content_text = fetch_full_text(clean_link, summary_raw)
            else:
                clean_desc = re.sub(r'<[^>]+>', ' ', summary_raw)
                content_text = re.sub(r'\s+', ' ', clean_desc).strip()[:2000]

            if not content_text:
                content_text = raw_title

            ai_result = process_with_ai(client, raw_title, content_text, recent_titles, use_spam_filter)
            if ai_result:
                card = {
                    "title": ai_result["title"],
                    "summary": ai_result["summary"],
                    "url": clean_link,
                    "source": feed_name,
                    "date": datetime.now().strftime("%d.%m.%Y %H:%M")
                }
                new_entries.append(card)
                existing_urls.add(norm_link)
                recent_titles.insert(0, ai_result["title"])
                print(f"Added: {ai_result['title']}")
                time.sleep(2)

    if new_entries:
        updated_news = (new_entries + existing_news)[:max_items]
        save_json(NEWS_FILE, updated_news)
        print(f"Saved {len(new_entries)} new items.")
    else:
        print("No new items added.")

if __name__ == "__main__":
    main()
