#!/usr/bin/env python3
"""Build T012 v2 lecture screencast (lecturer-visual-term-ref).

v2 rewrite (2026-04-27):
  - Slides rendered via HTML + MathJax 3 + Playwright Chromium. Per-term
    bboxes extracted from the live DOM via `\\class{term-X}{...}` MathJax
    annotations. Visual fidelity matches a real lecture deck.
  - 6 deictic events (2 per slide) instead of 3.
  - Deictic narration is *demonstrative only* ("look at this symbol") —
    role descriptions ("amplitude", "diffusion coefficient", ...) live in
    non-deictic narration so they don't leak the per-event answer.
  - Distractor structure: cursor briefly hovers each role-equivalent
    candidate before settling on the real target. Event 6 carries an
    audio-visual disagreement trap (audio recently named δ, cursor sits
    on ∇²).
  - Cursor motion: smooth cubic-smoothstep transit between anchors with
    gentle perpendicular arc on long moves and a continuous slow
    elliptical orbit (period ~5.5s, larger radius during dwells = the
    cursor visibly *circles* the referenced term). No per-frame
    Gaussian jitter.

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
TOTAL_SEC = 240.0

# ---------------------------------------------------------------------------
# Slides — TeX with `\class{term-X}{...}` annotations on per-term targets.
# Each `term-*` becomes a queryable bbox in the rendered MathJax DOM, used
# as the cursor anchor.
# ---------------------------------------------------------------------------

SLIDES = {
    "eq_1": {
        "num": 1,
        "title": "1 — Linear recurrence",
        "subtitle": "two-term recurrence relation",
        "tex": (
            r"\class{term-lhs}{x_{n+1}} \;=\; "
            r"\class{term-a}{a}\,\class{term-cdot1}{\cdot}\,\class{term-xn}{x_{n}} "
            r"\;+\; \class{term-b}{b}\,\class{term-cdot2}{\cdot}\,\class{term-yn}{y_{n}}"
        ),
        "body": [
            "Two-term linear recurrence over the index n.",
            "Coefficients a and b are constants for this lecture.",
            "Driving terms are the previous-step quantities.",
        ],
        "term_classes": [
            "term-lhs",
            "term-a",
            "term-cdot1",
            "term-xn",
            "term-b",
            "term-cdot2",
            "term-yn",
        ],
    },
    "eq_2": {
        "num": 2,
        "title": "2 — Sinusoidal model",
        "subtitle": "amplitude / frequency decomposition",
        "tex": (
            r"\class{term-ft}{f(t)} \;=\; "
            r"\class{term-alpha}{\alpha}\,\sin(\class{term-omega1}{\omega}\, t) "
            r"\;+\; \class{term-beta}{\beta}\,\cos(\class{term-omega2}{\omega}\, t)"
        ),
        "body": [
            "Decomposition into sine and cosine branches.",
            "Two amplitudes set each branch's contribution.",
            "Angular frequency ties the expression to time.",
        ],
        "term_classes": [
            "term-ft",
            "term-alpha",
            "term-omega1",
            "term-beta",
            "term-omega2",
        ],
    },
    "eq_3": {
        "num": 3,
        "title": "3 — Heat equation",
        "subtitle": "diffusion in one dimension",
        "tex": (
            r"\class{term-dudt}{\dfrac{\partial u}{\partial t}} \;=\; "
            r"\class{term-delta}{\delta}\,\class{term-laplacian}{\nabla^{2}}\,\class{term-u}{u}"
        ),
        "body": [
            "1-D heat equation in u(x, t).",
            "First-order in time, second-order in space.",
            "Drives an initial condition toward equilibrium along a rod.",
        ],
        "term_classes": [
            "term-dudt",
            "term-delta",
            "term-laplacian",
            "term-u",
        ],
    },
}

SEGMENT_RANGES = [
    ("eq_1", 0.0, 80.0),
    ("eq_2", 80.0, 160.0),
    ("eq_3", 160.0, 240.0),
]


# ---------------------------------------------------------------------------
# HTML template — lecture-slide layout. MathJax CHTML output, classes
# preserved on rendered glyph groups.
# ---------------------------------------------------------------------------

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
  .title    { margin: 30px 0 0 60px; font-size: 40px; font-weight: 700; letter-spacing: -0.2px; }
  .subtitle { margin: 4px 0 0 60px; font-size: 21px; font-weight: 400; color: #5a6478; }
  .divider  { margin: 14px 0 0 60px; width: 168px; height: 4px; background: #3a5fcd; border-radius: 2px; }
  .equation { margin: 38px 0 0 0; text-align: center; font-size: 1em; }
  .equation mjx-container { font-size: 285% !important; }
  .body     { margin: 38px 0 0 60px; padding: 0 40px 0 0; font-size: 21px; color: #3a4256; line-height: 1.65; }
  .body li  { margin-bottom: 7px; }
  .footer   {
    margin-top: auto;
    padding: 12px 30px; font-size: 12px; color: #8a92a8;
    border-top: 1px solid #d8dde6; background: #fafbfc;
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
    <span>Math 414 &middot; Lecture 7</span>
    <span>Slide __NUM__ / 3</span>
  </div>
  <div class="title">__TITLE__</div>
  <div class="subtitle">__SUBTITLE__</div>
  <div class="divider"></div>
  <div class="equation">$$__TEX__$$</div>
  <ul class="body">__BODY__</ul>
  <div class="footer">&copy; Math 414 &middot; Course Notes &middot; CC-BY-SA-4.0</div>
<script>
  // Typeset is triggered manually via Playwright after MathJax script
  // is injected — see build_assets.py.
  window.__triggerTypeset = function () {
    if (!window.MathJax || !window.MathJax.startup || !window.MathJax.startup.promise) {
      window.__error = "MathJax startup missing";
      window.__rendered = true;
      return;
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
    """Download MathJax once into WORK/. Network-free on subsequent builds."""
    local = WORK / "mathjax.js"
    if local.exists() and local.stat().st_size > 100_000:
        return local
    import urllib.request

    print(f"  fetching MathJax → {local}", flush=True)
    urllib.request.urlretrieve(MATHJAX_URL, str(local))
    return local


def render_slides_via_playwright():
    """Render each slide via Playwright + MathJax. Returns
    {slide_id: (PIL.Image RGB 1280x720, {term_class: (cx, cy)})}."""
    from playwright.sync_api import sync_playwright

    mathjax_path = _ensure_mathjax()

    out = {}
    bbox_js = (
        "(cls) => {"
        "  const el = document.querySelector(`mjx-container .${cls}`);"
        "  if (!el) return null;"
        "  const r = el.getBoundingClientRect();"
        "  return { x: r.x, y: r.y, w: r.width, h: r.height };"
        "}"
    )
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
        for slide_id, slide in SLIDES.items():
            html = (
                HTML_TEMPLATE.replace("__NUM__", str(slide["num"]))
                .replace("__TITLE__", slide["title"])
                .replace("__SUBTITLE__", slide["subtitle"])
                .replace("__TEX__", slide["tex"])
                .replace("__BODY__", "".join(f"<li>{b}</li>" for b in slide["body"]))
            )
            html_path = WORK / f"slide_{slide_id}.html"
            html_path.write_text(html, encoding="utf-8")

            page = ctx.new_page()
            page.goto(f"file://{html_path.resolve()}")
            page.add_script_tag(path=str(mathjax_path))
            page.evaluate("window.__triggerTypeset()")
            page.wait_for_function("window.__rendered === true", timeout=60000)
            err = page.evaluate("window.__error || null")
            if err:
                raise RuntimeError(f"MathJax error on {slide_id}: {err}")

            term_centers = {}
            for cls in slide["term_classes"]:
                bb = page.evaluate(bbox_js, cls)
                if bb is None:
                    raise RuntimeError(f"missing bbox for .{cls} on {slide_id}")
                term_centers[cls] = {
                    "cx": bb["x"] + bb["w"] / 2,
                    "cy": bb["y"] + bb["h"] / 2,
                    "hw": bb["w"] / 2,
                    "hh": bb["h"] / 2,
                }

            png_path = WORK / f"slide_{slide_id}.png"
            page.screenshot(path=str(png_path), full_page=False, omit_background=False)
            page.close()

            img = Image.open(png_path).convert("RGB")
            if img.size != (W, H):
                img = img.resize((W, H))
            out[slide_id] = (img, term_centers)
            print(f"  rendered {slide_id}: terms={list(term_centers)}", flush=True)
        ctx.close()
        browser.close()
    return out


# ---------------------------------------------------------------------------
# Cursor schedule.
# Each anchor: (t_sec, slide_id, target_class_or_xy, behavior).
# behavior in {"dwell", "transit"} controls orbit radius/period blending.
# ---------------------------------------------------------------------------

CURSOR_ANCHORS = [
    # eq_1 (0–80s)
    (0.0, "eq_1", (180, 200), "transit"),
    (4.0, "eq_1", (240, 230), "transit"),
    (10.0, "eq_1", "term-lhs", "dwell"),
    (16.0, "eq_1", "term-lhs", "dwell"),
    (19.0, "eq_1", (700, 320), "transit"),
    (22.0, "eq_1", "term-yn", "dwell"),  # distractor: y_n hover
    (25.0, "eq_1", "term-yn", "dwell"),
    (27.0, "eq_1", "term-xn", "transit"),  # approach event 1
    (30.0, "eq_1", "term-xn", "dwell"),  # EVENT 1 dwell start
    (38.0, "eq_1", "term-xn", "dwell"),  # EVENT 1 dwell end
    (43.0, "eq_1", (560, 380), "transit"),  # wander
    (50.0, "eq_1", "term-a", "dwell"),  # distractor: a hover
    (54.0, "eq_1", "term-a", "dwell"),
    (58.0, "eq_1", (700, 280), "transit"),
    (62.0, "eq_1", "term-b", "transit"),  # approach event 2
    (65.0, "eq_1", "term-b", "dwell"),  # EVENT 2 dwell start
    (73.0, "eq_1", "term-b", "dwell"),  # EVENT 2 dwell end
    (78.0, "eq_1", (550, 200), "transit"),
    # eq_2 (80–160s)
    (82.0, "eq_2", (180, 200), "transit"),
    (86.0, "eq_2", (300, 250), "transit"),
    (92.0, "eq_2", "term-ft", "dwell"),
    (97.0, "eq_2", "term-ft", "dwell"),
    (100.0, "eq_2", "term-alpha", "transit"),
    (102.0, "eq_2", "term-alpha", "dwell"),  # brief hover (pre-event)
    (105.0, "eq_2", "term-beta", "transit"),  # distractor: β first
    (108.0, "eq_2", "term-beta", "dwell"),
    (110.0, "eq_2", "term-alpha", "transit"),  # back to α
    (112.0, "eq_2", "term-alpha", "dwell"),  # EVENT 3 dwell start
    (120.0, "eq_2", "term-alpha", "dwell"),  # EVENT 3 dwell end
    (124.0, "eq_2", (650, 380), "transit"),  # wander
    (128.0, "eq_2", "term-omega1", "transit"),  # ω instances
    (132.0, "eq_2", "term-omega2", "transit"),
    (136.0, "eq_2", "term-beta", "transit"),
    (140.0, "eq_2", "term-beta", "dwell"),  # EVENT 4 dwell start
    (148.0, "eq_2", "term-beta", "dwell"),  # EVENT 4 dwell end
    (153.0, "eq_2", (550, 200), "transit"),
    # eq_3 (160–240s)
    (162.0, "eq_3", (180, 200), "transit"),
    (168.0, "eq_3", (260, 280), "transit"),
    (174.0, "eq_3", "term-dudt", "dwell"),
    (178.0, "eq_3", "term-dudt", "dwell"),
    (181.0, "eq_3", "term-u", "transit"),
    (183.0, "eq_3", "term-laplacian", "transit"),
    (186.0, "eq_3", "term-delta", "dwell"),  # EVENT 5 dwell start
    (194.0, "eq_3", "term-delta", "dwell"),  # EVENT 5 dwell end
    (198.0, "eq_3", "term-delta", "transit"),
    (204.0, "eq_3", (700, 250), "transit"),  # wander
    (210.0, "eq_3", "term-u", "transit"),
    (215.0, "eq_3", "term-laplacian", "transit"),
    (220.0, "eq_3", "term-laplacian", "dwell"),  # EVENT 6 dwell start (TRAP)
    (228.0, "eq_3", "term-laplacian", "dwell"),  # EVENT 6 dwell end
    (232.0, "eq_3", (550, 200), "transit"),
    (240.0, "eq_3", (180, 200), "transit"),
]


def _resolve(anchor, slide_centers):
    _t, slide_id, target, behavior = anchor
    if isinstance(target, tuple):
        return float(target[0]), float(target[1]), behavior, None, None
    centers = slide_centers[slide_id]
    if target not in centers:
        raise KeyError(
            f"unknown term {target!r} on slide {slide_id}; have {list(centers)}"
        )
    info = centers[target]
    return float(info["cx"]), float(info["cy"]), behavior, info["hw"], info["hh"]


def _orbit_offset(behavior, t, anchor_idx, glyph_half_w=None, glyph_half_h=None):
    """Continuous slow elliptical orbit. Larger during dwells (= the
    cursor visibly circles the referenced term), small while transiting.

    Orbit radius is bounded by glyph half-extent (minus a 2 px inner
    margin) so the cursor stays inside the term it's referring to even
    for narrow glyphs (e.g. `b`, `δ`)."""
    if behavior == "dwell":
        rx, ry = 22.0, 14.0
        period = 5.5 + (anchor_idx % 3) * 0.4
        phase = (anchor_idx * 0.71) % (2 * math.pi)
    else:
        rx, ry = 6.0, 4.0
        period = 4.0 + (anchor_idx % 2) * 0.3
        phase = (anchor_idx * 1.21) % (2 * math.pi)
    if glyph_half_w is not None:
        rx = min(rx, max(4.0, glyph_half_w - 2.0))
    if glyph_half_h is not None:
        ry = min(ry, max(4.0, glyph_half_h - 2.0))
    theta = 2 * math.pi * t / period + phase
    return rx * math.cos(theta), ry * math.sin(theta)


def cursor_at(t, anchors, slide_centers):
    """Cursor (x, y) at video-time t. Smooth cubic-smoothstep transit +
    perpendicular arc on long moves + continuous slow elliptical orbit."""
    n = len(anchors)
    if t <= anchors[0][0]:
        x, y, beh, hw, hh = _resolve(anchors[0], slide_centers)
        ox, oy = _orbit_offset(beh, t, 0, hw, hh)
        return x + ox, y + oy
    if t >= anchors[-1][0]:
        x, y, beh, hw, hh = _resolve(anchors[-1], slide_centers)
        ox, oy = _orbit_offset(beh, t, n - 1, hw, hh)
        return x + ox, y + oy

    for i in range(n - 1):
        t0 = anchors[i][0]
        t1 = anchors[i + 1][0]
        if t0 <= t < t1:
            x0, y0, b0, hw0, hh0 = _resolve(anchors[i], slide_centers)
            x1, y1, b1, hw1, hh1 = _resolve(anchors[i + 1], slide_centers)
            u = (t - t0) / max(1e-6, t1 - t0)
            ue = u * u * (3 - 2 * u)

            base_x = x0 + ue * (x1 - x0)
            base_y = y0 + ue * (y1 - y0)

            dx, dy = x1 - x0, y1 - y0
            dist = math.hypot(dx, dy)
            if dist > 60:
                nx = -dy / dist
                ny = dx / dist
                arc_amp = min(35.0, dist * 0.06)
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

    x, y, beh, hw, hh = _resolve(anchors[-1], slide_centers)
    ox, oy = _orbit_offset(beh, t, n - 1, hw, hh)
    return x + ox, y + oy


def _segment_for_time(t):
    for sid, t0, t1 in SEGMENT_RANGES:
        if t0 <= t < t1:
            return sid
    return SEGMENT_RANGES[-1][0]


def make_cursor():
    img = Image.new("RGBA", (32, 40), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    pts = [(2, 2), (2, 30), (10, 22), (15, 36), (19, 34), (12, 20), (26, 20)]
    d.polygon(pts, fill=(255, 255, 255, 255), outline=(0, 0, 0, 255))
    return img


# ---------------------------------------------------------------------------
# Narration. Demonstrative deictic only at the 6 events; role descriptions
# live in non-deictic segments. Event 6 carries an audio-visual trap: at
# 200s the narration says "the diffusion coefficient" (= δ), then at 220s
# the deictic phrase is uttered while the cursor is on ∇².
# ---------------------------------------------------------------------------

NARRATION = [
    # eq_1
    (1.0, "Welcome. We open with a two-term linear recurrence."),
    (8.0, "On the left, x sub n plus one, the next-step value."),
    (
        16.0,
        "On the right we have two products. The constant coefficients a and b multiply the previous-step quantities x sub n and y sub n.",
    ),
    (30.0, "Look at the symbol my cursor is on right now."),  # EVENT 1
    (37.0, "Hold on that for a moment."),
    (47.0, "Notice the constants on the left and right of the plus sign."),
    (65.0, "And here, this character my cursor is on now."),  # EVENT 2
    (73.0, "All right, on to the next equation."),
    # eq_2
    (82.0, "Now a sinusoidal expansion of f of t."),
    (
        90.0,
        "Two amplitudes and an angular frequency. Alpha and beta drive the branch magnitudes; omega ties the phase to time.",
    ),
    (105.0, "Let me sweep across the right-hand side."),
    (
        109.0,
        "The cosine amplitude is visible on the right.",
    ),  # EVENT 3 trap prime → primes β
    (112.0, "Look at the symbol my cursor is over now."),  # EVENT 3 (cursor on α)
    (120.0, "Continuing."),
    (
        128.0,
        "And note that the angular frequency appears in both calls — same letter, two positions.",
    ),
    (
        137.0,
        "The sine amplitude sits on the left of the plus sign.",
    ),  # EVENT 4 trap prime → primes α
    (140.0, "And the symbol my cursor is on now."),  # EVENT 4 (cursor on β)
    (149.0, "Last equation coming up."),
    # eq_3
    (162.0, "The heat equation in one dimension."),
    (170.0, "Partial u by partial t equals a constant times the Laplacian of u."),
    (180.0, "Let me hover across the right-hand side."),
    (
        184.0,
        "The time derivative sits on the left side of the equation.",
    ),  # EVENT 5 trap prime → primes ∂u/∂t
    (186.5, "Look at the symbol my cursor is on right now."),  # EVENT 5 (cursor on δ)
    (195.0, "Onwards."),
    (
        200.0,
        "The diffusion coefficient governs the rate at which heat spreads through the medium — higher values mean faster smoothing.",
    ),
    (220.0, "And the symbol my cursor is over now."),  # EVENT 6 (TRAP — cursor on ∇²)
    (229.0, "That concludes today's lecture."),
]


# Hidden gold (build introspection only; verifier carries its own copy).
GOLD = [
    {"event_id": 1, "timestamp_sec": 32.0, "term_string": "x_n", "equation_id": "eq_1"},
    {"event_id": 2, "timestamp_sec": 67.0, "term_string": "b", "equation_id": "eq_1"},
    {"event_id": 3, "timestamp_sec": 115.0, "term_string": "α", "equation_id": "eq_2"},
    {"event_id": 4, "timestamp_sec": 143.0, "term_string": "β", "equation_id": "eq_2"},
    {"event_id": 5, "timestamp_sec": 188.0, "term_string": "δ", "equation_id": "eq_3"},
    {"event_id": 6, "timestamp_sec": 223.0, "term_string": "∇²", "equation_id": "eq_3"},
]


# ---------------------------------------------------------------------------
# Audio synthesis (Kokoro).
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


def render_video(slide_data, audio_wav: Path) -> Path:
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

    slide_centers = {sid: c for sid, (_img, c) in slide_data.items()}

    last_seg = None
    base_frame = None
    for i in range(n_frames):
        t = i / FPS
        sid = _segment_for_time(t)
        if sid != last_seg:
            base_frame = slide_data[sid][0]
            last_seg = sid
        x, y = cursor_at(t, CURSOR_ANCHORS, slide_centers)
        frame = base_frame.copy()
        frame.paste(cursor_img, (int(x) - 2, int(y) - 2), cursor_img)
        frame.save(frames_dir / f"f_{i:06d}.png", optimize=False, compress_level=1)
        if i % 240 == 0:
            print(f"  rendered frame {i}/{n_frames} (t={t:.1f}s seg={sid})", flush=True)

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
    print("Step 1/3: render slides via Playwright + MathJax", flush=True)
    slide_data = render_slides_via_playwright()

    print("Step 2/3: synthesize narration", flush=True)
    audio_wav = synth_narration()

    print("Step 3/3: composite frames + encode video", flush=True)
    out = render_video(slide_data, audio_wav)
    print(f"Wrote {out} ({out.stat().st_size / 1e6:.1f} MB)", flush=True)

    sha = hashlib.sha256(out.read_bytes()).hexdigest()
    print(f"sha256 = {sha}", flush=True)

    (WORK / "gold.json").write_text(json.dumps(GOLD, indent=2, ensure_ascii=False))
    print(f"Gold ({len(GOLD)} events) written to {WORK / 'gold.json'}")


if __name__ == "__main__":
    main()
