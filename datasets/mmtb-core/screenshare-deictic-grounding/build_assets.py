#!/usr/bin/env python3
"""Build the screenshare-deictic-grounding screencast (v3 — realistic motion).

Produces ./environment/assets/recording.webm — a ~95 s Playwright-rendered
sprint-board screencast with a single facilitator narrator. Each of the
four decisions requires multi-hop reasoning combining:

  - Verbal POLICY stated by the speaker (audio).
  - Multiple visual fields (card effort estimates, days-in-status badges,
    tag combinations across columns, owner avatars in cross-column reach).

v3 vs v2:
  - Stronger reasoning traps:
      D01 quantitative ambiguity (21 vs 20 pts close-call instead of >half).
      D03 2-of-3 distractor (in-review + 9d but missing QA-approved tag).
      D04 4-hop chain with tie-break (Backlog owner ALSO owns Done card →
            tie-break by highest effort → card-102 over card-104).
  - Cursor motion library: MoveTo / Rest / Circle / Underline / IdleDrift.
    Speaker circles around fields, underlines tags, idle-drifts during
    pauses. Crucially, several Circle/Underline events land on DECOY cards
    (card-202, card-302, card-303, card-103) so cursor-only baselines that
    treat emphasis as a pointer are actively misled — only joint AV
    reasoning over the verbal policy + visible fields resolves the gold.

Audio: Kokoro-82M (am_michael) for the facilitator. Kokoro is markedly
faster than Chatterbox on CPU and natural enough for this format.

Run: uv run --with kokoro --with soundfile --with pillow \\
     --with playwright --with numpy python -u build_assets.py
"""

from __future__ import annotations

import json
import math
import random
import shutil
import subprocess
import wave
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import soundfile as sf
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).parent
ASSETS = ROOT / "environment" / "assets"
WORK = ROOT / ".build"
ASSETS.mkdir(parents=True, exist_ok=True)
WORK.mkdir(parents=True, exist_ok=True)

WIDTH, HEIGHT = 1600, 900
FPS = 30
SAMPLE_RATE = 24_000  # Kokoro native

# -------------------------------------------------------------------------
# Narration timeline. Each entry: (start_sec_target, text).
# The speaker states the POLICY but does NOT name the answer card. The agent
# must apply the policy to the visible board state.
# -------------------------------------------------------------------------
NARRATION = [
    (0.5,
     "Hey team, four sprint-board decisions to walk through. "
     "Apply each rule to the visible board state — I won't be naming the cards."),
    # D01 — highest-effort policy (~12-30s).
    (12.0,
     "First decision. Skip the in-progress card with the highest effort estimate. "
     "Look carefully — two cards in that column are in the high-effort range, "
     "and the difference is a single point. The effort badge is the small "
     "amber chip in the top-right corner of each card."),
    # Pause for cursor to scan In Progress.
    # D02 — multi-hop ownership (~32-48s).
    (32.0,
     "Second decision. Approve the Q-A-approved card whose owner also has "
     "a P-zero priority card somewhere on the board. There are two cards "
     "tagged Q-A approved — find the one whose owner appears on a P-zero "
     "card."),
    # Pause.
    # D03 — QA-approved AND >7 days (~50-64s, 2-of-3 distractor).
    (50.0,
     "Third decision. Block any card in the In Review column that is "
     "Q-A approved and has been sitting in review for more than seven days. "
     "Both conditions must hold — designer-review tags don't count, even "
     "if the card has been in review for over a week."),
    # Pause.
    # D04 — Backlog owner ALSO owns Done card; tie-break by highest effort (~70-88s).
    (70.0,
     "Last one. Assign Mina to the Backlog card whose owner also owns "
     "a Done-column card. If more than one Backlog card qualifies, take the "
     "one with the highest effort estimate. Due Friday, May first, "
     "twenty twenty-six."),
    # Outro.
    (90.0,
     "All right, that's it. Thanks team."),
]


# -------------------------------------------------------------------------
# Cursor motion library. Each Motion subclass renders an absolute (x,y)
# at a given time t in [t_start, t_end], given the cursor position at
# event start (start_pos) and the resolved anchor (target_pos).
# -------------------------------------------------------------------------

@dataclass
class Motion:
    t_start: float
    duration: float
    target: Any = None  # str "#elem-id", tuple (x,y), or None for IdleDrift
    start_pos: tuple[float, float] = field(default=(0.0, 0.0), init=False)
    target_pos: tuple[float, float] = field(default=(0.0, 0.0), init=False)
    end_pos: tuple[float, float] = field(default=(0.0, 0.0), init=False)

    @property
    def t_end(self) -> float:
        return self.t_start + self.duration

    def _local(self, t: float) -> float:
        if self.duration <= 0:
            return 1.0
        return max(0.0, min(1.0, (t - self.t_start) / self.duration))

    def resolve(self, t: float) -> tuple[float, float]:
        raise NotImplementedError

    def finalize_end_pos(self) -> None:
        self.end_pos = self.target_pos


def _smoothstep(a: float) -> float:
    return a * a * (3 - 2 * a)


class MoveTo(Motion):
    def resolve(self, t):
        a = _smoothstep(self._local(t))
        x = self.start_pos[0] + a * (self.target_pos[0] - self.start_pos[0])
        y = self.start_pos[1] + a * (self.target_pos[1] - self.start_pos[1])
        return (x, y)


class Rest(Motion):
    def resolve(self, t):
        return self.target_pos


class Circle(Motion):
    """Circle around target with bell-shaped radius — biggest mid-event."""
    radius: float = 42.0
    cycles: float = 1.25

    def __init__(self, t_start, duration, target, radius=42.0, cycles=1.25):
        super().__init__(t_start, duration, target)
        self.radius = radius
        self.cycles = cycles

    def resolve(self, t):
        local = self._local(t)
        # Bell-shaped radius envelope: 0 at edges, max in middle.
        env = math.sin(math.pi * local)
        r = self.radius * env
        omega = 2 * math.pi * self.cycles
        x = self.target_pos[0] + r * math.cos(omega * local)
        y = self.target_pos[1] + r * math.sin(omega * local)
        return (x, y)

    def finalize_end_pos(self):
        # Ends at target (radius=0 at local=1).
        self.end_pos = self.target_pos


class Underline(Motion):
    """Sweep horizontally below target: left → right → left."""
    half_width: float = 80.0
    y_offset: float = 22.0

    def __init__(self, t_start, duration, target, half_width=80.0, y_offset=22.0):
        super().__init__(t_start, duration, target)
        self.half_width = half_width
        self.y_offset = y_offset

    def resolve(self, t):
        local = self._local(t)
        if local < 0.5:
            a = local * 2.0
            x_off = -self.half_width + 2.0 * self.half_width * a
        else:
            a = (local - 0.5) * 2.0
            x_off = self.half_width - 2.0 * self.half_width * a
        return (self.target_pos[0] + x_off, self.target_pos[1] + self.y_offset)

    def finalize_end_pos(self):
        # Ends back at left edge (left-right-left sweep).
        self.end_pos = (self.target_pos[0] - self.half_width, self.target_pos[1] + self.y_offset)


class IdleDrift(Motion):
    """Smooth random wandering anchored near target — sum of sines."""

    def resolve(self, t):
        local = t - self.t_start
        ax = (60.0 * math.sin(local * 1.4 + 0.3)
              + 35.0 * math.sin(local * 0.7 + 1.2)
              + 18.0 * math.sin(local * 2.3 + 2.4))
        ay = (40.0 * math.sin(local * 1.1 + 1.8)
              + 25.0 * math.sin(local * 0.6 + 0.8)
              + 12.0 * math.sin(local * 1.9 + 0.3))
        return (self.target_pos[0] + ax, self.target_pos[1] + ay)

    def finalize_end_pos(self):
        # End at the resolved drift position at t_end.
        self.end_pos = self.resolve(self.t_end)


# Cursor event timeline. Anchors are #element-ids or absolute (x,y).
# Several Circle/Underline events deliberately emphasize DECOY cards
# (card-202, card-302, card-303, card-103) — cursor-only baselines treat
# emphasis as pointer and are misled. Only the verbal policy resolves
# the gold card.
def make_events() -> list[Motion]:
    return [
        # Intro (0-12s).
        Rest(0.0, 2.5, (800, 30)),
        IdleDrift(2.5, 7.0, (800, 250)),
        MoveTo(9.5, 2.5, "#capacity-widget"),
        # D01 (12-32s) — scan In Progress, underline DECOY card-202 (20pts).
        Circle(12.0, 3.0, "#col-progress", radius=80.0, cycles=0.75),
        MoveTo(15.0, 1.6, "#card-201"),
        MoveTo(16.6, 1.8, "#card-203"),
        MoveTo(18.4, 1.8, "#card-202"),
        MoveTo(20.2, 1.6, "#card-204"),
        IdleDrift(21.8, 4.2, (800, 360)),
        # MISLEADING: underline the 20-pts decoy (card-202), not the gold.
        Underline(26.0, 4.0, "#card-202", half_width=70.0),
        MoveTo(30.0, 2.0, "#col-progress"),
        # D02 (32-50s) — owner-overlap.
        MoveTo(32.0, 2.0, "#card-301"),
        MoveTo(34.0, 2.0, "#card-303"),
        IdleDrift(36.0, 3.0, (1100, 470)),
        MoveTo(39.0, 2.0, "#card-203"),  # cross to In Progress for P0
        Circle(41.0, 3.0, "#card-203", radius=36.0),  # circle on P0 card
        # MISLEADING: underline the OTHER QA-approved (card-303), not the gold (card-301).
        Underline(44.0, 3.5, "#card-303", half_width=65.0),
        IdleDrift(47.5, 2.5, (900, 500)),
        # D03 (50-70s) — QA-approved + days>7 (2-of-3 distractor).
        MoveTo(50.0, 2.0, "#card-301"),
        MoveTo(52.0, 2.0, "#card-302"),
        # MISLEADING: circle on card-302 (9d, designer-review — fails the AND clause).
        Circle(54.0, 4.0, "#card-302", radius=40.0),
        MoveTo(58.0, 2.0, "#card-303"),
        IdleDrift(60.0, 4.0, (1100, 500)),
        MoveTo(64.0, 2.0, "#card-301"),
        IdleDrift(66.0, 4.0, (1000, 360)),
        # D04 (70-90s) — Backlog owner ALSO owns Done; tie-break highest effort.
        MoveTo(70.0, 2.0, "#card-101"),
        MoveTo(72.0, 1.8, "#card-102"),
        MoveTo(73.8, 1.8, "#card-103"),
        MoveTo(75.6, 1.8, "#card-104"),
        MoveTo(77.4, 2.0, "#col-done"),
        MoveTo(79.4, 1.8, "#card-501"),
        MoveTo(81.2, 1.8, "#card-502"),
        IdleDrift(83.0, 3.0, (800, 500)),
        # MISLEADING: underline card-103 — the v2 fewest-cards answer, but the
        # v3 owner-also-Done rule selects card-102 (R.Juarez has card-501).
        Underline(86.0, 4.0, "#card-103", half_width=60.0),
        MoveTo(90.0, 2.0, (800, 700)),
        IdleDrift(92.0, 3.0, (800, 700)),
    ]


# -------------------------------------------------------------------------
# Gold manifest. Each gold derived from applying the verbal policy to the
# visible board values.
#
# D01: In Progress efforts: card-201=5, card-203=21, card-202=20, card-204=3.
#      Highest = 21 → skip card-203. (card-202 at 20 is the close-call decoy.)
# D02: QA-approved cards = card-301 (R.Juarez), card-303 (S.Khan).
#      P0 card = card-203 (R.Juarez). Owner overlap = R.Juarez.
#      → approve card-301.
# D03: In Review days-in-status: card-301=2d, card-302=9d, card-303=8d.
#      QA-approved AND > 7d: card-302 has 9d but tag is "designer review"
#      (fails AND clause). card-303 has 8d AND QA-approved → block card-303.
# D04: Backlog owners: card-101 D.Tran, card-102 R.Juarez, card-103 M.Okafor,
#      card-104 S.Khan. Done owners: card-501 R.Juarez, card-502 S.Khan.
#      Eligible Backlog (owner-also-Done): card-102 (R.Juarez, 13pts),
#      card-104 (S.Khan, 3pts). Highest effort = 13 → assign card-102,
#      owner=Mina, due=2026-05-01.
# -------------------------------------------------------------------------
GOLD_DECISIONS = [
    {"decision_id": "D01", "item_id": "card-203", "decision": "skip",
     "owner": None, "due_date": None,
     "evidence_start": 12.0, "evidence_end": 32.0},
    {"decision_id": "D02", "item_id": "card-301", "decision": "approve",
     "owner": None, "due_date": None,
     "evidence_start": 32.0, "evidence_end": 48.0},
    {"decision_id": "D03", "item_id": "card-303", "decision": "block",
     "owner": None, "due_date": None,
     "evidence_start": 50.0, "evidence_end": 64.0},
    {"decision_id": "D04", "item_id": "card-102", "decision": "assign",
     "owner": "Mina", "due_date": "2026-05-01",
     "evidence_start": 70.0, "evidence_end": 88.0},
]


# -------------------------------------------------------------------------
# Playwright rendering.
# -------------------------------------------------------------------------

def render_dashboard():
    from playwright.sync_api import sync_playwright

    html_path = ROOT / "dashboard.html"
    bg_png = WORK / "dashboard.png"

    with sync_playwright() as p:
        browser = p.chromium.launch(args=["--no-sandbox"])
        ctx = browser.new_context(viewport={"width": WIDTH, "height": HEIGHT}, device_scale_factor=1)
        page = ctx.new_page()
        page.goto(html_path.as_uri())
        page.wait_for_load_state("networkidle")
        page.screenshot(path=str(bg_png), full_page=False)
        bboxes = page.evaluate(
            """
            () => {
              const out = {};
              for (const el of document.querySelectorAll('[data-id]')) {
                const r = el.getBoundingClientRect();
                if (r.width === 0 || r.height === 0) continue;
                out[el.getAttribute('data-id')] = {
                  x: r.x, y: r.y, w: r.width, h: r.height,
                  text: (el.innerText || '').trim().split('\\n')[0]
                };
              }
              return out;
            }
            """
        )
        browser.close()
    (WORK / "elements.json").write_text(json.dumps(bboxes, indent=2))
    print(f"Rendered dashboard: {bg_png} ({len(bboxes)} elements)", flush=True)
    return bg_png, bboxes


# -------------------------------------------------------------------------
# Audio (Kokoro-82M).
# -------------------------------------------------------------------------

def _kokoro_synth(pipeline, voice, text):
    chunks = []
    for _gs, _ps, audio in pipeline(text, voice=voice):
        chunks.append(audio)
    return np.concatenate(chunks).astype(np.float32)


def build_audio() -> Path:
    import time
    print("Loading Kokoro pipeline (American)…", flush=True)
    from kokoro import KPipeline
    pipeline = KPipeline(lang_code="a")
    print("  ready.", flush=True)

    pieces: list[np.ndarray] = []
    cursor_at = 0.0
    print(f"Synthesizing {len(NARRATION)} turns with Kokoro …", flush=True)
    t0 = time.time()
    for i, (target_start, text) in enumerate(NARRATION):
        gap = max(0.0, target_start - cursor_at)
        if gap > 0:
            pieces.append(np.zeros(int(gap * SAMPLE_RATE), dtype=np.float32))
            cursor_at += gap
        ts = time.time()
        wav_np = _kokoro_synth(pipeline, "am_michael", text)
        if wav_np.shape[0] == 0:
            continue
        pieces.append(wav_np)
        cursor_at += wav_np.shape[0] / SAMPLE_RATE
        synth_t = time.time() - ts
        print(
            f"  [{i:02d}] t≈{cursor_at - wav_np.shape[0] / SAMPLE_RATE:6.2f}-{cursor_at:6.2f} "
            f"audio={wav_np.shape[0] / SAMPLE_RATE:5.2f}s synth={synth_t:5.1f}s "
            f"wall={time.time() - t0:6.1f}s",
            flush=True,
        )
    pad_to = max(cursor_at + 1.5, 95.0)
    pieces.append(np.zeros(int((pad_to - cursor_at) * SAMPLE_RATE), dtype=np.float32))
    full = np.concatenate(pieces)
    wav_path = WORK / "narration.wav"
    sf.write(str(wav_path), full, SAMPLE_RATE)
    print(f"WAV: {full.shape[0] / SAMPLE_RATE:.2f}s ({wav_path.stat().st_size / 1e6:.1f} MB)", flush=True)
    return wav_path


# -------------------------------------------------------------------------
# Cursor + frame rendering.
# -------------------------------------------------------------------------

def resolve_target(spec, bboxes):
    if spec is None:
        return None
    if isinstance(spec, tuple):
        return (float(spec[0]), float(spec[1]))
    if isinstance(spec, str) and spec.startswith("#"):
        bb = bboxes[spec[1:]]
        return (float(bb["x"] + bb["w"] / 2), float(bb["y"] + bb["h"] / 2))
    raise ValueError(spec)


def precompute_motion(events: list[Motion], bboxes, init_pos=(800.0, 30.0)) -> None:
    pos = init_pos
    for ev in events:
        ev.start_pos = pos
        if ev.target is None:
            ev.target_pos = pos
        else:
            ev.target_pos = resolve_target(ev.target, bboxes)
        ev.finalize_end_pos()
        pos = ev.end_pos


def cursor_at(t: float, events: list[Motion]) -> tuple[int, int]:
    # Find active event. If between events, use the previous event's end_pos.
    base = events[0].start_pos
    for i, ev in enumerate(events):
        if t < ev.t_start:
            base = events[i - 1].end_pos if i > 0 else ev.start_pos
            break
        if ev.t_start <= t <= ev.t_end:
            base = ev.resolve(t)
            break
        if i == len(events) - 1 and t > ev.t_end:
            base = ev.end_pos
    rng = random.Random(int(t * 1000))
    return (int(base[0] + rng.gauss(0.0, 1.6)), int(base[1] + rng.gauss(0.0, 1.6)))


def hover_target(x, y, bboxes):
    cands = []
    for name, bb in bboxes.items():
        if bb["x"] <= x <= bb["x"] + bb["w"] and bb["y"] <= y <= bb["y"] + bb["h"]:
            if bb["w"] * bb["h"] > 700 * 700:
                continue
            cands.append((bb["w"] * bb["h"], name))
    if not cands:
        return "none"
    cands.sort()
    return cands[0][1]


def _font(size, mono=False):
    cand = ["/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"] if mono else \
           ["/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"]
    for c in cand:
        if Path(c).exists():
            return ImageFont.truetype(c, size)
    return ImageFont.load_default()


def render_cursor():
    img = Image.new("RGBA", (24, 30), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    pts = [(2, 2), (2, 22), (8, 16), (12, 26), (15, 25), (10, 14), (18, 14)]
    d.polygon(pts, fill=(255, 255, 255, 255), outline=(0, 0, 0, 255))
    return img


def render_video(audio_wav, bg_png, bboxes, events: list[Motion]):
    bg_master = Image.open(bg_png).convert("RGB")
    cursor = render_cursor()
    with wave.open(str(audio_wav), "rb") as w:
        total_sec = w.getnframes() / w.getframerate()
    n_frames = int(math.ceil(total_sec * FPS))
    print(f"Video: {n_frames} frames @ {FPS}fps for {total_sec:.2f}s", flush=True)

    frames_dir = WORK / "frames"
    if frames_dir.exists():
        shutil.rmtree(frames_dir)
    frames_dir.mkdir()

    footer_bb = bboxes["footer-status"]
    fy = int(footer_bb["y"])
    f_font = _font(11, mono=True)

    for i in range(n_frames):
        t = i / FPS
        x, y = cursor_at(t, events)
        hov_id = hover_target(x, y, bboxes)

        frame = bg_master.copy()
        d = ImageDraw.Draw(frame)

        # Footer band (live hovered:).
        d.rectangle([0, fy, WIDTH, fy + int(footer_bb["h"])], fill=(30, 41, 59))
        ftx = 14
        fty = fy + 9
        d.text((ftx, fty), "[ inspector ]", fill=(163, 230, 53), font=f_font)
        ftx += f_font.getlength("[ inspector ]") + 14
        d.text((ftx, fty), "hovered: ", fill=(148, 163, 184), font=f_font)
        ftx += f_font.getlength("hovered: ")
        d.text((ftx, fty), hov_id, fill=(203, 213, 225), font=f_font)
        right = "sprint Q3-S2 · build 2026.04.18-r3 · session #f81d"
        rw = f_font.getlength(right)
        d.text((WIDTH - rw - 14, fty), right, fill=(100, 116, 139), font=f_font)

        # Cursor (last so it's on top).
        frame.paste(cursor, (x - 2, y - 2), cursor)

        frame.save(frames_dir / f"f_{i:06d}.png", optimize=False, compress_level=1)
        if i % 300 == 0:
            print(f"  rendered frame {i}/{n_frames}", flush=True)

    out = ASSETS / "recording.webm"
    cmd = [
        "ffmpeg", "-y",
        "-framerate", str(FPS),
        "-i", str(frames_dir / "f_%06d.png"),
        "-i", str(audio_wav),
        "-c:v", "libvpx-vp9", "-crf", "32", "-b:v", "0",
        "-pix_fmt", "yuv420p",
        "-c:a", "libopus", "-b:a", "96k",
        "-shortest", "-deadline", "good", "-cpu-used", "4",
        str(out),
    ]
    subprocess.run(cmd, check=True)
    return out


def main():
    bg_png, bboxes = render_dashboard()
    for d in GOLD_DECISIONS:
        if d["item_id"] not in bboxes:
            raise RuntimeError(f"gold item {d['item_id']!r} missing from rendered DOM")

    events = make_events()
    precompute_motion(events, bboxes)
    # Sanity-print motion timeline for review.
    print("Motion timeline:", flush=True)
    for ev in events:
        kind = ev.__class__.__name__
        tgt = ev.target if ev.target is not None else "(drift)"
        print(f"  {ev.t_start:6.2f}-{ev.t_end:6.2f}  {kind:10s}  -> {tgt}", flush=True)

    audio_wav = build_audio()

    (WORK / "recording_gold.json").write_text(json.dumps(GOLD_DECISIONS, indent=2) + "\n")
    print(f"Gold: {len(GOLD_DECISIONS)} decisions", flush=True)
    for g in GOLD_DECISIONS:
        print(f"  {g['decision_id']}: {g['decision']:7s} {g['item_id']}  "
              f"evidence=[{g['evidence_start']},{g['evidence_end']}]")

    out = render_video(audio_wav, bg_png, bboxes, events)
    print(f"Wrote {out} ({out.stat().st_size / 1e6:.1f} MB)", flush=True)


if __name__ == "__main__":
    main()
