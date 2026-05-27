#!/usr/bin/env python3
"""
Coralogix CyberTube — Daily Video Generator v2
Light theme · Two-host podcast narration · Overview card grid
"""

import os, json, subprocess, shutil, sys, re, asyncio
from datetime import datetime, timezone
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
import feedparser
import edge_tts
from groq import Groq

# ── Paths ─────────────────────────────────────────────────────────────────────
BASE       = Path(__file__).parent
VIDEOS_DIR = BASE / "videos"
THUMBS_DIR = BASE / "thumbnails"
EPISODES_F = BASE / "episodes.json"

W, H = 1280, 720

# ── Light theme ───────────────────────────────────────────────────────────────
BG       = (245, 247, 250)   # off-white page
CARD     = (255, 255, 255)   # white cards
ACCENT   = ( 28, 220, 140)   # Coralogix green
ACCENT_D = ( 14, 160, 100)   # darker green for text on white
TEXT     = ( 15,  23,  42)   # near-black
MUTED    = (100, 116, 139)   # slate gray
BORDER   = (220, 230, 240)   # card border
SHADOW   = (200, 210, 225)   # drop-shadow color

# ── Voices ────────────────────────────────────────────────────────────────────
VOICE_A = "en-US-AriaNeural"   # female host — energetic
VOICE_B = "en-US-GuyNeural"    # male host   — analytical
RATE    = "+20%"                # faster pace

# ── RSS feeds ─────────────────────────────────────────────────────────────────
RSS_FEEDS = [
    ("The Hacker News",   "https://feeds.feedburner.com/TheHackersNews"),
    ("BleepingComputer",  "https://www.bleepingcomputer.com/feed/"),
    ("SecurityWeek",      "https://feeds.feedburner.com/securityweek"),
    ("Krebs on Security", "https://krebsonsecurity.com/feed/"),
]

# Story-type icons (text-based, always render)
ICONS = ["⚠", "☠", "⚡", "⛔", "🔒"]


# ─────────────────────────────────────────────────────────────────────────────
# Fonts
# ─────────────────────────────────────────────────────────────────────────────

def _font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    candidates = [
        "/System/Library/Fonts/Helvetica.ttc",
        "/System/Library/Fonts/Arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    for p in candidates:
        if Path(p).exists():
            return ImageFont.truetype(p, size)
    return ImageFont.load_default()


# ─────────────────────────────────────────────────────────────────────────────
# Drawing helpers
# ─────────────────────────────────────────────────────────────────────────────

def draw_shadow_card(draw, x0, y0, x1, y1, radius=14):
    """White card with subtle drop shadow."""
    for off in range(6, 0, -1):
        alpha = int(30 + off * 8)
        c = tuple([max(0, v - 20) for v in BG]) + (alpha,)
        draw.rounded_rectangle([x0+off, y0+off, x1+off, y1+off],
                                radius=radius, fill=SHADOW)
    draw.rounded_rectangle([x0, y0, x1, y1], radius=radius, fill=CARD,
                            outline=BORDER, width=2)


def draw_wrapped(draw, text, size, x, y, max_w, fill, spacing=8, bold=False):
    f = _font(size, bold)
    lines, cur = [], ""
    for w in text.split():
        test = (cur + " " + w).strip()
        if draw.textbbox((0,0), test, font=f)[2] <= max_w:
            cur = test
        else:
            if cur: lines.append(cur)
            cur = w
    if cur: lines.append(cur)
    for line in lines:
        draw.text((x, y), line, font=f, fill=fill)
        y += draw.textbbox((0,0), line, font=f)[3] + spacing
    return y


def centered(draw, text, size, y, fill, bold=False):
    f = _font(size, bold)
    tw = draw.textbbox((0,0), text, font=f)[2]
    draw.text(((W - tw)//2, y), text, font=f, fill=fill)


def _deco(draw):
    """Scattered decorative circles in the background."""
    import random
    random.seed(42)
    for _ in range(18):
        cx = random.randint(0, W)
        cy = random.randint(0, H)
        r  = random.randint(20, 80)
        a  = random.randint(8, 22)
        col = (*ACCENT, a)
        draw.ellipse([cx-r, cy-r, cx+r, cy+r], outline=col, width=2)
    for _ in range(10):
        cx = random.randint(0, W)
        cy = random.randint(0, H)
        r  = random.randint(6, 20)
        draw.ellipse([cx-r, cy-r, cx+r, cy+r],
                     fill=(*BORDER, 60))


# ─────────────────────────────────────────────────────────────────────────────
# Slides
# ─────────────────────────────────────────────────────────────────────────────

def _base() -> tuple[Image.Image, ImageDraw.ImageDraw]:
    img  = Image.new("RGBA", (W, H), (*BG, 255))
    draw = ImageDraw.Draw(img, "RGBA")
    _deco(draw)
    # top green bar
    draw.rectangle([0, 0, W, 6], fill=(*ACCENT, 255))
    # bottom green bar
    draw.rectangle([0, H-6, W, H], fill=(*ACCENT, 255))
    return img.convert("RGB"), ImageDraw.Draw(img.convert("RGB"))


def _header(draw, date_str):
    logo = "CORALOGIX  CYBERTUBE"
    draw.text((44, 16), logo, font=_font(18), fill=ACCENT_D)
    dw = draw.textbbox((0,0), date_str, font=_font(16))[2]
    draw.text((W - dw - 44, 18), date_str, font=_font(16), fill=MUTED)
    draw.rectangle([44, 50, W-44, 51], fill=(*BORDER, 200))


def slide_overview(stories: list[dict], date_str: str) -> Path:
    img, draw = _base()
    _header(draw, date_str)

    centered(draw, "Today's Top 5 Cyber Stories", 34, 68, TEXT)

    # 5 cards: 3 top row, 2 bottom row
    card_w, card_h = 360, 220
    gap = 24
    top_y   = 130
    bot_y   = top_y + card_h + gap
    positions = [
        (44,                  top_y),
        (44 + card_w + gap,   top_y),
        (44 + (card_w+gap)*2, top_y),
        (W//2 - card_w - gap//2, bot_y),
        (W//2 + gap//2,          bot_y),
    ]

    for i, (story, (cx, cy)) in enumerate(zip(stories, positions), 1):
        draw_shadow_card(draw, cx, cy, cx+card_w, cy+card_h, radius=14)
        # Number badge
        num = f"0{i}"
        draw.text((cx+16, cy+14), num, font=_font(28), fill=ACCENT_D)
        # Divider
        draw.rectangle([cx+16, cy+52, cx+card_w-16, cy+53], fill=BORDER)
        # Title
        title = story["title"][:60] + ("…" if len(story["title"]) > 60 else "")
        draw_wrapped(draw, title, 20, cx+16, cy+62, card_w-32, TEXT, spacing=5)
        # Source
        draw.text((cx+16, cy+card_h-28), story["source"],
                  font=_font(14), fill=MUTED)

    p = BASE / "_s_overview.png"
    img.save(p)
    return p


def slide_story(n: int, story: dict, date_str: str) -> Path:
    img, draw = _base()
    _header(draw, date_str)

    # Large icon + story number on left panel
    icon = ICONS[(n-1) % len(ICONS)]
    draw_shadow_card(draw, 44, 68, 340, H-40, radius=16)

    # Colored accent strip on left card
    draw.rounded_rectangle([44, 68, 80, H-40], radius=16,
                            fill=(*ACCENT, 220))

    # Story number
    draw.text((90, 90), f"0{n}", font=_font(56), fill=ACCENT_D)
    # Icon
    draw.text((100, 165), icon, font=_font(72), fill=TEXT)
    # Source
    src = story["source"]
    sw = draw.textbbox((0,0), src, font=_font(16))[2]
    draw.text((192 - sw//2, H-100), src, font=_font(16), fill=MUTED)

    # Right content card
    draw_shadow_card(draw, 360, 68, W-44, H-40, radius=16)

    # Title
    y = 90
    y = draw_wrapped(draw, story["title"], 34, 384, y, W-44-384-20, TEXT, spacing=8)

    # Divider
    y += 10
    draw.rectangle([384, y, W-64, y+2], fill=(*ACCENT, 180))
    y += 16

    # Summary
    y = draw_wrapped(draw, story["summary"], 21, 384, y, W-44-384-20, MUTED, spacing=6)

    # "What to do" if present
    if "What to do:" in story.get("script", ""):
        lines = story["script"].split("\n")
        todo = next((l for l in lines if "What to do:" in l), "")
        if todo:
            todo = re.sub(r"HOST_[AB]:\s*", "", todo).strip()
            y += 12
            draw.rounded_rectangle([384, y, W-64, y+50], radius=8,
                                    fill=(*ACCENT, 30), outline=(*ACCENT, 100), width=1)
            draw.text((400, y+12), todo[:90], font=_font(18), fill=ACCENT_D)

    p = BASE / f"_s_story_{n}.png"
    img.save(p)
    return p


def slide_outro(date_str: str) -> Path:
    img, draw = _base()
    draw.rectangle([0, 0, W, 6], fill=(*ACCENT, 255))
    draw.rectangle([0, H-6, W, H], fill=(*ACCENT, 255))
    draw_shadow_card(draw, W//2-320, H//2-120, W//2+320, H//2+120, radius=20)
    centered(draw, "Stay Secure.  Stay Informed.", 38, H//2-90, TEXT)
    centered(draw, "CORALOGIX  CYBERTUBE", 26, H//2-20, ACCENT_D)
    centered(draw, "New episode every day  ·  " + date_str, 18, H//2+30, MUTED)
    p = BASE / "_s_outro.png"
    img.save(p)
    return p


# ─────────────────────────────────────────────────────────────────────────────
# Audio — two-host dialogue
# ─────────────────────────────────────────────────────────────────────────────

async def _speak_line(text: str, voice: str, out: Path):
    tts = edge_tts.Communicate(text, voice=voice, rate=RATE)
    await tts.save(str(out))


def speak_dialogue(script: str, out: Path):
    """Parse HOST_A/HOST_B script and interleave two voices into one MP3."""
    lines = []
    for raw in script.strip().splitlines():
        raw = raw.strip()
        if not raw:
            continue
        if raw.startswith("HOST_A:"):
            lines.append(("A", raw[7:].strip()))
        elif raw.startswith("HOST_B:"):
            lines.append(("B", raw[7:].strip()))

    if not lines:   # fallback: single voice
        asyncio.run(_speak_line(script, VOICE_A, out))
        return

    async def _gen_all():
        parts = []
        for i, (host, text) in enumerate(lines):
            p = BASE / f"_dl_{i}.mp3"
            voice = VOICE_A if host == "A" else VOICE_B
            await _speak_line(text, voice, p)
            parts.append(p)
        return parts

    parts = asyncio.run(_gen_all())

    # Concatenate with ffmpeg
    lst = BASE / "_dl_list.txt"
    lst.write_text("\n".join(f"file '{p}'" for p in parts))
    subprocess.run([
        "ffmpeg", "-y", "-f", "concat", "-safe", "0",
        "-i", str(lst), "-c", "copy", str(out),
    ], check=True, capture_output=True)
    lst.unlink()
    for p in parts:
        p.unlink(missing_ok=True)


def speak_single(text: str, out: Path):
    asyncio.run(_speak_line(text, VOICE_A, out))


# ─────────────────────────────────────────────────────────────────────────────
# FFmpeg
# ─────────────────────────────────────────────────────────────────────────────

def slide_to_mp4(img: Path, audio: Path, out: Path):
    subprocess.run([
        "ffmpeg", "-y",
        "-loop", "1", "-i", str(img),
        "-i", str(audio),
        "-c:v", "libx264", "-tune", "stillimage",
        "-c:a", "aac", "-b:a", "192k",
        "-pix_fmt", "yuv420p", "-shortest", str(out),
    ], check=True, capture_output=True)


def concat_mp4s(parts: list[Path], out: Path):
    lst = BASE / "_concat.txt"
    lst.write_text("\n".join(f"file '{p}'" for p in parts))
    subprocess.run([
        "ffmpeg", "-y", "-f", "concat", "-safe", "0",
        "-i", str(lst), "-c", "copy", str(out),
    ], check=True, capture_output=True)
    lst.unlink()


def extract_thumb(video: Path, thumb: Path):
    subprocess.run([
        "ffmpeg", "-y", "-i", str(video),
        "-ss", "00:00:04", "-vframes", "1", str(thumb),
    ], check=True, capture_output=True)


# ─────────────────────────────────────────────────────────────────────────────
# News + Groq
# ─────────────────────────────────────────────────────────────────────────────

def _load_env():
    env = BASE / ".env"
    if env.exists():
        for line in env.read_text().splitlines():
            if "=" in line and not line.startswith("#"):
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip())


def _clean(raw: str) -> str:
    text = re.sub(r"<[^>]+>", " ", raw)
    text = re.sub(r"&\w+;", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def fetch_and_summarise() -> list[dict]:
    _load_env()
    entries = []
    for source, url in RSS_FEEDS:
        try:
            feed = feedparser.parse(url)
            for e in feed.entries[:4]:
                pub = e.get("published_parsed") or e.get("updated_parsed")
                entries.append({
                    "source":    source,
                    "title":     e.get("title", ""),
                    "link":      e.get("link",  ""),
                    "raw":       _clean(e.get("summary", "")),
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
            max_tokens=600,
            messages=[{"role": "user", "content": f"""You are writing a script for a two-host cybersecurity podcast called Coralogix CyberTube.

Article title: {e['title']}
Source: {e['source']}
Content: {e['raw']}

Write a punchy two-host conversation script. Use EXACTLY this format, nothing else:

HOST_A: [line]
HOST_B: [line]
HOST_A: [line]
...

Rules:
- 8 to 10 exchanges total
- HOST_A is energetic, uses exclamations, reacts with surprise
- HOST_B is analytical, gives context and details
- Cover: what happened, who is affected, why it is dangerous, specific details
- Last HOST_A line must start with "What to do:" and give one clear action
- Natural spoken language, no bullet points, no markdown, no em-dashes
- Keep each line under 25 words"""}],
        )
        script = resp.choices[0].message.content.strip()
        # Slide summary: first 3 sentences from raw content
        sentences = re.split(r'(?<=[.!?])\s+', e["raw"])
        e["summary"] = " ".join(sentences[:3])
        e["script"]  = script
        print(f"  ✓ {e['title'][:58]}…")

    return top5


# ─────────────────────────────────────────────────────────────────────────────
# Website
# ─────────────────────────────────────────────────────────────────────────────

def load_episodes() -> list[dict]:
    return json.loads(EPISODES_F.read_text()) if EPISODES_F.exists() else []


def save_episodes(eps: list[dict]):
    EPISODES_F.write_text(json.dumps(eps, indent=2, default=str))


def regenerate_site(eps: list[dict]):
    if not eps:
        return
    e = eps[0]
    featured = f"""
      <div class="featured">
        <div class="video-wrap">
          <video id="main-player" controls poster="{e['thumbnail']}">
            <source src="{e['video']}" type="video/mp4">
          </video>
        </div>
        <div class="featured-meta">
          <span class="live-badge">&#9679; Latest</span>
          <h2>{e['title']}</h2>
          <p class="ep-date">{e['date']}</p>
          <p class="ep-desc">{e['description']}</p>
        </div>
      </div>"""

    cards = "".join(f"""
        <div class="card" onclick="playEpisode('{ep['video']}','{ep['thumbnail']}')">
          <div class="thumb-wrap">
            <img src="{ep['thumbnail']}" alt="{ep['title']}" loading="lazy">
            <div class="play-overlay">&#9654;</div>
          </div>
          <div class="card-body">
            <p class="card-date">{ep['date']}</p>
            <p class="card-title">{ep['title']}</p>
          </div>
        </div>""" for ep in eps[1:])

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
    :root{{--bg:#0A0E1A;--surface:#141929;--border:#1E2535;--accent:#1CF09A;--text:#E6EDF3;--muted:#8B949E;}}
    *{{box-sizing:border-box;margin:0;padding:0}}
    body{{background:var(--bg);color:var(--text);font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;min-height:100vh}}
    header{{background:var(--surface);border-bottom:3px solid var(--accent);height:62px;display:flex;align-items:center;justify-content:space-between;padding:0 2rem;position:sticky;top:0;z-index:99}}
    .logo{{font-size:1.25rem;font-weight:800;letter-spacing:.3px;display:flex;align-items:center;gap:.5rem}}
    .logo .cx{{color:var(--accent)}}
    .logo .sub{{font-size:.72rem;color:var(--muted);font-weight:400;margin-left:.2rem}}
    .hdr-right{{font-size:.8rem;color:var(--muted)}}
    main{{max-width:1140px;margin:0 auto;padding:2rem 1.5rem}}
    .featured{{display:grid;grid-template-columns:1fr 320px;gap:1.5rem;background:var(--surface);border:1px solid var(--border);border-radius:12px;overflow:hidden;margin-bottom:3rem}}
    .video-wrap{{background:#000}}
    #main-player{{width:100%;display:block;aspect-ratio:16/9}}
    .featured-meta{{padding:1.5rem;display:flex;flex-direction:column;gap:.8rem}}
    .live-badge{{display:inline-flex;align-items:center;gap:.4rem;background:var(--accent);color:#0A0E1A;font-size:.7rem;font-weight:700;padding:3px 10px;border-radius:20px;width:fit-content;text-transform:uppercase;letter-spacing:.5px}}
    .featured-meta h2{{font-size:1.1rem;line-height:1.5}}
    .ep-date{{font-size:.8rem;color:var(--muted)}}
    .ep-desc{{font-size:.87rem;color:var(--muted);line-height:1.65}}
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
<footer>Coralogix CyberTube &nbsp;·&nbsp; The Hacker News · BleepingComputer · SecurityWeek · Krebs on Security</footer>
<script>
  function playEpisode(src, poster) {{
    var p = document.getElementById('main-player');
    p.poster = poster; p.src = src; p.play();
    window.scrollTo({{top:0, behavior:'smooth'}});
  }}
</script>
</body>
</html>"""
    (BASE / "index.html").write_text(html, encoding="utf-8")
    print(f"  ✓ index.html updated ({len(eps)} episode(s))")


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

def main():
    if not shutil.which("ffmpeg"):
        sys.exit("ERROR: ffmpeg not found. Install with: brew install ffmpeg")

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

    print("Fetching & summarising stories…")
    stories = fetch_and_summarise()

    print("\nBuilding video…")
    segs = []

    # ── Overview slide (all 5 stories) ──
    print("  [overview]")
    s_ov = slide_overview(stories, date_str)
    a_ov = BASE / "_a_overview.mp3"
    titles = ". ".join(f"Story {i+1}: {s['title']}" for i,s in enumerate(stories))
    speak_single(
        f"Welcome to Coralogix CyberTube. Today is {date_str}. "
        f"Here is what we are covering. {titles}.",
        a_ov
    )
    v_ov = BASE / "_v_overview.mp4"
    slide_to_mp4(s_ov, a_ov, v_ov)
    segs.append(v_ov)

    # ── Story segments ──
    for i, story in enumerate(stories, 1):
        print(f"  [story {i}] {story['title'][:50]}…")
        s = slide_story(i, story, date_str)
        a = BASE / f"_a_{i}.mp3"
        v = BASE / f"_v_{i}.mp4"
        speak_dialogue(story["script"], a)
        slide_to_mp4(s, a, v)
        segs.append(v)

    # ── Outro ──
    print("  [outro]")
    s_out = slide_outro(date_str)
    a_out = BASE / "_a_outro.mp3"
    speak_single(
        "That is all for today on Coralogix CyberTube. "
        "Stay secure, stay informed, and we will see you tomorrow.",
        a_out
    )
    v_out = BASE / "_v_outro.mp4"
    slide_to_mp4(s_out, a_out, v_out)
    segs.append(v_out)

    print(f"\nConcatenating → {final.name}…")
    concat_mp4s(segs, final)

    print("Extracting thumbnail…")
    extract_thumb(final, thumb)

    print("Cleaning up…")
    for pat in ["_a_*.mp3", "_v_*.mp4", "_s_*.png", "_dl_*.mp3"]:
        for f in BASE.glob(pat):
            f.unlink(missing_ok=True)

    print("Updating website…")
    eps = load_episodes()
    eps.insert(0, {
        "date":      date_str,
        "slug":      slug,
        "video":     f"videos/{slug}.mp4",
        "thumbnail": f"thumbnails/{slug}.jpg",
        "title":     f"Top 5 Cyber Stories — {date_str}",
        "description": "  ·  ".join(s["title"][:42] for s in stories[:3]) + "…",
    })
    save_episodes(eps)
    regenerate_site(eps)

    size_mb = final.stat().st_size / 1_048_576
    print(f"\n✅  Done!  {final.name}  ({size_mb:.1f} MB)")
    print(f"   Open index.html in your browser.")


if __name__ == "__main__":
    main()
