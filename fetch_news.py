import os
import re
import json
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

def clean_url(url):
    if "google.com/url" in url:
        parsed = urlparse(url)
        qs = parse_qs(parsed.query)
        if "url" in qs:
            url = unquote(qs["url"][0])
        elif "q" in qs:
            url = unquote(qs["q"][0])
    parsed = urlparse(url)
    clean = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
    if parsed.query and "youtube.com" in parsed.netloc:
        qs = parse_qs(parsed.query)
        if "v" in qs:
            clean += f"?v={qs['v'][0]}"
    return clean.rstrip("/")

def get_words(text):
    words = re.findall(r"[а-яА-ЯёЁa-zA-Z0-9]{3,}", text.lower())
    stop = {"для", "что", "как", "это", "или", "при", "the", "and", "for", "with"}
    return set(w for w in words if w not in stop)

def is_similar(title1, title2, threshold=0.6):
    w1, w2 = get_words(title1), get_words(title2)
    if not w1 or not w2:
        return False
    inter = len(w1 & w2)
    union = len(w1 | w2)
    return (inter / union) >= threshold

def fetch_full_article(url, fallback_text):
    try:
        jina_url = f"https://r.jina.ai/{url}"
        resp = requests.get(jina_url, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
        if resp.status_code == 200 and len(resp.text.strip()) > 250:
            return resp.text.strip()[:3500]
    except Exception:
        pass
    return re.sub(r"<[^>]+>", " ", fallback_text).strip()[:2000]

def process_with_ai(client, title, content, recent_titles, topics, use_spam_filter):
    topics_str = ", ".join(topics) if topics else "любые полезные новости"
    recent_str = "\n".join(f"- {t}" for t in recent_titles[:12]) if recent_titles else "Нет"

    spam_rule = (
        f"1. Если текст является скрытой рекламой, продажей курсов, гороскопом, поздравлением, "
        f"анонсом стрима без сути, пустым кликбейтом без фактов или не относится к темам [{topics_str}] — "
        f"верни ТОЛЬКО одно слово: SPAM\n"
        if use_spam_filter else ""
    )

    system_prompt = (
        "Ты строгий новостной редактор. Твоя задача — отфильтровать шум и сделать настоящую аналитическую выжимку сути.\n\n"
        "Правила проверки:\n"
        f"{spam_rule}"
        f"2. Если новость описывает то же самое событие, что уже есть в этом списке недавних заголовков:\n{recent_str}\n"
        "— верни ТОЛЬКО одно слово: DUPLICATE\n"
        "3. Если это полезная уникальная новость — переведи суть на русский язык и переформулируй своими словами "
        "(не копируй первые строчки оригинала!). Верни ответ СТРОГО в формате JSON без маркдауна (без ```json) и без пояснений:\n"
        '{"title": "Короткий точный заголовок на русском (до 70 символов)", '
        '"summary": "Первый тезис: главный факт события.\nВторой тезис: ключевые детали или цифры.\nТретий тезис: практический вывод или значение."}'
    )

    user_prompt = f"Заголовок: {title}\n\nТекст материала:\n{content}"
    models = ["openai/gpt-oss-120b", "llama-3.3-70b-versatile"]

    for model in models:
        try:
            response = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                temperature=0.2,
                max_tokens=400
            )
            if isinstance(response, str):
                print(f"AI string response ({model}): {response[:200]}")
                continue
            raw = response.choices[0].message.content.strip()
            if raw.upper() in ("SPAM", "DUPLICATE"):
                return raw.upper()
            raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.IGNORECASE).strip()
            data = json.loads(raw)
            if "title" in data and "summary" in data:
                return data
        except Exception as e:
            print(f"AI error ({model}): {e}")
            time.sleep(1)
    return None

def main():
    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        print("GROQ_API_KEY is missing!")
        return

    client = OpenAI(base_url="https://api.groq.com/openai/v1", api_key=api_key)
    config = load_json(SOURCES_FILE, {})
    news = load_json(NEWS_FILE, [])

    feeds = config.get("feeds", [])
    blocked_domains = [d.lower() for d in config.get("blocked_domains", [])]
    stop_words = [w.lower() for w in config.get("stop_words", [])]
    topics = config.get("topics", [])
    settings = config.get("settings", {})
    max_items = settings.get("max_items", 50)
    sim_threshold = settings.get("similarity_threshold", 0.6)
    use_spam_filter = settings.get("ai_spam_filter", True)

    existing_urls = {item.get("url") for item in news}
    recent_titles = [item.get("title", "") for item in news[:30]]
    added_count = 0

    for feed_cfg in feeds:
        feed_name = feed_cfg.get("name", "Источник")
        feed_url = feed_cfg.get("url", "")
        feed_type = feed_cfg.get("type", "social")
        if not feed_url:
            continue

        print(f"Checking feed: {feed_name}")
        parsed = feedparser.parse(feed_url)
        for entry in parsed.entries[:8]:
            raw_link = entry.get("link", "")
            if not raw_link:
                continue
            url = clean_url(raw_link)

            if url in existing_urls:
                continue
            if any(bd in url.lower() for bd in blocked_domains):
                continue

            orig_title = entry.get("title", "").strip()
            summary_raw = entry.get("summary", "") or entry.get("description", "")
            combined_lower = (orig_title + " " + summary_raw).lower()

            if any(sw in combined_lower for sw in stop_words):
                continue
            if any(is_similar(orig_title, rt, sim_threshold) for rt in recent_titles):
                continue

            if feed_type == "google_alert":
                content = fetch_full_article(url, summary_raw)
            else:
                content = re.sub(r"<[^>]+>", " ", summary_raw).strip()[:2500]
                if not content:
                    content = orig_title

            ai_res = process_with_ai(client, orig_title, content, recent_titles, topics, use_spam_filter)
            if not ai_res or ai_res in ("SPAM", "DUPLICATE"):
                print(f"Skipped ({ai_res}): {orig_title[:50]}")
                continue

            if any(is_similar(ai_res["title"], rt, sim_threshold) for rt in recent_titles):
                continue

            new_item = {
                "id": url,
                "source": feed_name,
                "date": datetime.now().strftime("%d.%m.%Y %H:%M"),
                "title": ai_res["title"].strip(),
                "summary": ai_res["summary"].strip(),
                "url": url
            }
            news.insert(0, new_item)
            existing_urls.add(url)
            recent_titles.insert(0, new_item["title"])
            added_count += 1
            print(f"Added: {new_item['title']}")
            time.sleep(1.5)

    if added_count > 0:
        save_json(NEWS_FILE, news[:max_items])
        print(f"Saved {added_count} new items.")
    else:
        print("No new items added.")

if __name__ == "__main__":
    main()
