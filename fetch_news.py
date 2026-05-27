#!/usr/bin/env python3
"""
Daily Cybersecurity News Digest
Fetches top 5 stories from RSS feeds, summarizes via Claude API, generates index.html
"""

import os
import feedparser
import anthropic
from datetime import datetime, timezone
from pathlib import Path

# Load .env file if present
env_file = Path(__file__).parent / ".env"
if env_file.exists():
    for line in env_file.read_text().splitlines():
        if "=" in line and not line.startswith("#"):
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip())

RSS_FEEDS = [
    ("The Hacker News",     "https://feeds.feedburner.com/TheHackersNews"),
    ("BleepingComputer",    "https://www.bleepingcomputer.com/feed/"),
    ("SecurityWeek",        "https://feeds.feedburner.com/securityweek"),
    ("Krebs on Security",   "https://krebsonsecurity.com/feed/"),
    ("Dark Reading",        "https://www.darkreading.com/rss/all.xml"),
]

TOP_N = 5


def fetch_entries() -> list[dict]:
    entries = []
    for source, url in RSS_FEEDS:
        try:
            feed = feedparser.parse(url)
            for e in feed.entries[:4]:
                published = e.get("published_parsed") or e.get("updated_parsed")
                summary = e.get("summary", e.get("content", [{}])[0].get("value", ""))
                entries.append({
                    "source": source,
                    "title": e.get("title", ""),
                    "link": e.get("link", ""),
                    "summary_raw": summary,
                    "published": datetime(*published[:6], tzinfo=timezone.utc) if published else datetime.now(timezone.utc),
                })
        except Exception as exc:
            print(f"  [warn] {source}: {exc}")

    entries.sort(key=lambda x: x["published"], reverse=True)
    return entries[:TOP_N]


def summarize(entries: list[dict]) -> list[dict]:
    client = anthropic.Anthropic()
    results = []
    for e in entries:
        prompt = f"""You are a cybersecurity journalist writing for a non-technical audience.

Article title: {e['title']}
Source: {e['source']}
Raw content: {e['summary_raw'][:3000]}

Write a 4–6 sentence plain-English summary that explains:
1. What happened
2. Who is affected
3. Why it matters
4. What people/orgs should do

Be clear, direct, and avoid jargon. Do not use bullet points — write in paragraph form."""

        message = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=512,
            messages=[{"role": "user", "content": prompt}],
        )
        results.append({**e, "summary": message.content[0].text})
        print(f"  ✓ Summarised: {e['title'][:60]}...")
    return results


def render_html(stories: list[dict]) -> str:
    now = datetime.now(timezone.utc).strftime("%B %d, %Y at %H:%M UTC")
    cards = ""
    for i, s in enumerate(stories, 1):
        date_str = s["published"].strftime("%b %d, %Y")
        cards += f"""
        <article class="card">
          <div class="card-header">
            <span class="index">#{i}</span>
            <span class="source">{s['source']}</span>
            <span class="date">{date_str}</span>
          </div>
          <h2 class="card-title">
            <a href="{s['link']}" target="_blank" rel="noopener">{s['title']}</a>
          </h2>
          <p class="card-summary">{s['summary']}</p>
          <a class="read-more" href="{s['link']}" target="_blank" rel="noopener">
            Read full article &rarr;
          </a>
        </article>"""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Daily Cybersecurity Digest</title>
  <style>
    :root {{
      --bg: #0d1117;
      --surface: #161b22;
      --border: #30363d;
      --accent: #58a6ff;
      --accent2: #f78166;
      --text: #e6edf3;
      --muted: #8b949e;
      --tag-bg: #1f2937;
    }}
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{
      background: var(--bg);
      color: var(--text);
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      line-height: 1.7;
      padding: 2rem 1rem;
    }}
    header {{
      max-width: 800px;
      margin: 0 auto 2.5rem;
      border-bottom: 1px solid var(--border);
      padding-bottom: 1.5rem;
    }}
    header h1 {{
      font-size: 1.8rem;
      font-weight: 700;
      color: var(--text);
      display: flex;
      align-items: center;
      gap: 0.5rem;
    }}
    header h1 span.shield {{ color: var(--accent2); }}
    .meta {{
      color: var(--muted);
      font-size: 0.85rem;
      margin-top: 0.4rem;
    }}
    .cards {{ max-width: 800px; margin: 0 auto; display: flex; flex-direction: column; gap: 1.5rem; }}
    .card {{
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: 10px;
      padding: 1.5rem;
      transition: border-color 0.2s;
    }}
    .card:hover {{ border-color: var(--accent); }}
    .card-header {{
      display: flex;
      align-items: center;
      gap: 0.6rem;
      margin-bottom: 0.75rem;
      flex-wrap: wrap;
    }}
    .index {{
      background: var(--accent);
      color: #0d1117;
      font-size: 0.75rem;
      font-weight: 700;
      padding: 2px 8px;
      border-radius: 20px;
    }}
    .source {{
      background: var(--tag-bg);
      color: var(--accent);
      font-size: 0.75rem;
      font-weight: 600;
      padding: 2px 10px;
      border-radius: 20px;
    }}
    .date {{ color: var(--muted); font-size: 0.8rem; margin-left: auto; }}
    .card-title {{
      font-size: 1.1rem;
      font-weight: 600;
      margin-bottom: 0.75rem;
      line-height: 1.4;
    }}
    .card-title a {{
      color: var(--text);
      text-decoration: none;
    }}
    .card-title a:hover {{ color: var(--accent); text-decoration: underline; }}
    .card-summary {{ color: var(--muted); font-size: 0.93rem; margin-bottom: 1rem; }}
    .read-more {{
      display: inline-block;
      color: var(--accent);
      font-size: 0.85rem;
      font-weight: 500;
      text-decoration: none;
    }}
    .read-more:hover {{ text-decoration: underline; }}
    footer {{
      max-width: 800px;
      margin: 3rem auto 0;
      text-align: center;
      color: var(--muted);
      font-size: 0.8rem;
      border-top: 1px solid var(--border);
      padding-top: 1.2rem;
    }}
  </style>
</head>
<body>
  <header>
    <h1><span class="shield">&#x1F6E1;</span> Daily Cybersecurity Digest</h1>
    <p class="meta">Top 5 stories &nbsp;·&nbsp; Last updated: {now} &nbsp;·&nbsp; Summarised by Claude AI</p>
  </header>
  <main class="cards">{cards}
  </main>
  <footer>Auto-generated daily &nbsp;·&nbsp; Sources: The Hacker News, BleepingComputer, SecurityWeek, Krebs on Security, Dark Reading</footer>
</body>
</html>"""


def main():
    print("Fetching RSS feeds...")
    entries = fetch_entries()
    print(f"  {len(entries)} stories selected")

    print("Summarising with Claude...")
    stories = summarize(entries)

    out = Path(__file__).parent / "index.html"
    out.write_text(render_html(stories), encoding="utf-8")
    print(f"\nDone! HTML written to: {out}")


if __name__ == "__main__":
    main()
