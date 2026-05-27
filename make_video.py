#!/usr/bin/env python3
"""
Coralogix CyberTube — Daily Video Generator
Fetches top 5 cybersecurity stories, generates slides + narration,
stitches into one MP4, and updates the CyberTube website.
"""

import os, json, subprocess, shutil, sys, re, asyncio
from datetime import datetime, timezone
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
import feedparser
import edge_tts
from groq import Groq

# ── Directories ───────────────────────────────────────────────────────────────
BASE       = Path(__file__).parent
VIDEOS_DIR = BASE / "videos"
THUMBS_DIR = BASE / "thumbnails"
EPISODES_F = BASE / "episodes.json"

# ── Video settings ────────────────────────────────────────────────────────────
W, H = 1280, 720

# ── Brand colours (Coralogix) ─────────────────────────────────────────────────
BG       = (10,  14, 26)
SURFACE  = (20,  25, 41)
ACCENT   = (255, 107, 53)   # Coralogix orange
TEXT     = (230, 237, 243)
MUTED    = (139, 148, 158)
DARK_TAG = (30,  37,  53)

# ── Edge TTS ─────────────────────────────────────────────────────────────────
VOICE = "en-US-AriaNeural"   # free Microsoft neural voice, no API key needed

# ── RSS sources ───────────────────────────────────────────────────────────────
RSS_FEEDS = [
    ("The Hacker News",   "https://feeds.feedburner.com/TheHackersNews"),
    ("BleepingComputer",  "https://www.bleepingcomputer.com/feed/"),
    ("SecurityWeek",      "https://feeds.feedburner.com/securityweek"),
    ("Krebs on Security", "https://krebsonsecurity.com/feed/"),
]


# ─────────────────────────────────────────────────────────────────────────────
# Fonts
# ─────────────────────────────────────────────────────────────────────────────

def _font(size: int) -> ImageFont.FreeTypeFont:
    for p in [
        "/System/Library/Fonts/Helvetica.ttc",
        "/System/Library/Fonts/Arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]:
        if Path(p).exists():
            return ImageFont.truetype(p, size)
    return ImageFont.load_default()


# ─────────────────────────────────────────────────────────────────────────────
# Drawing helpers
# ─────────────────────────────────────────────────────────────────────────────

def draw_wrapped(draw, text: str, size: int, x: int, y: int,
                 max_w: int, fill, spacing: int = 10) -> int:
    """Draw word-wrapped text, return new y."""
    f = _font(size)
    words = text.split()
    lines, cur = [], ""
    for w in words:
        test = (cur + " " + w).strip()
        bw = draw.textbbox((0, 0), test, font=f)[2]
        if bw <= max_w:
            cur = test
        else:
            if cur:
                lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    for line in lines:
        draw.text((x, y), line, font=f, fill=fill)
        y += draw.textbbox((0, 0), line, font=f)[3] + spacing
    return y


def badge(draw, text: str, x: int, y: int, size: int,
          bg, fg, radius: int = 18) -> int:
    """Draw a pill badge, return right-edge x."""
    f = _font(size)
    tw = draw.textbbox((0, 0), text, font=f)[2]
    pad = 14
    draw.rounded_rectangle([x, y, x + tw + pad * 2, y + size + 10],
                            radius=radius, fill=bg)
    draw.text((x + pad, y + 5), text, font=f, fill=fg)
    return x + tw + pad * 2 + 10


def centered_text(draw, text: str, size: int, y: int, fill):
    f = _font(size)
    tw = draw.textbbox((0, 0), text, font=f)[2]
    draw.text(((W - tw) // 2, y), text, font=f, fill=fill)


# ─────────────────────────────────────────────────────────────────────────────
# Slide factories
# ─────────────────────────────────────────────────────────────────────────────

def _base_image() -> tuple[Image.Image, ImageDraw.ImageDraw]:
    img = Image.new("RGB", (W, H), BG)
    draw = ImageDraw.Draw(img)
    draw.rectangle([0, 0, W, 7], fill=ACCENT)       # top bar
    draw.rectangle([0, H - 7, W, H], fill=ACCENT)   # bottom bar
    return img, draw


def slide_intro(date_str: str) -> Path:
    img, draw = _base_image()

    # Decorative rings
    cx, cy = W // 2, H // 2
    for r in (260, 200, 140):
        draw.ellipse([cx - r, cy - r, cx + r, cy + r],
                     outline=(255, 107, 53, 18), width=1)

    centered_text(draw, "CORALOGIX  CYBERTUBE", 40, cy - 120, ACCENT)
    centered_text(draw, "Daily Cybersecurity Digest", 30, cy - 55, TEXT)
    centered_text(draw, date_str, 24, cy + 10, MUTED)

    # "Top 5 Stories" pill
    f22 = _font(22)
    t = "Top 5 Stories"
    tw = draw.textbbox((0, 0), t, font=f22)[2]
    bx = (W - tw - 36) // 2
    draw.rounded_rectangle([bx, cy + 65, bx + tw + 36, cy + 65 + 34],
                            radius=17, fill=ACCENT)
    draw.text((bx + 18, cy + 70), t, font=f22, fill=BG)

    p = BASE / "_s_intro.png"
    img.save(p)
    return p


def slide_story(n: int, story: dict) -> Path:
    img, draw = _base_image()

    # Subtle column grid
    for gx in range(0, W, 100):
        draw.line([(gx, 0), (gx, H)], fill=(255, 255, 255, 4), width=1)

    # Header row
    draw.text((44, 18), "CORALOGIX CYBERTUBE", font=_font(17), fill=ACCENT)
    date_txt = story["published"].strftime("%b %d, %Y")
    dw = draw.textbbox((0, 0), date_txt, font=_font(17))[2]
    draw.text((W - dw - 44, 18), date_txt, font=_font(17), fill=MUTED)
    draw.rectangle([44, 52, W - 44, 54], fill=DARK_TAG)

    # Number + source badges
    next_x = badge(draw, f"#{n}", 44, 68, 20, ACCENT, BG)
    badge(draw, story["source"], next_x, 68, 18, DARK_TAG, MUTED)

    # Title
    y = 128
    title = story["title"][:95] + ("…" if len(story["title"]) > 95 else "")
    y = draw_wrapped(draw, title, 44, 44, y, W - 88, TEXT, spacing=8)

    # Divider
    y += 14
    draw.rectangle([44, y, W - 44, y + 2], fill=(*ACCENT, 70))
    y += 16

    # Summary (capped to keep it on-slide)
    summary = story["summary"]
    if len(summary) > 430:
        summary = summary[:427] + "…"
    draw_wrapped(draw, summary, 25, 44, y, W - 88, MUTED, spacing=7)

    # Source link at bottom
    link = story["link"][:75] + ("…" if len(story["link"]) > 75 else "")
    draw.text((44, H - 50), f"Read more →  {story['source']}", font=_font(19), fill=ACCENT)

    p = BASE / f"_s_story_{n}.png"
    img.save(p)
    return p


def slide_outro() -> Path:
    img, draw = _base_image()
    centered_text(draw, "Stay Secure.  Stay Informed.", 42, H // 2 - 80, TEXT)
    centered_text(draw, "CORALOGIX  CYBERTUBE", 30, H // 2 - 10, ACCENT)
    centered_text(draw, "New episode every day", 22, H // 2 + 50, MUTED)
    p = BASE / "_s_outro.png"
    img.save(p)
    return p


# ─────────────────────────────────────────────────────────────────────────────
# Audio (Mac say)
# ─────────────────────────────────────────────────────────────────────────────

def speak(text: str, out: Path):
    async def _gen():
        tts = edge_tts.Communicate(text, voice=VOICE)
        await tts.save(str(out))
    asyncio.run(_gen())


# ─────────────────────────────────────────────────────────────────────────────
# FFmpeg helpers
# ─────────────────────────────────────────────────────────────────────────────

def slide_to_mp4(img: Path, audio: Path, out: Path):
    subprocess.run([
        "ffmpeg", "-y",
        "-loop", "1", "-i", str(img),
        "-i", str(audio),
        "-c:v", "libx264", "-tune", "stillimage",
        "-c:a", "aac", "-b:a", "192k",
        "-pix_fmt", "yuv420p",
        "-shortest", str(out),
    ], check=True, capture_output=True)


def concat_mp4s(parts: list[Path], out: Path):
    lst = BASE / "_concat.txt"
    lst.write_text("\n".join(f"file '{p}'" for p in parts))
    subprocess.run([
        "ffmpeg", "-y",
        "-f", "concat", "-safe", "0", "-i", str(lst),
        "-c", "copy", str(out),
    ], check=True, capture_output=True)
    lst.unlink()


def extract_thumb(video: Path, thumb: Path):
    subprocess.run([
        "ffmpeg", "-y", "-i", str(video),
        "-ss", "00:00:04", "-vframes", "1", str(thumb),
    ], check=True, capture_output=True)


# ─────────────────────────────────────────────────────────────────────────────
# News fetcher + summariser
# ─────────────────────────────────────────────────────────────────────────────

def _clean_html(raw: str) -> str:
    """Strip HTML tags and collapse whitespace."""
    text = re.sub(r"<[^>]+>", " ", raw)
    text = re.sub(r"&nbsp;", " ", text)
    text = re.sub(r"&amp;", "&", text)
    text = re.sub(r"&lt;", "<", text)
    text = re.sub(r"&gt;", ">", text)
    text = re.sub(r"&#\d+;", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    # Keep first 3 sentences
    sentences = re.split(r"(?<=[.!?])\s+", text)
    return " ".join(sentences[:4])


def fetch_and_summarise() -> list[dict]:
    # Load .env for GROQ_API_KEY
    env_file = BASE / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            if "=" in line and not line.startswith("#"):
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip())

    entries = []
    for source, url in RSS_FEEDS:
        try:
            feed = feedparser.parse(url)
            for e in feed.entries[:4]:
                pub = e.get("published_parsed") or e.get("updated_parsed")
                raw = e.get("summary", "") or e.get("description", "")
                entries.append({
                    "source":    source,
                    "title":     e.get("title", ""),
                    "link":      e.get("link",  ""),
                    "raw":       _clean_html(raw),
                    "published": datetime(*pub[:6], tzinfo=timezone.utc) if pub
                                 else datetime.now(timezone.utc),
                })
        except Exception as exc:
            print(f"  [warn] {source}: {exc}")

    entries.sort(key=lambda x: x["published"], reverse=True)
    top5 = entries[:5]

    client = Groq(api_key=os.environ["GROQ_API_KEY"])

    for e in top5:
        resp = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            max_tokens=500,
            messages=[{"role": "user", "content": f"""You are a cybersecurity news presenter writing a video script for a non-technical audience.

Article title: {e['title']}
Source: {e['source']}
Content: {e['raw']}

Write a narration script with this EXACT structure (no markdown, no bullet points):

Start with a catchy one-line title that explains the threat in plain English.
Then 3-4 sentences explaining: what happened, who is affected, why it's dangerous, any specific details like CVE numbers or company names.
End with one sentence starting with "What to do:" giving clear action advice.

Keep it conversational and clear — this will be read aloud as a news broadcast. No jargon."""}],
        )
        e["summary"] = resp.choices[0].message.content.strip()
        print(f"  ✓ {e['title'][:58]}…")

    return top5


# ─────────────────────────────────────────────────────────────────────────────
# Website generator
# ─────────────────────────────────────────────────────────────────────────────

def load_episodes() -> list[dict]:
    return json.loads(EPISODES_F.read_text()) if EPISODES_F.exists() else []


def save_episodes(eps: list[dict]):
    EPISODES_F.write_text(json.dumps(eps, indent=2, default=str))


def _featured_html(ep: dict) -> str:
    return f"""
      <div class="featured">
        <div class="video-wrap">
          <video id="main-player" controls poster="{ep['thumbnail']}">
            <source src="{ep['video']}" type="video/mp4">
          </video>
        </div>
        <div class="featured-meta">
          <span class="live-badge">&#9679; Latest</span>
          <h2>{ep['title']}</h2>
          <p class="ep-date">{ep['date']}</p>
          <p class="ep-desc">{ep['description']}</p>
        </div>
      </div>"""


def _card_html(ep: dict) -> str:
    return f"""
        <div class="card" onclick="playEpisode('{ep['video']}','{ep['thumbnail']}')">
          <div class="thumb-wrap">
            <img src="{ep['thumbnail']}" alt="{ep['title']}" loading="lazy">
            <div class="play-overlay">&#9654;</div>
          </div>
          <div class="card-body">
            <p class="card-date">{ep['date']}</p>
            <p class="card-title">{ep['title']}</p>
          </div>
        </div>"""


def regenerate_site(eps: list[dict]):
    if not eps:
        return
    featured = _featured_html(eps[0])
    cards = "".join(_card_html(e) for e in eps[1:])
    past = f"""
  <section class="past">
    <h3 class="section-label">Past Episodes</h3>
    <div class="grid">{cards}
    </div>
  </section>""" if cards else ""

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8"/>
  <meta name="viewport" content="width=device-width,initial-scale=1"/>
  <title>Coralogix CyberTube</title>
  <style>
    :root{{
      --bg:#0A0E1A;--surface:#141929;--border:#1E2535;
      --accent:#1CF09A;--text:#E6EDF3;--muted:#8B949E;
    }}
    *{{box-sizing:border-box;margin:0;padding:0}}
    body{{background:var(--bg);color:var(--text);font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;min-height:100vh}}

    /* Header */
    header{{background:var(--surface);border-bottom:3px solid var(--accent);height:62px;display:flex;align-items:center;justify-content:space-between;padding:0 2rem;position:sticky;top:0;z-index:99}}
    .logo{{font-size:1.25rem;font-weight:800;letter-spacing:.3px;display:flex;align-items:center;gap:.4rem}}
    .logo .shield{{font-size:1.4rem}}
    .logo .cx{{color:var(--accent)}}
    .logo .sub{{font-size:.72rem;color:var(--muted);font-weight:400;margin-left:.2rem}}
    .hdr-right{{font-size:.8rem;color:var(--muted)}}

    /* Layout */
    main{{max-width:1140px;margin:0 auto;padding:2rem 1.5rem}}

    /* Featured */
    .featured{{display:grid;grid-template-columns:1fr 320px;gap:1.5rem;background:var(--surface);border:1px solid var(--border);border-radius:12px;overflow:hidden;margin-bottom:3rem}}
    .video-wrap{{background:#000}}
    #main-player{{width:100%;display:block;aspect-ratio:16/9}}
    .featured-meta{{padding:1.5rem;display:flex;flex-direction:column;gap:.8rem}}
    .live-badge{{display:inline-flex;align-items:center;gap:.4rem;background:var(--accent);color:#0A0E1A;font-size:.7rem;font-weight:700;padding:3px 10px;border-radius:20px;width:fit-content;text-transform:uppercase;letter-spacing:.5px}}
    .featured-meta h2{{font-size:1.1rem;line-height:1.5}}
    .ep-date{{font-size:.8rem;color:var(--muted)}}
    .ep-desc{{font-size:.87rem;color:var(--muted);line-height:1.65}}

    /* Past */
    .section-label{{font-size:.85rem;font-weight:700;text-transform:uppercase;letter-spacing:1px;color:var(--muted);border-bottom:1px solid var(--border);padding-bottom:.6rem;margin-bottom:1.2rem}}
    .grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(210px,1fr));gap:1.2rem}}
    .card{{background:var(--surface);border:1px solid var(--border);border-radius:10px;overflow:hidden;cursor:pointer;transition:border-color .2s,transform .15s}}
    .card:hover{{border-color:var(--accent);transform:translateY(-3px)}}
    .thumb-wrap{{position:relative;aspect-ratio:16/9;background:#000;overflow:hidden}}
    .thumb-wrap img{{width:100%;height:100%;object-fit:cover;display:block}}
    .play-overlay{{position:absolute;inset:0;display:flex;align-items:center;justify-content:center;font-size:2.4rem;color:#fff;background:rgba(0,0,0,.35);opacity:0;transition:opacity .2s}}
    .card:hover .play-overlay{{opacity:1}}
    .card-body{{padding:.75rem}}
    .card-date{{font-size:.73rem;color:var(--muted);margin-bottom:.2rem}}
    .card-title{{font-size:.88rem;font-weight:600;line-height:1.4}}

    /* Footer */
    footer{{text-align:center;padding:2rem 1rem;color:var(--muted);font-size:.78rem;border-top:1px solid var(--border);margin-top:3rem}}

    @media(max-width:700px){{.featured{{grid-template-columns:1fr}}}}
  </style>
</head>
<body>
<header>
  <div class="logo">
    <svg width="34" height="24" viewBox="0 0 34 24" style="flex-shrink:0">
      <rect width="34" height="24" rx="6" fill="#1CF09A"/>
      <polygon points="13,6 13,18 24,12" fill="#0A0E1A"/>
    </svg>
    <span><span class="cx">Coralogix</span> CyberTube<span class="sub">Daily Security Digest</span></span>
  </div>
  <div class="hdr-right">Updated daily &nbsp;·&nbsp; Powered by Claude AI</div>
</header>
<main>
  <section>{featured}
  </section>{past}
</main>
<footer>Coralogix CyberTube &nbsp;·&nbsp; Sources: The Hacker News · BleepingComputer · SecurityWeek · Krebs on Security &nbsp;·&nbsp; Summaries by Claude AI</footer>
<script>
  function playEpisode(src, poster) {{
    var p = document.getElementById('main-player');
    p.poster = poster;
    p.src = src;
    p.play();
    window.scrollTo({{top: 0, behavior: 'smooth'}});
  }}
</script>
</body>
</html>"""
    (BASE / "index.html").write_text(html, encoding="utf-8")
    print(f"  ✓ index.html written ({len(eps)} episode(s))")


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

def main():
    if not shutil.which("ffmpeg"):
        sys.exit("ERROR: ffmpeg not found. Install with:  brew install ffmpeg")

    VIDEOS_DIR.mkdir(exist_ok=True)
    THUMBS_DIR.mkdir(exist_ok=True)

    today    = datetime.now(timezone.utc)
    date_str = today.strftime("%B %d, %Y")
    slug     = today.strftime("%Y-%m-%d")
    final    = VIDEOS_DIR / f"{slug}.mp4"
    thumb    = THUMBS_DIR / f"{slug}.jpg"

    if final.exists():
        print(f"Today's video already exists: {final.name}")
        return

    print("Fetching & summarising top 5 stories…")
    stories = fetch_and_summarise()

    print("\nBuilding video segments…")
    segs = []

    # Intro
    print("  [intro]")
    s_intro = slide_intro(date_str)
    a_intro = BASE / "_a_intro.mp3"
    speak(f"Welcome to Coralogix CyberTube. Here are today's top 5 cybersecurity stories for {date_str}.", a_intro)
    v_intro = BASE / "_v_intro.mp4"
    slide_to_mp4(s_intro, a_intro, v_intro)
    segs.append(v_intro)

    # Stories
    for i, story in enumerate(stories, 1):
        print(f"  [story {i}] {story['title'][:52]}…")
        s = slide_story(i, story)
        a = BASE / f"_a_{i}.mp3"
        v = BASE / f"_v_{i}.mp4"
        narration = (f"Story {i}. {story['title']}. "
                     f"{story['summary']} "
                     f"Source: {story['source']}.")
        speak(narration, a)
        slide_to_mp4(s, a, v)
        segs.append(v)

    # Outro
    print("  [outro]")
    s_outro = slide_outro()
    a_outro = BASE / "_a_outro.mp3"
    speak("That's all for today's Coralogix CyberTube digest. Stay secure and stay informed. See you tomorrow.", a_outro)
    v_outro = BASE / "_v_outro.mp4"
    slide_to_mp4(s_outro, a_outro, v_outro)
    segs.append(v_outro)

    print(f"\nConcatenating → {final.name}…")
    concat_mp4s(segs, final)

    print("Extracting thumbnail…")
    extract_thumb(final, thumb)

    print("Cleaning up temp files…")
    for f in BASE.glob("_[asv]_*.{png,mp3,mp4}"):
        f.unlink(missing_ok=True)
    for f in BASE.glob("_s_*.png"):
        f.unlink(missing_ok=True)

    print("Updating website…")
    eps = load_episodes()
    eps.insert(0, {
        "date":        date_str,
        "slug":        slug,
        "video":       f"videos/{slug}.mp4",
        "thumbnail":   f"thumbnails/{slug}.jpg",
        "title":       f"Top 5 Cyber Stories — {date_str}",
        "description": "  ·  ".join(s["title"][:42] for s in stories[:3]) + "…",
    })
    save_episodes(eps)
    regenerate_site(eps)

    size_mb = final.stat().st_size / 1_048_576
    print(f"\n✅  Done!  {final.name}  ({size_mb:.1f} MB)")
    print(f"   Open index.html in your browser to preview CyberTube.")


if __name__ == "__main__":
    main()
