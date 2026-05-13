#!/usr/bin/env python3
"""Build the deictic-ui-reference screencast deterministically.

Produces ./environment/assets/recording.webm (the agent-facing input).
A side-car .build/recording_gold.json is also written for build-side debugging
but is NOT shipped — the gold values live only in tests/verifier.py.

Pipeline:
  1. Render dashboard.html with headless Chromium (Playwright) at 1440x810,
     capture a single PNG screenshot, and pull bounding boxes for every
     element with a data-id attribute.
  2. Synthesize 9 narration utterances with Piper (en_US-lessac-medium, MIT)
     onto a fixed timeline (silence-padded).
  3. Render 30fps PIL frames over the screenshot: cursor sprite at the
     cursor's interpolated position; the dashboard footer's "hovered:" and
     "text:" labels are repainted live based on which element the cursor
     is over (resolved from the bbox map).
  4. Encode video (libvpx-vp9) and mux audio (libopus) to webm.

Run:  uv run --with piper-tts --with pillow --with playwright python build_assets.py
"""

from __future__ import annotations

import json
import math
import shutil
import subprocess
import wave
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).parent
ASSETS = ROOT / "environment" / "assets"
WORK = ROOT / ".build"
ASSETS.mkdir(parents=True, exist_ok=True)
WORK.mkdir(parents=True, exist_ok=True)

WIDTH, HEIGHT = 1440, 810
FPS = 30
SAMPLE_RATE = 22050  # Piper native

VOICE = "en_US-lessac-medium"
VOICE_DIR = Path.home() / ".local" / "share" / "piper" / "voices"

# Gold target — the cell the reviewer's VERBAL description identifies as the
# bug, not the cell the cursor happens to be on at the gold phrase moment.
# At gold-phrase time the cursor sits on a DECOY (r6-ord); the gold cell is
# only identifiable by joint audio-visual reasoning (verbal attribute cues +
# table contents).
GOLD_ELEMENT_ID = "r4-ord"
GOLD_VISIBLE_TEXT = "ORD-2O26-O8I1IL-R0"


# ---------------------------------------------------------------------------
# Playwright rendering: dashboard screenshot + element bbox map
# ---------------------------------------------------------------------------


def render_dashboard() -> tuple[Path, dict[str, dict[str, float]]]:
    """Render dashboard.html, screenshot it, and return (png_path, bbox_map)."""
    from playwright.sync_api import sync_playwright

    html_path = ROOT / "dashboard.html"
    bg_png = WORK / "dashboard.png"
    bbox_json = WORK / "elements.json"

    with sync_playwright() as p:
        browser = p.chromium.launch(args=["--no-sandbox"])
        ctx = browser.new_context(
            viewport={"width": WIDTH, "height": HEIGHT}, device_scale_factor=1
        )
        page = ctx.new_page()
        page.goto(html_path.as_uri())
        page.wait_for_load_state("networkidle")
        page.screenshot(path=str(bg_png), full_page=False, omit_background=False)
        bboxes = page.evaluate(
            """
            () => {
              const out = {};
              for (const el of document.querySelectorAll('[data-id]')) {
                const r = el.getBoundingClientRect();
                if (r.width === 0 || r.height === 0) continue;
                out[el.getAttribute('data-id')] = {
                  x: r.x, y: r.y, w: r.width, h: r.height,
                  text: (el.innerText || '').trim()
                };
              }
              return out;
            }
            """
        )
        browser.close()
    bbox_json.write_text(json.dumps(bboxes, indent=2))
    print(f"Rendered dashboard: {bg_png} ({len(bboxes)} elements)")
    if GOLD_ELEMENT_ID not in bboxes:
        raise RuntimeError(
            f"Gold element {GOLD_ELEMENT_ID!r} missing from rendered DOM"
        )
    if bboxes[GOLD_ELEMENT_ID]["text"] != GOLD_VISIBLE_TEXT:
        raise RuntimeError(
            f"Gold text mismatch: dashboard.html shows "
            f"{bboxes[GOLD_ELEMENT_ID]['text']!r}, build expects {GOLD_VISIBLE_TEXT!r}"
        )
    return bg_png, bboxes


# ---------------------------------------------------------------------------
# Audio: TTS + silence-padded timeline
# ---------------------------------------------------------------------------

# Each entry: (start_sec_target, text, settle — element data-id or "_pos:x,y")
#
# Joint-audio-visual design: the cursor at the *gold phrase moment* sits on a
# DECOY cell (r6-ord, an unrelated In-Review row's order ID). The reviewer
# then immediately self-corrects in the next utterance, naming the gold row
# by attributes only — "the escalated row above" and "Mariana's row". A
# cursor-only strategy reads the decoy and fails on element_id + visible_text;
# a joint reasoner combines the verbal correction with table contents to
# identify row 4 (the only Escalated row, customer name with apostrophe) and
# read its order-ID cell visually to recover ORD-2026-B7E14D-R2.
NARRATION = [
    (
        0.5,
        "Walking through the refund audit queue. Eight pending rows. A few are clean and one is broken.",
        "_pos:720,30",
    ),
    (
        9.0,
        "Henderson up top and Cassidy below it look fine — those refunds are tracking normally.",
        "r1-cust",
    ),
    (
        17.0,
        "There are two rows on this view marked escalated this week, and one of them — only one — is the actual root-cause row.",
        "_pos:760,420",
    ),
    (
        28.0,
        "I want the escalated row whose customer-name field carries that little curl mark — the apostrophe between the first and last name. Not the other escalated row, the one without the apostrophe.",
        "_pos:760,440",
    ),
    (
        43.0,
        "More precisely, it is the order-ID column on that row — not the email, not the amount — where our identifier generator dropped a digit.",
        "_pos:760,460",
    ),
    (
        53.0,
        "Let me hover over a couple of other rows as I think out loud. The disputed badge over here.",
        "r3-status",
    ),
    (
        59.0,
        "And the in-review row further down has the same generator hash but is otherwise normal.",
        "r6-status",
    ),
    # GOLD PHRASE: cursor lands on a DECOY (r6-ord). True gold (r4-ord)
    # has been described verbally in turns 2 and 3.
    (
        63.5,
        "Okay — this one right here is the problem.",
        "r6-ord",
    ),
    # CORRECTION: reviewer admits the cursor is on the wrong cell and names
    # the gold row by attributes (no spelling-out of the email address).
    (
        67.5,
        "Wait, my cursor is on the wrong cell — I mean the escalated row, with Mariana's name. The order-ID column on that row.",
        "_pos:560,300",
    ),
    (
        78.0,
        "Patch that generator and the rest unwind on their own.",
        "stat-queued",
    ),
]


def piper_synth(text: str, out_wav: Path):
    model = VOICE_DIR / f"{VOICE}.onnx"
    if not model.exists():
        raise RuntimeError(f"Piper voice not cached at {model}")
    subprocess.run(
        ["piper", "--model", str(model), "--output_file", str(out_wav)],
        input=text.encode(),
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def silence_wav(duration_sec: float, sample_rate: int, out: Path):
    n = int(duration_sec * sample_rate)
    with wave.open(str(out), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(b"\x00\x00" * n)


def concat_wavs(wavs: list[Path], out: Path):
    with wave.open(str(out), "wb") as wo:
        for i, p in enumerate(wavs):
            with wave.open(str(p), "rb") as wi:
                if i == 0:
                    wo.setnchannels(wi.getnchannels())
                    wo.setsampwidth(wi.getsampwidth())
                    wo.setframerate(wi.getframerate())
                wo.writeframes(wi.readframes(wi.getnframes()))


def resample_to(in_wav: Path, target_sr: int, out_wav: Path):
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(in_wav),
            "-ar",
            str(target_sr),
            "-ac",
            "1",
            str(out_wav),
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def build_audio() -> tuple[Path, list[dict]]:
    pieces: list[Path] = []
    segments = []
    cursor_at = 0.0
    for i, (target_start, text, settle) in enumerate(NARRATION):
        gap = max(0.0, target_start - cursor_at)
        if gap > 0:
            sil = WORK / f"sil_{i:02d}.wav"
            silence_wav(gap, SAMPLE_RATE, sil)
            pieces.append(sil)
            cursor_at += gap
        raw = WORK / f"raw_{i:02d}.wav"
        utt = WORK / f"utt_{i:02d}.wav"
        piper_synth(text, raw)
        resample_to(raw, SAMPLE_RATE, utt)
        with wave.open(str(utt), "rb") as w:
            dur = w.getnframes() / w.getframerate()
        segments.append(
            {
                "i": i,
                "text": text,
                "settle": settle,
                "start": cursor_at,
                "end": cursor_at + dur,
            }
        )
        pieces.append(utt)
        cursor_at += dur
    pad_to = max(cursor_at + 1.5, 84.0)
    sil = WORK / "sil_tail.wav"
    silence_wav(pad_to - cursor_at, SAMPLE_RATE, sil)
    pieces.append(sil)
    final = WORK / "narration_full.wav"
    concat_wavs(pieces, final)
    with wave.open(str(final), "rb") as w:
        total = w.getnframes() / w.getframerate()
    print(f"Audio: {len(NARRATION)} utterances, total {total:.2f}s")
    for s in segments:
        print(
            f"  [{s['i']}] {s['start']:6.2f}-{s['end']:6.2f}  settle={s['settle']:18}  {s['text'][:60]}"
        )
    return final, segments


# ---------------------------------------------------------------------------
# Video: cursor + live footer overlay
# ---------------------------------------------------------------------------


def _font(size: int, mono: bool = False) -> ImageFont.FreeTypeFont:
    cands_prop = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/TTF/DejaVuSans.ttf",
    ]
    cands_mono = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
        "/usr/share/fonts/TTF/DejaVuSansMono.ttf",
    ]
    for cand in cands_mono if mono else cands_prop:
        if Path(cand).exists():
            return ImageFont.truetype(cand, size)
    return ImageFont.load_default()


def render_cursor() -> Image.Image:
    """Standard arrow cursor sprite, white with black outline."""
    img = Image.new("RGBA", (24, 30), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    pts = [(2, 2), (2, 22), (8, 16), (12, 26), (15, 25), (10, 14), (18, 14)]
    d.polygon(pts, fill=(255, 255, 255, 255), outline=(0, 0, 0, 255))
    return img


def settle_xy(name: str, bboxes: dict, gold_xy: tuple[int, int]) -> tuple[int, int]:
    if name.startswith("_pos:"):
        x, y = name[5:].split(",")
        return (int(x), int(y))
    if name == "GOLD":
        return gold_xy
    bb = bboxes[name]
    # When the cursor is on the decoy ord cell at gold-phrase time, place it
    # right over the cell text (not in whitespace) so the inspector footer
    # reads cleanly — but the gold cell is in a different row entirely.
    if name == "r6-ord":
        return (int(bb["x"] + bb["w"] * 0.55), int(bb["y"] + bb["h"] / 2))
    return (int(bb["x"] + bb["w"] / 2), int(bb["y"] + bb["h"] / 2))


def cursor_keyframes(
    segments: list[dict], bboxes: dict
) -> list[tuple[float, tuple[int, int]]]:
    gold_bb = bboxes[GOLD_ELEMENT_ID]
    gold_xy = (
        int(gold_bb["x"] + gold_bb["w"] / 2),
        int(gold_bb["y"] + gold_bb["h"] / 2),
    )
    kf: list[tuple[float, tuple[int, int]]] = [(0.0, (WIDTH // 2, 30))]
    for s in segments:
        target = settle_xy(s["settle"], bboxes, gold_xy)
        kf.append((s["start"] + (s["end"] - s["start"]) * 0.5, target))
        kf.append((s["end"], target))
    kf.sort(key=lambda x: x[0])
    return kf


def _smoothstep(a: float) -> float:
    return a * a * (3 - 2 * a)


def cursor_at(t: float, kf) -> tuple[int, int]:
    """Naturalistic cursor: smoothstep ease-in-out + small Gaussian jitter."""
    import random

    if t <= kf[0][0]:
        base = kf[0][1]
    elif t >= kf[-1][0]:
        base = kf[-1][1]
    else:
        base = kf[-1][1]
        for (t0, p0), (t1, p1) in zip(kf[:-1], kf[1:]):
            if t0 <= t <= t1:
                if t1 == t0:
                    base = p1
                else:
                    a = (t - t0) / (t1 - t0)
                    a_e = _smoothstep(a)
                    base = (
                        p0[0] + a_e * (p1[0] - p0[0]),
                        p0[1] + a_e * (p1[1] - p0[1]),
                    )
                break
    # Deterministic per-frame jitter — simulates hand tremor.
    rng = random.Random(int(t * 1000))
    return (
        int(base[0] + rng.gauss(0.0, 2.5)),
        int(base[1] + rng.gauss(0.0, 2.5)),
    )


def hover_target(x: int, y: int, bboxes: dict) -> tuple[str, str]:
    """Return (data-id, text) of the smallest bbox containing (x, y), else ('none', '—')."""
    candidates = []
    for name, bb in bboxes.items():
        if bb["x"] <= x <= bb["x"] + bb["w"] and bb["y"] <= y <= bb["y"] + bb["h"]:
            # Skip very large containers (header, sidebar, panels) so we report the leaf element.
            if bb["w"] * bb["h"] > 400 * 400:
                continue
            candidates.append((bb["w"] * bb["h"], name, bb["text"]))
    if not candidates:
        return ("none", "—")
    candidates.sort()
    _, name, text = candidates[0]
    # Truncate text for footer display.
    if len(text) > 60:
        text = text[:60] + "…"
    return (name, text)


def find_footer_segments(bboxes: dict) -> dict:
    """Pick the rectangles to repaint each frame for the live footer text."""
    return {
        "footer": bboxes["footer-status"],
        # Repaint the entire footer line region — coordinates relative to footer.
    }


def render_video(
    audio_wav: Path, segments: list[dict], bg_png: Path, bboxes: dict
) -> Path:
    bg_master = Image.open(bg_png).convert("RGB")
    cursor = render_cursor()
    with wave.open(str(audio_wav), "rb") as w:
        total_sec = w.getnframes() / w.getframerate()
    n_frames = int(math.ceil(total_sec * FPS))
    print(f"Video: {n_frames} frames @ {FPS}fps for {total_sec:.2f}s")

    kf = cursor_keyframes(segments, bboxes)

    frames_dir = WORK / "frames"
    if frames_dir.exists():
        shutil.rmtree(frames_dir)
    frames_dir.mkdir()

    footer_bb = bboxes["footer-status"]
    fy = int(footer_bb["y"])
    # Small monospace footer — element_id only, no visible-text leak.
    f_font = _font(10, mono=True)

    for i in range(n_frames):
        t = i / FPS
        x, y = cursor_at(t, kf)
        hov_id, _ = hover_target(x, y, bboxes)

        frame = bg_master.copy()
        d = ImageDraw.Draw(frame)
        # Repaint footer band so the "hovered:" segment updates each frame.
        d.rectangle([0, fy, WIDTH, fy + int(footer_bb["h"])], fill=(30, 41, 59))
        ftx = 14
        fty = fy + 9
        d.text((ftx, fty), "[ inspector ]", fill=(163, 230, 53), font=f_font)
        ftx += f_font.getlength("[ inspector ]") + 14
        d.text((ftx, fty), "hovered: ", fill=(148, 163, 184), font=f_font)
        ftx += f_font.getlength("hovered: ")
        d.text((ftx, fty), hov_id, fill=(203, 213, 225), font=f_font)
        right = "build 2026.04.18-r3 · session #f81d · cwd ~/ops/refunds"
        rw = f_font.getlength(right)
        d.text((WIDTH - rw - 14, fty), right, fill=(100, 116, 139), font=f_font)

        # Cursor overlay (tip of arrow at cursor (x, y))
        frame.paste(cursor, (x - 2, y - 2), cursor)

        frame.save(frames_dir / f"f_{i:06d}.png", optimize=False, compress_level=1)
        if i % 300 == 0:
            print(f"  rendered frame {i}/{n_frames}")

    out = ASSETS / "recording.webm"
    cmd = [
        "ffmpeg",
        "-y",
        "-framerate",
        str(FPS),
        "-i",
        str(frames_dir / "f_%06d.png"),
        "-i",
        str(audio_wav),
        "-c:v",
        "libvpx-vp9",
        "-crf",
        "32",
        "-b:v",
        "0",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "libopus",
        "-b:a",
        "96k",
        "-shortest",
        "-deadline",
        "good",
        "-cpu-used",
        "4",
        str(out),
    ]
    subprocess.run(cmd, check=True)
    return out


def main():
    bg_png, bboxes = render_dashboard()
    audio_wav, segments = build_audio()

    # Gold timestamp = midpoint of the "this one right here is the problem"
    # phrase, regardless of where the cursor is at that moment. The cursor
    # may be on a decoy element by design.
    gold_seg = next(
        s for s in segments if "this one right here is the problem" in s["text"].lower()
    )
    gold_mid = (gold_seg["start"] + gold_seg["end"]) / 2.0
    gold_record = {
        "element_id": GOLD_ELEMENT_ID,
        "visible_text": GOLD_VISIBLE_TEXT,
        "timestamp_sec": round(gold_mid, 2),
        "gold_phrase_start": round(gold_seg["start"], 2),
        "gold_phrase_end": round(gold_seg["end"], 2),
        "bbox": bboxes[GOLD_ELEMENT_ID],
    }
    (WORK / "recording_gold.json").write_text(json.dumps(gold_record, indent=2) + "\n")
    print(f"Gold ground truth: {gold_record}")

    out = render_video(audio_wav, segments, bg_png, bboxes)
    print(f"Wrote {out} ({out.stat().st_size / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()
