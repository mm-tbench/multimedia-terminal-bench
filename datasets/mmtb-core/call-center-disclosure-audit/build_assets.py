#!/usr/bin/env python3
"""Build the call-center-disclosure-audit screencast (v3 — 6 checks + motion).

Produces ./environment/assets/recording.webm — a ~105 s screen-recording of a
support representative working through one customer call. The agent must
audit six compliance checks (3 PASS + 3 FAIL).

  CK01  disclose-recording        PASS
  CK02  verify-tier-statement     PASS  (rep says "enterprise tier" and UI
                                          row-tier shows "enterprise")
  CK03  account-status-claim      FAIL  claim-contradicts-state — rep says
                                          "everything is in good standing"
                                          while the UI shows "PAYMENT FAILED"
                                          on the active customer.
  CK04  verify-email-statement    PASS  (rep states a primary email and the
                                          UI shows the same address)
  CK05  primary-phone-claim       FAIL  wrong-phone-quoted — rep says
                                          "five-five-five oh-one-two-four"
                                          while UI shows 555-0142 (digit
                                          transposition).
  CK06  refund-amount-claim       FAIL  wrong-amount-quoted — rep says
                                          "$1,247.00" while the UI shows
                                          $1,742.00 (digit transposition).

v3 vs v2:
  - 6 checks (was 3); 3 PASS + 3 FAIL forces the agent to actually
    cross-check verbal claims against visible state instead of defaulting
    to FAIL.
  - Tighter ±1.0s evidence tolerance (was ±2.0s).
  - Realistic cursor motion library: MoveTo / Rest / Circle / Underline /
    IdleDrift. Speaker idles, circles around fields, underlines values they
    read aloud, drifts during pauses.

Audio: Kokoro-82M two-voice (am_michael for rep, af_heart for customer).

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
SAMPLE_RATE = 24_000

# Two-speaker turns. Customer turns get a slight pitch shift to differ from rep.
NARRATION = [
    ("rep",  0.5,  "Acme Support, this is Daniel. Thanks for holding."),
    # CK01 disclosure (audio evidence ~6.5s).
    ("rep",  4.0,  "Before we get started, I do need to let you know this call is being recorded for quality assurance."),
    ("cust", 12.0, "Yes, that's fine."),
    ("rep",  14.5, "Thank you. Let me pull up your account here."),
    # Click James Patel customer row at ~17s.
    # CK02 verify-tier-statement: rep correctly states "enterprise tier" (PASS).
    ("rep",  18.5, "OK, I have your record open. James Patel, enterprise tier — that's our highest service level."),
    # CK03 claim-contradicts-state: rep claims "good standing" — UI shows PAYMENT FAILED.
    ("rep",  25.0, "Quick read on your account here — everything is in good standing, payments are all current as of the most recent billing cycle."),
    ("cust", 33.5, "Oh, that's a relief to hear."),
    # CK04 verify-email-statement: rep correctly reads the email (PASS).
    ("rep",  36.0, "Just to confirm we have your contact details right — the primary email I'm showing is james dot patel at boreal-foods dot example."),
    ("cust", 45.5, "Yes, that's the one."),
    # CK05 primary-phone-claim: rep mis-states phone number as 555-0124, UI shows 555-0142 (FAIL).
    ("rep",  48.0, "Great. And the best callback number for you is five-five-five, oh-one-two-four — is that still right?"),
    ("cust", 56.5, "Yeah, that's the right number."),
    ("rep",  59.5, "Perfect. Now let me pull up the refund I see on your account."),
    # Click "Issue refund" at ~67s.
    ("rep",  64.5, "OK, authorizing this refund per your enterprise tier service-level agreement."),
    # CK06 wrong-amount-quoted: rep says $1,247, UI shows $1,742 (FAIL).
    ("rep",  70.0, "Processing the refund of one thousand two hundred and forty-seven dollars now."),
    ("rep",  77.0, "Done. The refund should land in two to three business days."),
    ("cust", 82.5, "Great, thank you for your help."),
    ("rep",  85.0, "Of course. Anything else I can help you with today?"),
    ("cust", 89.0, "No, that's all."),
    ("rep",  90.5, "Have a great day. Thanks for calling Acme."),
]

# Click events: (time, target_id) — paint a click ring at this time.
CLICKS = [
    (17.0, "cust-row-jptl"),  # CK01 UI action: click James Patel customer row
    (67.0, "btn-refund"),     # CK06 UI action: click Issue refund
]


# -------------------------------------------------------------------------
# Cursor motion library. Each Motion subclass renders an absolute (x,y)
# at a given time t in [t_start, t_end].
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
    radius: float = 36.0
    cycles: float = 1.25

    def __init__(self, t_start, duration, target, radius=36.0, cycles=1.25):
        super().__init__(t_start, duration, target)
        self.radius = radius
        self.cycles = cycles

    def resolve(self, t):
        local = self._local(t)
        env = math.sin(math.pi * local)
        r = self.radius * env
        omega = 2 * math.pi * self.cycles
        x = self.target_pos[0] + r * math.cos(omega * local)
        y = self.target_pos[1] + r * math.sin(omega * local)
        return (x, y)

    def finalize_end_pos(self):
        self.end_pos = self.target_pos


class Underline(Motion):
    """Sweep horizontally below target: left → right → left."""
    half_width: float = 80.0
    y_offset: float = 16.0

    def __init__(self, t_start, duration, target, half_width=80.0, y_offset=16.0):
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
        self.end_pos = self.resolve(self.t_end)


def make_events() -> list[Motion]:
    return [
        # Intro (0-13s).
        Rest(0.0, 2.0, (1300, 30)),
        IdleDrift(2.0, 11.0, (300, 250)),
        # CK01: click James Patel at 17s.
        MoveTo(13.0, 3.0, "#cust-row-jptl"),
        Rest(16.0, 2.5, "#cust-row-jptl"),
        # CK02 verify-tier: cursor on row-tier ~21-22s (PASS verbal+visual agree).
        MoveTo(18.5, 2.0, "#row-tier"),
        Circle(20.5, 3.0, "#row-tier", radius=28.0),
        # CK03 claim-mismatch: cursor near row-acct-status ~28-31s.
        MoveTo(23.5, 2.5, "#row-acct-status"),
        Rest(26.0, 5.0, "#row-acct-status"),
        IdleDrift(31.0, 4.5, (700, 480)),
        # CK04 verify-email: cursor on row-email ~40-44s, underline (rep reads aloud).
        MoveTo(35.5, 2.0, "#row-email"),
        Underline(37.5, 6.0, "#row-email", half_width=110.0, y_offset=14.0),
        IdleDrift(43.5, 4.0, (700, 320)),
        # CK05 verify-phone (FAIL): cursor on row-phone ~52-58s, circle (rep emphasizes).
        MoveTo(47.5, 2.5, "#row-phone"),
        Circle(50.0, 7.5, "#row-phone", radius=32.0),
        IdleDrift(57.5, 4.5, (700, 380)),
        # btn-refund click at 67s.
        MoveTo(62.0, 4.5, "#btn-refund"),
        Rest(66.5, 2.5, "#btn-refund"),
        # CK06 wrong-amount (FAIL): cursor on row-open-refund ~73-78s, underline.
        MoveTo(69.0, 2.5, "#row-open-refund"),
        Underline(71.5, 7.0, "#row-open-refund", half_width=80.0, y_offset=14.0),
        IdleDrift(78.5, 7.0, (1000, 420)),
        # Outro.
        MoveTo(85.5, 4.0, (1400, 200)),
        IdleDrift(89.5, 15.0, (1400, 200)),
    ]


# Gold compliance manifest (v3 — 6 checks).
GOLD_CHECKS = [
    {"check_id": "CK01", "pass_fail": "PASS",
     "failure_reason": "",
     "audio_evidence_time": 6.5,
     "ui_evidence_time":    17.0},
    {"check_id": "CK02", "pass_fail": "PASS",
     "failure_reason": "",
     "audio_evidence_time": 21.5,   # "enterprise tier"
     "ui_evidence_time":    21.5},  # row-tier visible (cursor on it)
    {"check_id": "CK03", "pass_fail": "FAIL",
     "failure_reason": "claim-contradicts-state",
     "audio_evidence_time": 29.0,   # "good standing"
     "ui_evidence_time":    29.0},  # PAYMENT FAILED visible
    {"check_id": "CK04", "pass_fail": "PASS",
     "failure_reason": "",
     "audio_evidence_time": 41.5,   # email read
     "ui_evidence_time":    41.5},  # row-email visible (cursor under it)
    {"check_id": "CK05", "pass_fail": "FAIL",
     "failure_reason": "wrong-phone-quoted",
     "audio_evidence_time": 53.0,   # "555-0124" stated
     "ui_evidence_time":    53.0},  # row-phone shows 555-0142
    {"check_id": "CK06", "pass_fail": "FAIL",
     "failure_reason": "wrong-amount-quoted",
     "audio_evidence_time": 73.5,   # "one thousand two hundred and forty-seven"
     "ui_evidence_time":    73.5},  # $1,742.00 visible (cursor on it)
]


# ---------------------------------------------------------------------------
# Playwright rendering.
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Audio (Kokoro-82M).
# ---------------------------------------------------------------------------

def _resample(wav, sr_in, sr_out):
    if sr_in == sr_out:
        return wav
    n = int(wav.shape[0] * sr_out / sr_in)
    return np.interp(
        np.linspace(0, wav.shape[0] - 1, n).astype(np.float32),
        np.arange(wav.shape[0], dtype=np.float32),
        wav,
    ).astype(np.float32)


def _pitch_shift_resample(wav, sr, factor=1.10):
    n = int(wav.shape[0] / factor)
    shifted = np.interp(
        np.linspace(0, wav.shape[0] - 1, n).astype(np.float32),
        np.arange(wav.shape[0], dtype=np.float32),
        wav,
    ).astype(np.float32)
    return _resample(shifted, sr, int(sr / factor))


SPEAKER_KOKORO_VOICES = {"rep": "am_michael", "cust": "af_heart"}


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
    for i, (speaker, target_start, text) in enumerate(NARRATION):
        gap = max(0.0, target_start - cursor_at)
        if gap > 0:
            pieces.append(np.zeros(int(gap * SAMPLE_RATE), dtype=np.float32))
            cursor_at += gap
        ts = time.time()
        wav_np = _kokoro_synth(pipeline, SPEAKER_KOKORO_VOICES[speaker], text)
        if wav_np.shape[0] == 0:
            continue
        if speaker == "cust":
            wav_np = _pitch_shift_resample(wav_np, SAMPLE_RATE, factor=1.10)
        pieces.append(wav_np)
        cursor_at += wav_np.shape[0] / SAMPLE_RATE
        synth_t = time.time() - ts
        print(
            f"  [{i:02d}] {speaker:4s} t≈{cursor_at - wav_np.shape[0] / SAMPLE_RATE:6.2f}-{cursor_at:6.2f} "
            f"audio={wav_np.shape[0] / SAMPLE_RATE:5.2f}s synth={synth_t:5.1f}s wall={time.time() - t0:6.1f}s",
            flush=True,
        )
    pad_to = max(cursor_at + 1.0, 105.0)
    pieces.append(np.zeros(int((pad_to - cursor_at) * SAMPLE_RATE), dtype=np.float32))
    full = np.concatenate(pieces)
    wav_path = WORK / "narration.wav"
    sf.write(str(wav_path), full, SAMPLE_RATE)
    print(f"WAV: {full.shape[0] / SAMPLE_RATE:.2f}s", flush=True)
    return wav_path


# ---------------------------------------------------------------------------
# Cursor + frame rendering.
# ---------------------------------------------------------------------------

def resolve_target(spec, bboxes):
    if spec is None:
        return None
    if isinstance(spec, tuple):
        return (float(spec[0]), float(spec[1]))
    if isinstance(spec, str) and spec.startswith("#"):
        bb = bboxes[spec[1:]]
        return (float(bb["x"] + bb["w"] / 2), float(bb["y"] + bb["h"] / 2))
    raise ValueError(spec)


def precompute_motion(events: list[Motion], bboxes, init_pos=(1300.0, 30.0)) -> None:
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


def click_ring_progress(t):
    for ct, _ in CLICKS:
        if ct - 0.05 <= t <= ct + 0.7:
            return (t - ct + 0.05) / 0.75
    return None


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

        progress = click_ring_progress(t)
        if progress is not None and 0 <= progress <= 1:
            r = int(8 + 30 * progress)
            alpha = int(220 * (1 - progress))
            for off in range(2):
                d.ellipse(
                    [x - r - off, y - r - off, x + r + off, y + r + off],
                    outline=(37, 99, 235, alpha - off * 30),
                    width=1,
                )

        d.rectangle([0, fy, WIDTH, fy + int(footer_bb["h"])], fill=(30, 41, 59))
        ftx = 14
        fty = fy + 9
        d.text((ftx, fty), "[ inspector ]", fill=(163, 230, 53), font=f_font)
        ftx += f_font.getlength("[ inspector ]") + 14
        d.text((ftx, fty), "hovered: ", fill=(148, 163, 184), font=f_font)
        ftx += f_font.getlength("hovered: ")
        d.text((ftx, fty), hov_id, fill=(203, 213, 225), font=f_font)
        right = "workstation WS-274 · session #f81d"
        rw = f_font.getlength(right)
        d.text((WIDTH - rw - 14, fty), right, fill=(100, 116, 139), font=f_font)

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

    events = make_events()
    precompute_motion(events, bboxes)
    print("Motion timeline:", flush=True)
    for ev in events:
        kind = ev.__class__.__name__
        tgt = ev.target if ev.target is not None else "(drift)"
        print(f"  {ev.t_start:6.2f}-{ev.t_end:6.2f}  {kind:10s}  -> {tgt}", flush=True)

    audio_wav = build_audio()

    (WORK / "recording_gold.json").write_text(json.dumps(GOLD_CHECKS, indent=2) + "\n")
    print(f"Gold: {len(GOLD_CHECKS)} compliance checks", flush=True)
    for g in GOLD_CHECKS:
        ui = g["ui_evidence_time"] if g["ui_evidence_time"] is not None else "—"
        print(f"  {g['check_id']}: {g['pass_fail']:4s}  audio={g['audio_evidence_time']}  ui={ui}  reason={g['failure_reason']!r}")

    out = render_video(audio_wav, bg_png, bboxes, events)
    print(f"Wrote {out} ({out.stat().st_size / 1e6:.1f} MB)", flush=True)


if __name__ == "__main__":
    main()
