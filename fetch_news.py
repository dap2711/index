import json
import os
import re
import time
from datetime import datetime
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse
import feedparser
import requests
from openai import OpenAI

SOURCES_FILE = "sources.json"
NEWS_FILE = "news.json"

GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
if not GROQ_API_KEY:
    raise ValueError("GROQ_API_KEY environment variable is missing!")

client = OpenAI(
    base_url="https://api.groq.com/openai/v1",
    api_key=GROQ_API_KEY,
)

MODELS = ["openai/gpt-oss-120b", "llama-3.3-70b-versatile"]


def load_json(path, default):
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            try:
                return json.load(f)
            except json.JSONDecodeError:
                return default
    return default


def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def clean_url(raw_url):
    parsed = urlparse(raw_url)
    if "google." in parsed.netloc and parsed.path == "/url":
        qs = parse_qs(parsed.query)
        if "url" in qs:
            raw_url = qs["url"][0]
            parsed = urlparse(raw_url)
    qs = parse_qs(parsed.query)
    clean_qs = {k: v for k, v in qs.items() if not k.startswith("utm_")}
    return urlunparse(parsed._replace(query=urlencode(clean_qs, doseq=True)))


def is_blocked(url, text, blocked_domains, stop_words):
    url_lower = url.lower()
    for domain in blocked_domains:
        if domain.lower() in url_lower:
            return True
    text_lower = text.lower()
    for word in stop_words:
        if word.lower() in text_lower:
            return True
    return False


def normalize_words(text):
    words = re.findall(r"[а-яА-ЯёЁa-zA-Z0-9]{3,}", text.lower())
    return set(words)


def is_similar_title(title, existing_titles, threshold):
    new_words = normalize_words(title)
    if not new_words:
        return False
    for old_title in existing_titles:
        old_words = normalize_words(old_title)
        if not old_words:
            continue
        intersection = len(new_words & old_words)
        union = len(new_words | old_words)
        if union > 0 and (intersection / union) >= threshold:
            return True
    return False


def fetch_full_text(url, fallback_text):
    try:
        jina_url = f"https://r.jina.ai/{url}"
        resp = requests.get(jina_url, timeout=10)
        if resp.status_code == 200 and len(resp.text.strip()) > 200:
            return resp.text.strip()[:3500]
    except Exception:
        pass
    clean_fallback = re.sub(r"<[^>]+>", " ", fallback_text)
    return re.sub(r"\s+", " ", clean_fallback).strip()[:2500]


def process_with_ai(title, content, recent_titles, use_spam_filter):
    recent_block = "\n".join(f"- {t}" for t in recent_titles[:10]) if recent_titles else "Нет"
    spam_rule = (
        "1. Если текст является скрытой рекламой, продажей курсов, гороскопом, поздравлением, "
        "анонсом стрима без сути или пустым кликбейтом без фактов — верни ТОЛЬКО одно слово: SPAM\n"
        if use_spam_filter
        else ""
    )

    prompt = (
        "Ты строгий новостной редактор. Проанализируй новость, переведи суть на русский язык и сделай краткую сводку.\n\n"
        "Правила:\n"
        f"{spam_rule}"
        f"2. Если новость описывает то же самое событие, что уже есть в списке недавних заголовков:\n{recent_block}\n"
        "— верни ТОЛЬКО одно слово: DUPLICATE\n"
        "3. Во всех остальных случаях верни ответ СТРОГО в формате чистого JSON без markdown-обертки (без ```json) и без пояснений:\n"
        '{"title": "Короткий точный заголовок на русском (до 75 символов)", '
        '"summary": "Первое предложение сути на русском.\nВторое предложение с ключевыми фактами.\nТретье предложение с выводом."}'
    )

    user_msg = f"Заголовок: {title}\n\nТекст:\n{content}"

    for model_name in MODELS:
        try:
            response = client.chat.completions.create(
                model=model_name,
                messages=[
                    {"role": "system", "content": prompt},
                    {"role": "user", "content": user_msg},
                ],
                temperature=0.2,
                max_tokens=400,
            )
            raw = response.choices[0].message.content.strip()
            if raw.upper() in ("SPAM", "DUPLICATE"):
                return raw.upper()
            raw_clean = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.IGNORECASE).strip()
            data = json.loads(raw_clean)
            if "title" in data and "summary" in data:
                return data
        except Exception as e:
            print(f"Model {model_name} error for '{title}': {e}")
            time.sleep(2)
    return None


def main():
    config = load_json(SOURCES_FILE, {})
    feeds = config.get("feeds", [])
    blocked_domains = config.get("blocked_domains", [])
    stop_words = config.get("stop_words", [])
    settings = config.get("settings", {})

    max_items = settings.get("max_items", 50)
    similarity_threshold = settings.get("similarity_threshold", 0.6)
    use_spam_filter = settings.get("ai_spam_filter", True)

    news_items = load_json(NEWS_FILE, [])
    existing_urls = {item.get("url") for item in news_items}
    existing_titles = [item.get("title", "") for item in news_items]

    new_entries = []

    for feed_cfg in feeds:
        feed_name = feed_cfg.get("name", "Источник")
        feed_url = feed_cfg.get("url")
        feed_type = feed_cfg.get("type", "rss")
        if not feed_url:
            continue

        print(f"Checking feed: {feed_name}")
        parsed = feedparser.parse(feed_url)

        for entry in parsed.entries[:5]:
            raw_link = entry.get("link", "")
            if not raw_link:
                continue
            link = clean_url(raw_link)

            if link in existing_urls:
                continue

            raw_title = re.sub(r"<[^>]+>", "", entry.get("title", "")).strip()
            raw_summary = entry.get("summary", entry.get("description", ""))

            if is_blocked(link, f"{raw_title} {raw_summary}", blocked_domains, stop_words):
                continue

            if is_similar_title(raw_title, existing_titles, similarity_threshold):
                continue

            if feed_type == "google_alert":
                article_text = fetch_full_text(link, raw_summary)
            else:
                clean_desc = re.sub(r"<[^>]+>", " ", raw_summary)
                article_text = re.sub(r"\s+", " ", clean_desc).strip()[:2500] or raw_title

            ai_result = process_with_ai(raw_title, article_text, existing_titles, use_spam_filter)
            time.sleep(2)

            if not ai_result or isinstance(ai_result, str):
                print(f"Skipped ({ai_result}): {raw_title}")
                continue

            card = {
                "title": ai_result["title"].strip(),
                "summary": ai_result["summary"].strip(),
                "url": link,
                "source": feed_name,
                "date": datetime.now().strftime("%d.%m.%Y %H:%M"),
            }

            new_entries.append(card)
            existing_urls.add(link)
            existing_titles.insert(0, card["title"])
            print(f"Added: {card['title']}")

    if new_entries:
        updated_news = (new_entries + news_items)[:max_items]
        save_json(NEWS_FILE, updated_news)
        print(f"Saved {len(new_entries)} new items.")
    else:
        print("No new items added.")


if __name__ == "__main__":
    main()
