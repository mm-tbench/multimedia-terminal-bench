#!/usr/bin/env python3
"""Build T010 proof-step-note lecture screencast.

Joint-AV redesign of the original spec (which had a frame-OCR + STT
shortcut concern flagged in the xlsx Decision Note).

Mechanic: a single static slide displays four lemmas in a 2×2 grid
(Lemma A / B / C / D). The lecturer walks through the proof in a
non-standard order (audio narration assigns step numbers 1..4 to
the four lemmas in scrambled correspondence) and the cursor
highlights the lemma quadrant being discussed in each step.
The slides themselves carry only neutral lemma labels — the
step→lemma correspondence is established **only** by the joint of
audio (step number) + cursor position (which quadrant). Both
modalities are required: pure-OCR returns four candidate formulas
with no step-assignment; pure-STT names step numbers but cannot
extract the formula glyphs.

Step → lemma assignment:
  step 1 → Lemma C (bottom-left)  — Area = (a+b)²
  step 2 → Lemma D (bottom-right) — Area = c² + 2ab
  step 3 → Lemma A (top-left)     — (a+b)² = a² + 2ab + b²    [GOLD]
  step 4 → Lemma B (top-right)    — c² = (a+b)² − 2ab

Output: ./environment/assets/lecture.mp4 — ~300 s, 1280×720, 24 fps.

Run:
    uv run --with kokoro --with soundfile --with pillow --with numpy \\
           --with playwright python -u build_assets.py
First time only:
    uv run --with playwright python -m playwright install chromium
"""

from __future__ import annotations

import hashlib
import json
import math
import shutil
import subprocess
import time
import urllib.request
import wave
from pathlib import Path

import numpy as np
import soundfile as sf
from PIL import Image, ImageDraw

ROOT = Path(__file__).parent
ASSETS = ROOT / "environment" / "assets"
WORK = ROOT / ".build"
ASSETS.mkdir(parents=True, exist_ok=True)
WORK.mkdir(parents=True, exist_ok=True)

W, H = 1280, 720
FPS = 24
SAMPLE_RATE = 24_000
TOTAL_SEC = 300.0

# ---------------------------------------------------------------------------
# Slide — single 2×2 grid with four lemma quadrants.
# Each quadrant gets a queryable .lemma-X class so Playwright can grab the
# bbox and use it as a cursor anchor.
# ---------------------------------------------------------------------------

LEMMAS = {
    "A": {
        "label": "Lemma A",
        "tex": r"(a+b)^{2} = a^{2} + 2ab + b^{2}",
    },
    "B": {
        "label": "Lemma B",
        "tex": r"c^{2} = (a+b)^{2} - 2ab",
    },
    "C": {
        "label": "Lemma C",
        "tex": r"S = (a+b)^{2}",
    },
    "D": {
        "label": "Lemma D",
        "tex": r"S = c^{2} + 2ab",
    },
}

# Step → lemma assignment. Step 3 is the gold step; its lemma is what the
# verifier scores against.
STEP_TO_LEMMA = {1: "C", 2: "D", 3: "A", 4: "B"}
GOLD_STEP = 3
GOLD_LEMMA = STEP_TO_LEMMA[GOLD_STEP]

HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<style>
  html, body { margin: 0; padding: 0; }
  body {
    width: 1280px; height: 720px; overflow: hidden;
    background: linear-gradient(180deg, #fafbfc 0%, #eef1f6 100%);
    font-family: 'Helvetica Neue', 'Arial', sans-serif;
    color: #1a2238; position: relative;
    display: flex; flex-direction: column;
  }
  .header {
    flex: 0 0 56px;
    background: linear-gradient(135deg, #1a2238 0%, #2c3e64 100%);
    color: #e6ecf2;
    display: flex; align-items: center; justify-content: space-between;
    padding: 0 32px;
    font-size: 17px; font-weight: 600; letter-spacing: 0.2px;
    box-shadow: 0 2px 6px rgba(0,0,0,0.12);
  }
  .title    { margin: 18px 0 0 0; text-align: center; font-size: 28px; font-weight: 700; }
  .subtitle { margin: 4px 0 14px 0; text-align: center; font-size: 16px; color: #5a6478; }
  .grid     {
    flex: 1; display: grid;
    grid-template-columns: 1fr 1fr; grid-template-rows: 1fr 1fr;
    gap: 24px; padding: 0 36px 18px 36px;
  }
  .quadrant {
    border: 2px solid #c8d0dc; border-radius: 12px;
    background: #ffffff;
    padding: 20px 24px;
    display: flex; flex-direction: column; align-items: center; justify-content: center;
    box-shadow: 0 2px 8px rgba(20, 30, 60, 0.05);
  }
  .quadrant h2 {
    margin: 0 0 14px 0; font-size: 22px; font-weight: 700; color: #2c3e64;
    letter-spacing: 0.2px;
  }
  .quadrant .formula {
    width: 100%; text-align: center;
  }
  .quadrant .formula mjx-container { font-size: 220% !important; }
  .footer   {
    padding: 8px 30px; font-size: 11px; color: #8a92a8;
    border-top: 1px solid #d8dde6; background: #fafbfc;
    text-align: center;
  }
</style>
<script>
  window.MathJax = {
    tex: {
      inlineMath: [['$','$']], displayMath: [['$$','$$']],
      packages: {'[+]': ['html']}
    },
    loader: { load: ['[tex]/html'] },
    startup: { typeset: false }
  };
</script>
<!-- MathJax injected by Playwright after navigation. -->
</head>
<body>
  <div class="header">
    <span>Math 414 &middot; Lecture 12</span>
    <span>Pythagorean Proof</span>
  </div>
  <div class="title">Pythagorean Theorem — geometric proof</div>
  <div class="subtitle">Four supporting lemmas (S denotes area)</div>
  <div class="grid">
    <div class="quadrant lemma-A"><h2>Lemma A</h2><div class="formula">$$__TEX_A__$$</div></div>
    <div class="quadrant lemma-B"><h2>Lemma B</h2><div class="formula">$$__TEX_B__$$</div></div>
    <div class="quadrant lemma-C"><h2>Lemma C</h2><div class="formula">$$__TEX_C__$$</div></div>
    <div class="quadrant lemma-D"><h2>Lemma D</h2><div class="formula">$$__TEX_D__$$</div></div>
  </div>
  <div class="footer">&copy; Math 414 &middot; Course Notes &middot; CC-BY-SA-4.0</div>
<script>
  window.__triggerTypeset = function () {
    if (!window.MathJax || !window.MathJax.startup || !window.MathJax.startup.promise) {
      window.__error = "MathJax startup missing"; window.__rendered = true; return;
    }
    window.MathJax.startup.promise
      .then(function () { return window.MathJax.typesetPromise(); })
      .then(function () { window.__rendered = true; })
      .catch(function (err) { window.__error = String(err); window.__rendered = true; });
  };
</script>
</body>
</html>
"""


MATHJAX_URL = "https://cdn.jsdelivr.net/npm/mathjax@3/es5/tex-chtml-full.js"


def _ensure_mathjax() -> Path:
    local = WORK / "mathjax.js"
    if local.exists() and local.stat().st_size > 100_000:
        return local
    print(f"  fetching MathJax → {local}", flush=True)
    urllib.request.urlretrieve(MATHJAX_URL, str(local))
    return local


def render_slide_via_playwright():
    """Returns (PIL.Image RGB 1280x720, {lemma_id: (cx, cy)}) for the four
    quadrant centers."""
    from playwright.sync_api import sync_playwright

    mathjax_path = _ensure_mathjax()

    bbox_js = (
        "(cls) => {"
        "  const el = document.querySelector(`.${cls}`);"
        "  if (!el) return null;"
        "  const r = el.getBoundingClientRect();"
        "  return { x: r.x, y: r.y, w: r.width, h: r.height };"
        "}"
    )

    html = HTML_TEMPLATE
    for lid, info in LEMMAS.items():
        html = html.replace(f"__TEX_{lid}__", info["tex"])
    html_path = WORK / "slide.html"
    html_path.write_text(html, encoding="utf-8")

    with sync_playwright() as p:
        browser = p.chromium.launch(
            args=[
                "--allow-file-access-from-files",
                "--disable-web-security",
            ]
        )
        ctx = browser.new_context(
            viewport={"width": W, "height": H},
            device_scale_factor=1,
        )
        page = ctx.new_page()
        page.goto(f"file://{html_path.resolve()}")
        page.add_script_tag(path=str(mathjax_path))
        page.evaluate("window.__triggerTypeset()")
        page.wait_for_function("window.__rendered === true", timeout=60000)
        err = page.evaluate("window.__error || null")
        if err:
            raise RuntimeError(f"MathJax error: {err}")

        quadrant_centers = {}
        for lid in LEMMAS:
            bb = page.evaluate(bbox_js, f"lemma-{lid}")
            if bb is None:
                raise RuntimeError(f"missing quadrant bbox for lemma {lid}")
            quadrant_centers[lid] = {
                "cx": bb["x"] + bb["w"] / 2,
                "cy": bb["y"] + bb["h"] / 2,
                "hw": bb["w"] / 2,
                "hh": bb["h"] / 2,
            }

        png_path = WORK / "slide.png"
        page.screenshot(path=str(png_path), full_page=False, omit_background=False)
        ctx.close()
        browser.close()

    img = Image.open(png_path).convert("RGB")
    if img.size != (W, H):
        img = img.resize((W, H))
    print(
        "  rendered slide; quadrant centers: "
        + ", ".join(
            f"{k}=({v['cx']:.0f},{v['cy']:.0f})" for k, v in quadrant_centers.items()
        ),
        flush=True,
    )
    return img, quadrant_centers


# ---------------------------------------------------------------------------
# Cursor schedule.
# Anchors: (t, anchor_target, behavior) where anchor_target is either a
# lemma id (uses quadrant center) or a literal (x, y) tuple.
# behavior is 'transit' (low orbit) or 'dwell' (larger orbit, circles target).
# ---------------------------------------------------------------------------

CURSOR_ANCHORS = [
    # 0–25s: cursor near title (intro)
    (0.0, (640, 90), "transit"),
    (10.0, (640, 90), "dwell"),
    (20.0, (640, 90), "dwell"),
    # 30–80s: step 1 — Lemma C (bottom-left)
    (28.0, "C", "transit"),
    (32.0, "C", "dwell"),  # step 1 announce + dwell
    (78.0, "C", "dwell"),
    # 90–140s: step 2 — Lemma D (bottom-right)
    (86.0, "D", "transit"),
    (92.0, "D", "dwell"),  # step 2
    (138.0, "D", "dwell"),
    # 150–200s: step 3 — Lemma A (top-left) — GOLD
    (146.0, "A", "transit"),
    (152.0, "A", "dwell"),  # step 3
    (200.0, "A", "dwell"),
    # 210–286s: step 4 — Lemma B (top-right) with callback to Lemma A
    (206.0, "B", "transit"),
    (212.0, "B", "dwell"),  # step 4 begins
    (236.0, "B", "dwell"),
    (238.5, "A", "transit"),  # callback to Lemma A — narration at 240s
    (241.5, "A", "dwell"),  # callback dwell on Lemma A
    (247.0, "A", "dwell"),
    (249.5, "B", "transit"),  # return to Lemma B
    (252.0, "B", "dwell"),  # step 4 continues
    (286.0, "B", "dwell"),  # step 4 ends
    # 290–300s: outro — back to title
    (290.0, (640, 90), "transit"),
    (300.0, (640, 90), "transit"),
]


def _resolve(anchor, quadrant_centers):
    _t, target, behavior = anchor
    if isinstance(target, tuple):
        return float(target[0]), float(target[1]), behavior, None, None
    if target not in quadrant_centers:
        raise KeyError(f"unknown lemma {target!r}; have {list(quadrant_centers)}")
    info = quadrant_centers[target]
    return float(info["cx"]), float(info["cy"]), behavior, info["hw"], info["hh"]


def _orbit_offset(behavior, t, anchor_idx, glyph_half_w=None, glyph_half_h=None):
    """Continuous slow elliptical orbit. Larger during dwells (= the
    cursor visibly circles the quadrant)."""
    if behavior == "dwell":
        rx, ry = 80.0, 50.0  # quadrant is large; orbit covers a wide arc
        period = 6.5 + (anchor_idx % 3) * 0.4
        phase = (anchor_idx * 0.71) % (2 * math.pi)
    else:
        rx, ry = 8.0, 6.0
        period = 4.0 + (anchor_idx % 2) * 0.3
        phase = (anchor_idx * 1.21) % (2 * math.pi)
    if glyph_half_w is not None:
        rx = min(rx, max(8.0, glyph_half_w - 12.0))
    if glyph_half_h is not None:
        ry = min(ry, max(6.0, glyph_half_h - 12.0))
    theta = 2 * math.pi * t / period + phase
    return rx * math.cos(theta), ry * math.sin(theta)


def cursor_at(t, anchors, quadrant_centers):
    n = len(anchors)
    if t <= anchors[0][0]:
        x, y, beh, hw, hh = _resolve(anchors[0], quadrant_centers)
        ox, oy = _orbit_offset(beh, t, 0, hw, hh)
        return x + ox, y + oy
    if t >= anchors[-1][0]:
        x, y, beh, hw, hh = _resolve(anchors[-1], quadrant_centers)
        ox, oy = _orbit_offset(beh, t, n - 1, hw, hh)
        return x + ox, y + oy

    for i in range(n - 1):
        t0 = anchors[i][0]
        t1 = anchors[i + 1][0]
        if t0 <= t < t1:
            x0, y0, b0, hw0, hh0 = _resolve(anchors[i], quadrant_centers)
            x1, y1, b1, hw1, hh1 = _resolve(anchors[i + 1], quadrant_centers)
            u = (t - t0) / max(1e-6, t1 - t0)
            ue = u * u * (3 - 2 * u)

            base_x = x0 + ue * (x1 - x0)
            base_y = y0 + ue * (y1 - y0)

            dx, dy = x1 - x0, y1 - y0
            dist = math.hypot(dx, dy)
            if dist > 60:
                nx = -dy / dist
                ny = dx / dist
                arc_amp = min(40.0, dist * 0.06)
                sign = 1.0 if i % 2 == 0 else -1.0
                arc = arc_amp * math.sin(math.pi * u) * sign
                base_x += nx * arc
                base_y += ny * arc

            if u < 0.5:
                beh_now, hw_now, hh_now = b0, hw0, hh0
            else:
                beh_now, hw_now, hh_now = b1, hw1, hh1
            ox, oy = _orbit_offset(beh_now, t, i, hw_now, hh_now)
            return base_x + ox, base_y + oy

    x, y, beh, hw, hh = _resolve(anchors[-1], quadrant_centers)
    ox, oy = _orbit_offset(beh, t, n - 1, hw, hh)
    return x + ox, y + oy


def make_cursor():
    img = Image.new("RGBA", (32, 40), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    pts = [(2, 2), (2, 30), (10, 22), (15, 36), (19, 34), (12, 20), (26, 20)]
    d.polygon(pts, fill=(255, 255, 255, 255), outline=(0, 0, 0, 255))
    return img


# ---------------------------------------------------------------------------
# Narration. The lecturer walks through 4 steps in scrambled order (1, 2, 3, 4
# correspond to Lemmas C, D, A, B respectively). The step-3 narration is the
# only segment that names "step 3" and that the verifier scores against.
# Slide labels (Lemma A/B/C/D) are NEVER spoken — step→lemma binding is
# established only by joint of audio (step number) + cursor (which quadrant).
# ---------------------------------------------------------------------------

NARRATION = [
    (
        1.0,
        "Today we prove the Pythagorean theorem geometrically, using four short lemmas. Let us go through them in order.",
    ),
    # Step 1 → Lemma C (bottom-left)
    (32.0, "First, begin with a large square whose side length is a plus b."),
    (
        42.0,
        "By the basic area formula, the area S of this square equals the side length, squared — that is, the quantity a plus b, all squared.",
    ),
    (
        59.0,
        "Hold on to this expression. We will refer back to it shortly.",
    ),
    # Step 2 → Lemma D (bottom-right)
    (
        92.0,
        "Now, decompose the same large square geometrically. Place a tilted square of side c in the middle, and four right triangles around it, each with legs a and b.",
    ),
    (
        108.0,
        "Adding it up, the total area equals c squared, for the central tilted square, plus four times one half a b, which simplifies to two a b, for the four triangles.",
    ),
    (125.0, "So the area can also be written as c squared plus two a b."),
    # Step 3 → Lemma A (top-left) — GOLD
    (152.0, "Next, we expand the binomial that we wrote down at the very start."),
    (
        163.0,
        "Using the standard binomial expansion, the square of the sum a plus b equals a squared, plus two times a times b, plus b squared.",
    ),
    (
        178.0,
        "Notice the cross-term, two a b — that middle product is the bridge from the geometry to the algebra in our final lemma.",
    ),
    # Step 4 → Lemma B (top-right) with callback to Lemma A around 240s
    (
        212.0,
        "Finally, set the two area expressions equal. From the first lemma, the area is a plus b squared.",
    ),
    (
        225.0,
        "From the second lemma, the same area is also c squared plus two a b.",
    ),
    (
        240.0,
        "Substituting in the binomial expansion we just did, we replace a plus b squared with a squared plus two a b plus b squared.",
    ),
    (
        260.0,
        "Subtracting two a b from both sides, the cross-terms cancel, and we are left with a squared plus b squared equals c squared. We can also rewrite this as c squared equals a plus b squared minus two a b.",
    ),
    (282.0, "And there is the Pythagorean theorem."),
]


# ---------------------------------------------------------------------------
# Gold (build introspection only; verifier carries its own copy).
# ---------------------------------------------------------------------------

GOLD = {
    "step": 3,
    "lemma": "A",
    "formula_tex": r"(a+b)^{2} = a^{2} + 2ab + b^{2}",
    "formula_required_tokens": [
        # Each entry is a list of acceptable variants; ALL outer entries
        # must appear in the note for the formula to be considered present.
        ["(a+b)", "(a + b)"],
        ["a^2", "a²", "a**2", "a^{2}"],
        ["2ab", "2 ab", "2*a*b", "2 a b", "2 \\cdot a \\cdot b"],
        ["b^2", "b²", "b**2", "b^{2}"],
    ],
    "explanation_keywords": ["expand", "binomial", "cross"],
    "explanation_min_keywords": 2,
    "forbidden_substrings": [
        # Lemma B distinctive RHS — c² = (a+b)² − 2ab. Step-3 note should
        # not present (a+b)² minus 2ab as its formula.
        "(a+b)^2 - 2ab",
        "(a+b)^{2} - 2ab",
        "(a+b)² - 2ab",
        "(a+b)² − 2ab",
        "(a + b)^2 - 2ab",
        # Lemma C / D distinctive LHS — area variable S as the equation's
        # left-hand side indicates wrong-step content.
        "S = (a+b)",
        "S = c",
    ],
}


# ---------------------------------------------------------------------------
# Audio synthesis.
# ---------------------------------------------------------------------------


def synth_narration() -> Path:
    print("Loading Kokoro pipeline (am_michael)…", flush=True)
    from kokoro import KPipeline

    pipeline = KPipeline(lang_code="a")
    print("  ready.", flush=True)

    items = sorted(NARRATION, key=lambda x: x[0])
    pieces: list[np.ndarray] = []
    cursor = 0.0
    print(f"Synthesizing {len(items)} narration turns…", flush=True)
    t0 = time.time()
    for i, (target_t, text) in enumerate(items):
        gap = max(0.0, target_t - cursor)
        if gap > 0:
            pieces.append(np.zeros(int(gap * SAMPLE_RATE), dtype=np.float32))
            cursor += gap
        ts = time.time()
        chunks = []
        for _gs, _ps, audio in pipeline(text, voice="am_michael"):
            chunks.append(audio)
        wav = (
            np.concatenate(chunks).astype(np.float32)
            if chunks
            else np.zeros(0, dtype=np.float32)
        )
        if wav.shape[0] == 0:
            continue
        pieces.append(wav)
        cursor += wav.shape[0] / SAMPLE_RATE
        synth_t = time.time() - ts
        print(
            f"  [{i:02d}] target_t={target_t:6.1f}s synth={synth_t:5.1f}s "
            f"cursor={cursor:6.1f}s wall={time.time() - t0:6.1f}s",
            flush=True,
        )
    pad_to = max(cursor + 1.0, TOTAL_SEC)
    pieces.append(np.zeros(int((pad_to - cursor) * SAMPLE_RATE), dtype=np.float32))
    full = np.concatenate(pieces)
    wav_path = WORK / "narration.wav"
    sf.write(str(wav_path), full, SAMPLE_RATE)
    print(
        f"WAV: {full.shape[0] / SAMPLE_RATE:.2f}s "
        f"({wav_path.stat().st_size / 1e6:.1f} MB)",
        flush=True,
    )
    return wav_path


# ---------------------------------------------------------------------------
# Video rendering.
# ---------------------------------------------------------------------------


def render_video(slide_img, quadrant_centers, audio_wav: Path) -> Path:
    cursor_img = make_cursor()

    with wave.open(str(audio_wav), "rb") as w:
        total_sec = w.getnframes() / w.getframerate()
    total_sec = max(total_sec, TOTAL_SEC)
    n_frames = int(math.ceil(total_sec * FPS))
    print(f"Video: {n_frames} frames @ {FPS}fps for {total_sec:.2f}s", flush=True)

    frames_dir = WORK / "frames"
    if frames_dir.exists():
        shutil.rmtree(frames_dir)
    frames_dir.mkdir()

    for i in range(n_frames):
        t = i / FPS
        x, y = cursor_at(t, CURSOR_ANCHORS, quadrant_centers)
        frame = slide_img.copy()
        frame.paste(cursor_img, (int(x) - 2, int(y) - 2), cursor_img)
        frame.save(frames_dir / f"f_{i:06d}.png", optimize=False, compress_level=1)
        if i % 240 == 0:
            print(f"  rendered frame {i}/{n_frames} (t={t:.1f}s)", flush=True)

    out = ASSETS / "lecture.mp4"
    cmd = [
        "ffmpeg",
        "-y",
        "-loglevel",
        "error",
        "-framerate",
        str(FPS),
        "-i",
        str(frames_dir / "f_%06d.png"),
        "-i",
        str(audio_wav),
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "22",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-b:a",
        "96k",
        "-ar",
        str(SAMPLE_RATE),
        "-ac",
        "1",
        "-shortest",
        str(out),
    ]
    subprocess.run(cmd, check=True)
    return out


def main():
    print("Step 1/3: render slide via Playwright + MathJax", flush=True)
    slide_img, quadrant_centers = render_slide_via_playwright()

    print("Step 2/3: synthesize narration", flush=True)
    audio_wav = synth_narration()

    print("Step 3/3: composite frames + encode video", flush=True)
    out = render_video(slide_img, quadrant_centers, audio_wav)
    print(f"Wrote {out} ({out.stat().st_size / 1e6:.1f} MB)", flush=True)

    sha = hashlib.sha256(out.read_bytes()).hexdigest()
    print(f"sha256 = {sha}", flush=True)

    (WORK / "gold.json").write_text(json.dumps(GOLD, indent=2))
    print(f"Gold (step {GOLD['step']} → Lemma {GOLD['lemma']}) written")


if __name__ == "__main__":
    main()
