#!/usr/bin/env python3
"""Build the av-identity-leak-detect clip (v4 — harder joint AV).

v3 shipped a realistic 5-slide deck with 2 leaks; MM v3 = 1.000.
v4 hardens the perception requirement so MM can no longer ace it:

  - 8 slides × 20s = 160s clip (was 5 × 20s = 100s).
  - 4 true cross-channel leaks (was 2). Agent must enumerate all of them.
  - 4 decoy slides that share *some* surface cues with leaks but don't
    actually constitute a same-window cross-channel leak:
      * Title slide (presenter name+logo — public identity, not redacted)
      * Org-chart slide (4 badges + anonymized headcount narration)
      * Distractor slide D1: badge visible + spoken first names but the
        names refer to other people in the timeline ("Mike will own Q4"),
        NOT to the person hidden behind the visible badge.
      * Distractor slide D2: bar chart (no badge) + spoken first names —
        audio-only, missing the visible-badge channel.
  - Tight audio-cue sub-window gold (4-6s, not the whole 20s slide).
    Agents that default to slide-level windows fail the IoU=0.5
    localization gate.
  - Verifier switches to F1 reward: precision + recall, so over-flagging
    decoys actively penalizes the agent.

Audio: Kokoro-82M (am_michael) for the facilitator. Slide images use
real Gaussian-blurred portrait placeholders for the leak slides.

Run: uv run --with kokoro --with soundfile --with pillow --with numpy \\
     python -u build_assets.py
"""

from __future__ import annotations

import json
import shutil
import subprocess
import wave
from pathlib import Path

import numpy as np
import soundfile as sf
from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = Path(__file__).parent
ASSETS = ROOT / "environment" / "assets"
WORK = ROOT / ".build"
ASSETS.mkdir(parents=True, exist_ok=True)
WORK.mkdir(parents=True, exist_ok=True)

W, H = 1280, 720
FPS = 30
SAMPLE_RATE = 24_000
SCENE_DUR = 20.0

# Slide plan. Each slide is 20 s long. Gold sub-windows below capture
# the audio-cue span when the cross-channel leak is actually occurring.
SLIDES = [
    # 0-20s: title slide. SAFE.
    {
        "type": "title",
        "title": "Q3 Department Lead Review",
        "subtitle": "Quarterly all-hands · Internal use only",
        "presenter": "Marina Chen · Director, People Operations",
        "date": "2026-04-15",
        "tts": (
            "Welcome to the Q-3 department lead review. "
            "I'm Marina, and today I'm walking us through five new "
            "department leads, the timeline for their onboarding, and "
            "where they fit on our roadmap for the rest of the year."
        ),
        "leak": False, "leak_type": "",
    },
    # 20-40s: org-chart slide (4 dept codes, anonymized). SAFE.
    {
        "type": "org_chart",
        "title": "Public org chart — department codes",
        "subtitle": "Used externally whenever staff identities need to stay anonymous",
        "departments": [
            {"code": "MED-12", "name": "Medical",        "headcount": 24, "color": (220, 38, 38)},
            {"code": "ENG-08", "name": "Engineering",    "headcount": 18, "color": (37, 99, 235)},
            {"code": "TECH-04","name": "Technology",     "headcount": 14, "color": (5, 150, 105)},
            {"code": "ADM-02", "name": "Administrative", "headcount": 9,  "color": (132, 90, 36)},
        ],
        "tts": (
            "Here's the public-release version of our org chart with "
            "four departments. We use these department codes externally "
            "any time staff identities need to stay anonymous, so there "
            "are no names on this slide intentionally."
        ),
        "leak": False, "leak_type": "",
    },
    # 40-60s: LEAK A — title-and-badge ENG-08. Audio cue: 46-51s.
    {
        "type": "leak",
        "title": "New engineering captain",
        "big_badge": "ENG-08",
        "department_name": "Engineering",
        "start_date": "Monday 2026-04-27",
        "mandate": "Bridge protocols · On-call rotation",
        "tts_lead_silence": 5.5,  # 40 + 5.5 = 45.5 → audio cue starts ~46s
        "tts": (
            "Our incoming engineering captain will be heading the team "
            "starting Monday, with a focus on bridge protocols and "
            "the on-call rotation that's been on the back burner."
        ),
        "leak": True, "leak_type": "title-and-badge",
        "gold_start_offset": 5.5, "gold_end_offset": 11.5,
    },
    # 60-80s: DECOY D1 — badge visible + spoken first names that DO NOT
    #         identify the person behind the badge. NOT a leak.
    {
        "type": "decoy_badge",
        "title": "Coordination across teams",
        "subtitle": "Badge in corner is for context only",
        "small_badge": "MED-12",
        "tts_lead_silence": 4.0,
        "tts": (
            "Looking at coordination across teams. Mike will own the "
            "overall Q-4 timeline, with Rajan supporting on the "
            "integration milestones in week four and week eight. "
            "Plenty of detail in the appendix."
        ),
        "leak": False, "leak_type": "",
    },
    # 80-100s: LEAK B — name-and-badge MED-12 (Sarah). Audio cue: 84-90s.
    {
        "type": "leak",
        "title": "New medical lead",
        "big_badge": "MED-12",
        "department_name": "Medical",
        "start_date": "Next week",
        "mandate": "Joins from partner hospital network",
        "tts_lead_silence": 4.0,
        "tts": (
            "And our new medical lead, Sarah, will be scrubbing in "
            "starting next week. Sarah joins us from the partner "
            "hospital network, and the welcome reception is Friday."
        ),
        "leak": True, "leak_type": "name-and-badge",
        "gold_start_offset": 4.0, "gold_end_offset": 11.0,
    },
    # 100-120s: DECOY D2 — chart, NO badge visible, spoken first names.
    #           Audio-only — missing the visual-badge channel. NOT a leak.
    {
        "type": "audio_chart",
        "title": "Q4 integration timeline",
        "subtitle": "Cross-team milestones · weeks 1-12",
        "tts_lead_silence": 4.0,
        "tts": (
            "Quick look at the Q-4 timeline. Rajan and Priya will "
            "handle the cross-team checkpoints, and the milestone "
            "owner names are listed in the appendix."
        ),
        "leak": False, "leak_type": "",
    },
    # 120-140s: LEAK C — title-and-badge TECH-04 (small corner badge).
    #           Subtle visual cue tests perception. Audio cue: 124-129s.
    {
        "type": "leak_small",
        "title": "Technology workstream",
        "subtitle": "Q4 hand-off",
        "small_badge": "TECH-04",
        "tts_lead_silence": 4.0,
        "tts": (
            "Our technology lead is taking over the Q-4 hand-off, "
            "starting with the platform migration and the data-pipeline "
            "consolidation we've been pushing on all quarter."
        ),
        "leak": True, "leak_type": "title-and-badge",
        "gold_start_offset": 4.0, "gold_end_offset": 10.0,
    },
    # 140-160s: LEAK D — name-and-badge ADM-02 (Priya). Audio cue: 144-150s.
    {
        "type": "leak",
        "title": "New administrative lead",
        "big_badge": "ADM-02",
        "department_name": "Administrative",
        "start_date": "May 4th",
        "mandate": "Operations · Vendor coordination",
        "tts_lead_silence": 4.0,
        "tts": (
            "And finally, our administrative lead, Priya, will be "
            "starting May fourth. Priya is taking over operations and "
            "all vendor coordination from day one."
        ),
        "leak": True, "leak_type": "name-and-badge",
        "gold_start_offset": 4.0, "gold_end_offset": 10.5,
    },
]


# ---------------------------------------------------------------------------
# Slide rendering (PIL).
# ---------------------------------------------------------------------------

def _font(size, bold=False):
    cands = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold
        else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    for c in cands:
        if Path(c).exists():
            return ImageFont.truetype(c, size)
    return ImageFont.load_default()


def _logo_mark(d, x, y, size=28):
    d.rectangle([x, y, x + size, y + size], fill=(37, 99, 235))
    f = _font(int(size * 0.7), bold=True)
    bbox = d.textbbox((0, 0), "A", font=f)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]
    d.text((x + (size - tw) // 2, y + (size - th) // 2 - 2), "A", fill=(255, 255, 255), font=f)


def _slide_footer(img, slide_no, total):
    d = ImageDraw.Draw(img)
    f = _font(13)
    f_b = _font(13, bold=True)
    y = H - 36
    d.line([(40, y - 6), (W - 40, y - 6)], fill=(203, 213, 225), width=1)
    d.text((40, y), "ACME · INTERNAL · 2026-Q3", fill=(100, 116, 139), font=f)
    right = f"{slide_no} / {total}"
    bbox = d.textbbox((0, 0), right, font=f_b)
    rw = bbox[2] - bbox[0]
    d.text((W - 40 - rw, y), right, fill=(71, 85, 105), font=f_b)


def render_title_slide(slide, slide_no, total):
    arr = np.zeros((H, W, 3), dtype=np.uint8)
    top = np.array([15, 23, 42])
    bot = np.array([30, 41, 70])
    for y in range(H):
        a = y / max(H - 1, 1)
        arr[y, :, :] = (top * (1 - a) + bot * a).astype(np.uint8)
    img = Image.fromarray(arr)
    d = ImageDraw.Draw(img)
    _logo_mark(d, 40, 36, size=32)
    d.text((84, 42), "Acme · People Ops", fill=(203, 213, 225), font=_font(15, bold=True))
    f_meta = _font(14)
    txt = slide["date"]
    bbox = d.textbbox((0, 0), txt, font=f_meta)
    d.text((W - 40 - (bbox[2] - bbox[0]), 50), txt, fill=(148, 163, 184), font=f_meta)

    f_title = _font(56, bold=True)
    f_sub = _font(22)
    f_pres = _font(18)
    bbox = d.textbbox((0, 0), slide["title"], font=f_title)
    tw = bbox[2] - bbox[0]
    d.text(((W - tw) // 2, H // 2 - 80), slide["title"], fill=(241, 245, 249), font=f_title)
    bbox = d.textbbox((0, 0), slide["subtitle"], font=f_sub)
    sw = bbox[2] - bbox[0]
    d.text(((W - sw) // 2, H // 2 - 10), slide["subtitle"], fill=(148, 163, 184), font=f_sub)
    d.rectangle([W // 2 - 60, H // 2 + 30, W // 2 + 60, H // 2 + 32], fill=(96, 165, 250))
    bbox = d.textbbox((0, 0), slide["presenter"], font=f_pres)
    pw = bbox[2] - bbox[0]
    d.text(((W - pw) // 2, H // 2 + 60), slide["presenter"], fill=(203, 213, 225), font=f_pres)

    d2 = ImageDraw.Draw(img)
    d2.rectangle([0, H - 50, W, H], fill=(15, 23, 42))
    f = _font(13)
    d2.text((40, H - 36), "ACME · INTERNAL · 2026-Q3", fill=(148, 163, 184), font=f)
    right = f"{slide_no} / {total}"
    bbox = d2.textbbox((0, 0), right, font=f)
    rw = bbox[2] - bbox[0]
    d2.text((W - 40 - rw, H - 36), right, fill=(203, 213, 225), font=f)
    return img


def render_org_chart_slide(slide, slide_no, total):
    img = Image.new("RGB", (W, H), (244, 246, 250))
    d = ImageDraw.Draw(img)
    _logo_mark(d, 40, 36, size=28)
    d.text((78, 41), "Acme · People Ops", fill=(71, 85, 105), font=_font(13, bold=True))
    f_title = _font(34, bold=True)
    f_sub = _font(15)
    d.text((40, 90), slide["title"], fill=(15, 23, 42), font=f_title)
    d.text((40, 132), slide["subtitle"], fill=(100, 116, 139), font=f_sub)
    d.rectangle([40, 165, 100, 167], fill=(37, 99, 235))

    depts = slide["departments"]
    card_w, card_h = 460, 180
    gap = 30
    grid_w = 2 * card_w + gap
    start_x = (W - grid_w) // 2
    start_y = 200
    for i, dep in enumerate(depts):
        col = i % 2
        row = i // 2
        x = start_x + col * (card_w + gap)
        y = start_y + row * (card_h + gap)
        d.rectangle([x, y, x + card_w, y + card_h], fill=(255, 255, 255), outline=(203, 213, 225), width=1)
        d.rectangle([x, y, x + card_w, y + 40], fill=dep["color"])
        f_code = _font(20, bold=True)
        d.text((x + 18, y + 10), dep["code"], fill=(255, 255, 255), font=f_code)
        f_lbl = _font(13)
        lbl = "DEPARTMENT CODE"
        bbox = d.textbbox((0, 0), lbl, font=f_lbl)
        d.text((x + card_w - 18 - (bbox[2] - bbox[0]), y + 14), lbl, fill=(255, 255, 255, 200), font=f_lbl)
        f_name = _font(28, bold=True)
        f_meta = _font(14)
        f_redacted = _font(13)
        d.text((x + 22, y + 60), dep["name"], fill=(15, 23, 42), font=f_name)
        d.text((x + 22, y + 105), f"Headcount: {dep['headcount']} staff", fill=(71, 85, 105), font=f_meta)
        d.text((x + 22, y + 130), "Names: redacted (public release)", fill=(148, 163, 184), font=f_redacted)
        lx, ly = x + card_w - 32, y + 130
        d.rectangle([lx + 4, ly + 4, lx + 14, ly + 12], outline=(148, 163, 184), width=1)
        d.rectangle([lx + 2, ly + 6, lx + 16, ly + 16], fill=(148, 163, 184))
    _slide_footer(img, slide_no, total)
    return img


def render_blurred_portrait(width=190, height=230, seed=0):
    base = Image.new("RGB", (width, height), (228, 232, 240))
    d = ImageDraw.Draw(base)
    rng = np.random.default_rng(1234 + seed)
    hair_palette = [(95, 70, 55), (120, 90, 70), (60, 55, 50), (140, 110, 85)]
    skin_palette = [(228, 195, 170), (215, 175, 145), (200, 165, 140), (235, 205, 180)]
    shirt_palette = [(70, 90, 130), (90, 100, 110), (60, 80, 110), (110, 100, 130)]
    hair = hair_palette[seed % len(hair_palette)]
    skin = skin_palette[seed % len(skin_palette)]
    shirt = shirt_palette[seed % len(shirt_palette)]
    d.ellipse([20, 5, width - 20, 130], fill=hair)
    d.ellipse([35, 30, width - 35, 155], fill=skin)
    d.rectangle([width // 2 - 22, 140, width // 2 + 22, 175], fill=skin)
    d.ellipse([0, 165, width, height + 50], fill=shirt)
    arr = np.array(base).astype(np.int32)
    noise = rng.integers(-12, 12, arr.shape, dtype=np.int32)
    arr = np.clip(arr + noise, 0, 255).astype(np.uint8)
    base = Image.fromarray(arr)
    return base.filter(ImageFilter.GaussianBlur(radius=20))


def render_leak_slide(slide, slide_no, total):
    img = Image.new("RGB", (W, H), (244, 246, 250))
    d = ImageDraw.Draw(img)
    _logo_mark(d, 40, 36, size=28)
    d.text((78, 41), "Acme · People Ops", fill=(71, 85, 105), font=_font(13, bold=True))
    d.text((40, 90), slide["title"], fill=(15, 23, 42), font=_font(34, bold=True))
    d.rectangle([40, 137, 100, 139], fill=(37, 99, 235))

    portrait_seed = {"ENG-08": 1, "MED-12": 2, "ADM-02": 3, "TECH-04": 4}.get(slide["big_badge"], 0)
    portrait = render_blurred_portrait(width=260, height=320, seed=portrait_seed)
    px, py = 100, 200
    img.paste(portrait, (px, py))
    d.rectangle([px - 4, py - 4, px + portrait.width + 4, py + portrait.height + 4],
                outline=(203, 213, 225), width=2)

    rx = 480
    ry = 200
    f_lbl = _font(14)
    f_val = _font(20, bold=True)
    f_badge = _font(60, bold=True)
    d.text((rx, ry), "DEPARTMENT", fill=(100, 116, 139), font=f_lbl)
    d.text((rx, ry + 22), slide["department_name"], fill=(15, 23, 42), font=f_val)
    d.text((rx, ry + 80), "DEPARTMENT CODE", fill=(100, 116, 139), font=f_lbl)
    badge_text = slide["big_badge"]
    bbox = d.textbbox((0, 0), badge_text, font=f_badge)
    bw = bbox[2] - bbox[0]
    bh = bbox[3] - bbox[1]
    box_w = bw + 60
    box_h = bh + 30
    d.rectangle([rx, ry + 105, rx + box_w, ry + 105 + box_h], fill=(185, 28, 28))
    d.text((rx + 30, ry + 110), badge_text, fill=(255, 255, 255), font=f_badge)
    d.text((rx, ry + 220), "START DATE", fill=(100, 116, 139), font=f_lbl)
    d.text((rx, ry + 242), slide["start_date"], fill=(15, 23, 42), font=f_val)
    d.text((rx, ry + 290), "FOCUS", fill=(100, 116, 139), font=f_lbl)
    d.text((rx, ry + 312), slide["mandate"], fill=(15, 23, 42), font=_font(16))

    _slide_footer(img, slide_no, total)
    return img


def render_leak_small_slide(slide, slide_no, total):
    """Subtle visual: small badge in corner, slide otherwise looks like a content slide."""
    img = Image.new("RGB", (W, H), (244, 246, 250))
    d = ImageDraw.Draw(img)
    _logo_mark(d, 40, 36, size=28)
    d.text((78, 41), "Acme · People Ops", fill=(71, 85, 105), font=_font(13, bold=True))
    d.text((40, 90), slide["title"], fill=(15, 23, 42), font=_font(34, bold=True))
    d.text((40, 132), slide["subtitle"], fill=(100, 116, 139), font=_font(15))
    d.rectangle([40, 165, 100, 167], fill=(37, 99, 235))

    # Small badge in upper-right corner.
    badge = slide["small_badge"]
    f_b = _font(14, bold=True)
    bbox = d.textbbox((0, 0), badge, font=f_b)
    bw = bbox[2] - bbox[0]
    bx = W - 60 - bw
    by = 95
    d.rectangle([bx, by, bx + bw + 16, by + 22], fill=(5, 150, 105))
    d.text((bx + 8, by + 4), badge, fill=(255, 255, 255), font=f_b)

    # Body content: a few bullet points to fill the slide.
    bullets = [
        "Platform migration: weeks 1-4",
        "Data-pipeline consolidation: weeks 3-6",
        "Hand-off review: week 7",
        "Final cut-over: week 10",
    ]
    f_body = _font(20)
    for i, b in enumerate(bullets):
        y = 240 + i * 56
        d.ellipse([60, y + 8, 72, y + 20], fill=(37, 99, 235))
        d.text((90, y), b, fill=(15, 23, 42), font=f_body)

    _slide_footer(img, slide_no, total)
    return img


def render_decoy_badge_slide(slide, slide_no, total):
    """Decoy: badge visible but the spoken names in audio refer to other people."""
    img = Image.new("RGB", (W, H), (244, 246, 250))
    d = ImageDraw.Draw(img)
    _logo_mark(d, 40, 36, size=28)
    d.text((78, 41), "Acme · People Ops", fill=(71, 85, 105), font=_font(13, bold=True))
    d.text((40, 90), slide["title"], fill=(15, 23, 42), font=_font(34, bold=True))
    d.text((40, 132), slide["subtitle"], fill=(100, 116, 139), font=_font(15))
    d.rectangle([40, 165, 100, 167], fill=(37, 99, 235))

    badge = slide["small_badge"]
    f_b = _font(14, bold=True)
    bbox = d.textbbox((0, 0), badge, font=f_b)
    bw = bbox[2] - bbox[0]
    bx = W - 60 - bw
    by = 95
    d.rectangle([bx, by, bx + bw + 16, by + 22], fill=(220, 38, 38))
    d.text((bx + 8, by + 4), badge, fill=(255, 255, 255), font=f_b)

    # Body: two columns of text describing coordination, NOT identifying badge holder.
    f_body = _font(18)
    items = [
        ("Q4 timeline owner:", "(see appendix)"),
        ("Integration milestone:", "weeks 4 + 8"),
        ("Cross-team checkpoint:", "weekly stand-up"),
        ("Budget review:", "monthly close"),
    ]
    for i, (k, v) in enumerate(items):
        y = 240 + i * 56
        d.text((90, y), k, fill=(71, 85, 105), font=f_body)
        d.text((420, y), v, fill=(15, 23, 42), font=_font(18, bold=True))

    _slide_footer(img, slide_no, total)
    return img


def render_audio_chart_slide(slide, slide_no, total):
    img = Image.new("RGB", (W, H), (244, 246, 250))
    d = ImageDraw.Draw(img)
    _logo_mark(d, 40, 36, size=28)
    d.text((78, 41), "Acme · People Ops", fill=(71, 85, 105), font=_font(13, bold=True))
    d.text((40, 90), slide["title"], fill=(15, 23, 42), font=_font(34, bold=True))
    d.text((40, 132), slide["subtitle"], fill=(100, 116, 139), font=_font(15))
    d.rectangle([40, 165, 100, 167], fill=(37, 99, 235))

    cx, cy = 80, 220
    cw, ch = W - 160, 380
    d.rectangle([cx, cy, cx + cw, cy + ch], fill=(255, 255, 255), outline=(203, 213, 225), width=1)
    n_weeks = 12
    bar_gap = 12
    bar_w = (cw - 80 - bar_gap * (n_weeks - 1)) // n_weeks
    bar_max = ch - 80
    heights = [0.45, 0.62, 0.78, 0.55, 0.40, 0.66, 0.82, 0.74, 0.58, 0.70, 0.88, 0.95]
    f_tick = _font(11)
    for i, frac in enumerate([0.0, 0.25, 0.5, 0.75, 1.0]):
        ty = cy + ch - 40 - int(bar_max * frac)
        d.line([cx + 60, ty, cx + cw - 20, ty], fill=(226, 232, 240), width=1)
        d.text((cx + 12, ty - 7), f"{int(frac * 100):3d}%", fill=(100, 116, 139), font=f_tick)
    f_lab = _font(12)
    for i, h in enumerate(heights):
        bx = cx + 60 + i * (bar_w + bar_gap)
        bh = int(bar_max * h)
        by = cy + ch - 40 - bh
        d.rectangle([bx, by, bx + bar_w, cy + ch - 40], fill=(37, 99, 235))
        wlbl = f"W{i + 1}"
        bbox = d.textbbox((0, 0), wlbl, font=f_lab)
        lw = bbox[2] - bbox[0]
        d.text((bx + (bar_w - lw) // 2, cy + ch - 32), wlbl, fill=(71, 85, 105), font=f_lab)
    d.text((cx + cw // 2 - 50, cy + ch - 12), "Weeks (Q4)", fill=(71, 85, 105), font=_font(13, bold=True))
    lx = cx + cw - 250
    ly = cy + 14
    d.rectangle([lx, ly, lx + 16, ly + 12], fill=(37, 99, 235))
    d.text((lx + 24, ly - 2), "Integration milestone completion", fill=(71, 85, 105), font=f_lab)

    _slide_footer(img, slide_no, total)
    return img


def render_slide_image(slide, slide_no, total):
    if slide["type"] == "title":
        return render_title_slide(slide, slide_no, total)
    if slide["type"] == "org_chart":
        return render_org_chart_slide(slide, slide_no, total)
    if slide["type"] == "leak":
        return render_leak_slide(slide, slide_no, total)
    if slide["type"] == "leak_small":
        return render_leak_small_slide(slide, slide_no, total)
    if slide["type"] == "decoy_badge":
        return render_decoy_badge_slide(slide, slide_no, total)
    if slide["type"] == "audio_chart":
        return render_audio_chart_slide(slide, slide_no, total)
    raise ValueError(slide["type"])


# ---------------------------------------------------------------------------
# Audio (Kokoro).
# ---------------------------------------------------------------------------

def synth_slide_audio(tts, text):
    chunks = []
    for _gs, _ps, audio in tts(text, voice="am_michael"):
        chunks.append(audio)
    return np.concatenate(chunks).astype(np.float32)


def build_segment(idx, slide, tts, total):
    img = render_slide_image(slide, idx + 1, total)
    img_path = WORK / f"slide_{idx}.png"
    img.save(img_path)
    audio = synth_slide_audio(tts, slide["tts"])
    target_n = int(SCENE_DUR * SAMPLE_RATE)
    # Honor explicit lead-silence so the audio cue lines up with the gold sub-window.
    lead = slide.get("tts_lead_silence")
    if lead is None:
        lead = max(0.5, (SCENE_DUR - audio.shape[0] / SAMPLE_RATE) / 2.0)
    full = np.concatenate([
        np.zeros(int(lead * SAMPLE_RATE), dtype=np.float32),
        audio,
        np.zeros(target_n, dtype=np.float32),
    ])[:target_n]
    audio_path = WORK / f"slide_{idx}.wav"
    sf.write(str(audio_path), full, SAMPLE_RATE)
    seg_path = WORK / f"seg_{idx}.mp4"
    cmd = [
        "ffmpeg", "-y",
        "-loop", "1", "-framerate", str(FPS), "-i", str(img_path),
        "-i", str(audio_path),
        "-t", str(SCENE_DUR),
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "24",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "96k", "-ar", str(SAMPLE_RATE), "-ac", "1",
        "-shortest", str(seg_path),
    ]
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return seg_path


def main():
    import time
    print("Loading Kokoro pipeline (American)…", flush=True)
    from kokoro import KPipeline
    t0 = time.time()
    tts = KPipeline(lang_code="a")
    print(f"  ready ({time.time() - t0:.1f}s)", flush=True)

    total = len(SLIDES)
    seg_paths = []
    for i, slide in enumerate(SLIDES):
        print(f"Building slide {i} ({slide['type']:13s}, leak={slide['leak']})", flush=True)
        ts = time.time()
        seg_paths.append(build_segment(i, slide, tts, total))
        print(f"  built in {time.time() - ts:.1f}s", flush=True)

    list_file = WORK / "segments.txt"
    list_file.write_text("\n".join(f"file '{p}'" for p in seg_paths) + "\n")
    out = ASSETS / "clip.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(list_file),
         "-c:v", "copy", "-c:a", "aac", "-b:a", "96k", str(out)],
        check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    print(f"Wrote {out} ({out.stat().st_size / 1e6:.1f} MB)", flush=True)

    # Hidden gold (build-only). Tight audio-cue sub-windows.
    gold = []
    for i, slide in enumerate(SLIDES):
        if slide["leak"]:
            slide_start = i * SCENE_DUR
            gold.append({
                "start": round(slide_start + slide["gold_start_offset"], 2),
                "end":   round(slide_start + slide["gold_end_offset"], 2),
                "leak_type": slide["leak_type"],
            })
    (WORK / "recording_gold.json").write_text(json.dumps(gold, indent=2))
    print(f"Gold leaks: {gold}", flush=True)


if __name__ == "__main__":
    main()
