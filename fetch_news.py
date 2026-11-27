import os
import re
import json
import urllib.parse
from datetime import datetime
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

def clean_url(raw_url):
    if "google." in raw_url and "/url?" in raw_url:
        parsed = urllib.parse.urlparse(raw_url)
        qs = urllib.parse.parse_qs(parsed.query)
        if "url" in qs:
            raw_url = qs["url"][0]
    parsed = urllib.parse.urlparse(raw_url)
    clean_qs = {k: v for k, v in urllib.parse.parse_qsl(parsed.query) if not k.startswith("utm_")}
    return urllib.parse.urlunparse(parsed._replace(query=urllib.parse.urlencode(clean_qs)))

def is_similar(title1, title2, threshold):
    w1 = set(re.findall(r"\w{4,}", title1.lower()))
    w2 = set(re.findall(r"\w{4,}", title2.lower()))
    if not w1 or not w2:
        return False
    inter = len(w1 & w2)
    union = len(w1 | w2)
    return (inter / union) >= threshold

def fetch_full_text(url, fallback_text):
    try:
        resp = requests.get(f"https://r.jina.ai/{url}", timeout=10, headers={"User-Agent": "Mozilla/5.0"})
        if resp.status_code == 200 and len(resp.text.strip()) > 120:
            return resp.text.strip()[:3500]
    except Exception:
        pass
    return fallback_text

def process_with_ai(client, text, recent_titles, use_spam_filter):
    titles_str = "\n".join(f"- {t}" for t in recent_titles[:10]) if recent_titles else "Нет"
    spam_rule = (
        "1. Если текст является скрытой рекламой, продажей курсов, гороскопом, поздравлением, "
        "анонсом стрима без сути или пустым кликбейтом без конкретных фактов — верни ТОЛЬКО одно слово: SPAM\n"
        if use_spam_filter else ""
    )
    prompt = f"""Ты строгий новостной редактор. Проанализируй текст и сделай краткую сводку на русском языке.

Правила:
{spam_rule}2. Если новость описывает то же самое событие, что уже есть в этом списке недавних заголовков:
{titles_str}
— верни ТОЛЬКО одно слово: DUPLICATE
3. Во всех остальных случаях переведи суть на русский язык и верни ответ СТРОГО в формате JSON без маркдауна (без ```json) и без пояснений:
{{"title": "Короткий точный заголовок (до 70 символов)", "summary": "Первое короткое предложение сути.\nВторое предложение с фактами.\nТретье предложение с выводом."}}

Текст:
{text}"""

    try:
        res = client.chat.completions.create(
            model="openai/gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
            max_tokens=400
        )
        ans = res.choices[0].message.content.strip()
        if ans in ("SPAM", "DUPLICATE"):
            return ans
        ans = re.sub(r"^```(?:json)?|```$", "", ans, flags=re.MULTILINE).strip()
        data = json.loads(ans)
        if "title" in data and "summary" in data:
            return data
    except Exception as e:
        print("AI error:", e)
    return None

def main():
    config = load_json(SOURCES_FILE, {})
    feeds = config.get("feeds", [])
    blocked_domains = [d.lower() for d in config.get("blocked_domains", [])]
    stop_words = [w.lower() for w in config.get("stop_words", [])]
    settings = config.get("settings", {})
    max_items = settings.get("max_items", 50)
    sim_threshold = settings.get("similarity_threshold", 0.6)
    use_spam = settings.get("ai_spam_filter", True)

    existing_news = load_json(NEWS_FILE, [])
    existing_urls = {item.get("url") for item in existing_news}
    recent_titles = [item.get("title", "") for item in existing_news[:30]]

    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        print("GITHUB_TOKEN missing")
        return

    client = OpenAI(
        base_url="https://models.github.ai/inference",
        api_key=token,
    )

    new_items = []
    for feed in feeds:
        feed_name = feed.get("name", "Источник")
        feed_url = feed.get("url", "")
        feed_type = feed.get("type", "social")
        if not feed_url:
            continue

        parsed = feedparser.parse(feed_url)
        for entry in parsed.entries[:5]:
            raw_link = entry.get("link", "")
            if not raw_link:
                continue
            url = clean_url(raw_link)
            if url in existing_urls:
                continue

            if any(dom in url.lower() for dom in blocked_domains):
                continue

            raw_title = entry.get("title", "")
            raw_desc = re.sub(r"<[^>]+>", " ", entry.get("summary", entry.get("description", "")))
            combined_low = f"{raw_title} {raw_desc}".lower()

            if any(sw in combined_low for sw in stop_words):
                continue

            if any(is_similar(raw_title, old_t, sim_threshold) for old_t in recent_titles):
                continue

            content = f"{raw_title}\n{raw_desc}".strip()
            if feed_type == "google_alert":
                content = fetch_full_text(url, content)

            ai_res = process_with_ai(client, content[:3500], recent_titles, use_spam)
            if not ai_res or ai_res in ("SPAM", "DUPLICATE"):
                continue

            card = {
                "title": ai_res["title"].strip(),
                "summary": ai_res["summary"].strip(),
                "url": url,
                "source": feed_name,
                "date": datetime.now().strftime("%d.%m.%Y %H:%M")
            }
            new_items.append(card)
            existing_urls.add(url)
            recent_titles.insert(0, card["title"])

    if new_items:
        updated = (new_items + existing_news)[:max_items]
        save_json(NEWS_FILE, updated)
        print(f"Added {len(new_items)} items.")
    else:
        print("No new items.")

if __name__ == "__main__":
    main()
