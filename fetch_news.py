import json
import os
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime

TOKEN = os.environ.get("GITHUB_TOKEN")

def fetch_rss(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        xml_data = resp.read()
    root = ET.fromstring(xml_data)
    items = []
    for item in root.findall(".//item")[:10]:
        title = item.findtext("title") or ""
        link = item.findtext("link") or ""
        desc = item.findtext("description") or ""
        items.append({"title": title, "link": link.strip(), "text": f"{title}. {desc[:800]}"})
    for entry in root.findall(".//{http://w3.org}entry")[:10]:
        title = entry.findtext("{http://w3.org}title") or ""
        link_el = entry.find("{http://w3.org}link")
        link = link_el.attrib.get("href", "") if link_el is not None else ""
        desc = entry.findtext("{http://w3.org}summary") or ""
        items.append({"title": title, "link": link.strip(), "text": f"{title}. {desc[:800]}"})
    return items

def summarize_and_translate(text):
    url = "https://github.ai"
    prompt = (
        "Переведи суть новости на русский язык. Верни ответ строго в JSON с двумя ключами: "
        "'title' (короткий заголовок) и 'summary' (суть новости ровно в 3 короткие строки без лишних символов)."
    )
    payload = {
        "model": "openai/gpt-4o-mini",
        "messages": [
            {"role": "system", "content": prompt},
            {"role": "user", "content": text}
        ],
        "response_format": {"type": "json_object"}
    }
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {TOKEN}",
            "Content-Type": "application/json"
        }
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        res = json.loads(resp.read().decode("utf-8"))
        return json.loads(res["choices"][0]["message"]["content"])

def main():
    with open("config.json", "r", encoding="utf-8") as f:
        config = json.load(f)
    try:
        with open("news.json", "r", encoding="utf-8") as f:
            news_list = json.load(f)
    except Exception:
        news_list = []

    existing_urls = {n["url"] for n in news_list}
    keywords = [k.lower() for k in config.get("keywords", [])]
    new_items = []

    for feed in config.get("feeds", []):
        try:
            posts = fetch_rss(feed["url"])
            for post in posts:
                if not post["link"] or post["link"] in existing_urls:
                    continue
                if keywords and not any(k in post["text"].lower() for k in keywords):
                    continue
                ai_data = summarize_and_translate(post["text"])
                new_items.append({
                    "title": ai_data.get("title", post["title"]),
                    "summary": ai_data.get("summary", ""),
                    "url": post["link"],
                    "source": feed["name"],
                    "date": datetime.now().strftime("%d.%m.%Y %H:%M")
                })
                existing_urls.add(post["link"])
                if len(new_items) >= 5:
                    break
        except Exception as e:
            print(f"Ошибка в ленте {feed['name']}: {e}")

    if new_items:
        updated = (new_items + news_list)[:50]
        with open("news.json", "w", encoding="utf-8") as f:
            json.dump(updated, f, ensure_ascii=False, indent=2)

if __name__ == "__main__":
    main()
