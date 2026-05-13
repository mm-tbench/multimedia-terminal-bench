#!/usr/bin/env python3
"""Build T167 lecture-demo-clip-extract: slide-section narration audit on a
real CC-BY-NC PyConline AU 2020 talk ("dask-image: distributed image
processing for large data" by Genevieve Buckley), trimmed to 25 min.

Task: agent receives the lecture + a list of 7 specific slide titles
that appear in the talk, and must locate the timestamp window for
each AND quote a verbatim phrase the presenter says while that slide
is visible.

Joint-AV is genuinely required:
  - Locating each slide window needs visual recognition of slide-title
    text PLUS audio cues for transitions ("now let's...", pauses).
  - The quoted phrase must be from inside the agent's claimed window
    AND be a verbatim substring of the actual transcript — paraphrasing
    fails. The verifier runs Whisper at verify-time and substring-
    checks the agent's quote against transcript inside the window.

Source: PyConline AU 2020, "dask-image: distributed image processing
for large data" by Genevieve Buckley. CC-BY-NC 4.0.
https://archive.org/details/pyconau_2020-daskimage_distributed_image_processing_for_large_data

Build steps:
  1. Trim the 26-min source to 25 min (skip intro).
  2. Re-encode lecture.mp4 with libx264 + faststart for video API.
  3. faster-whisper word-level transcript over the trimmed audio
     (used at build time to provide "gold phrases" the verifier
     accepts as canonical answers; agents are scored on substring
     match of their own quote against the transcript inside their
     window — no specific phrase is required).
  4. Hand-curated slide-title timeline (7 slides) auto-paired with
     gold phrases drawn from the build-time transcript inside each
     slide's window.

Run:
  uv run --with faster-whisper --with pillow --with numpy \\
         python -u build_assets.py
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).parent
ASSETS = ROOT / "environment" / "assets"
WORK = ROOT / ".build"
ASSETS.mkdir(parents=True, exist_ok=True)
WORK.mkdir(parents=True, exist_ok=True)


def _find_sample_media() -> Path:
    cands = [
        ROOT.parent.parent.parent / "datasets" / "sample-media",
    ]
    for c in cands:
        if (c / "lectures" / "pyconau_2020_daskimage.mp4").exists():
            return c
    raise RuntimeError(
        f"sample-media/lectures/pyconau_2020_daskimage.mp4 not found; tried {cands}"
    )


SAMPLE_MEDIA = _find_sample_media()
SOURCE_MP4 = SAMPLE_MEDIA / "lectures" / "pyconau_2020_daskimage.mp4"

TRIM_START_S = 30.0
TRIM_DURATION_S = 1500.0


# ---------------------------------------------------------------------------
# Hand-curated slide timeline (verified by frame inspection at 30s intervals).
# All windows are in trimmed-clip seconds (i.e., source - TRIM_START_S=30).
# Each slide title is unique inside the lecture so an agent that locates
# the slide via OCR / native vision can match by title alone.
# ---------------------------------------------------------------------------

SLIDE_TIMELINE = [
    {
        "slide_title": "Motivating examples",
        "start_sec": 175,
        "end_sec": 209,
    },
    {
        "slide_title": "Let's build a pipeline!",
        "start_sec": 470,
        "end_sec": 509,
    },
    {
        "slide_title": "4. Morphological operations",
        "start_sec": 750,
        "end_sec": 779,
    },
    {
        "slide_title": "5. Measuring objects",
        "start_sec": 930,
        "end_sec": 1049,
    },
    {
        "slide_title": "The full pipeline",
        "start_sec": 1255,
        "end_sec": 1284,
    },
    {
        "slide_title": "Custom functions",
        "start_sec": 1285,
        "end_sec": 1314,
    },
    {
        "slide_title": "Scaling up computation",
        "start_sec": 1315,
        "end_sec": 1334,
    },
]

IOU_TOL = 0.4
PASS_THRESHOLD = 0.67
MIN_QUOTE_WORDS = 5


def _normalize(s: str) -> str:
    return re.sub(r"[^\w\s]+", " ", s.lower())


def _normalize_ws(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


# ---------------------------------------------------------------------------
# Step 1: trim + re-encode lecture.mp4.
# ---------------------------------------------------------------------------


def trim_source() -> Path:
    out = WORK / "trimmed.mp4"
    if out.exists() and out.stat().st_size > 1_000_000:
        return out
    cmd = [
        "ffmpeg",
        "-y",
        "-loglevel",
        "error",
        "-ss",
        str(TRIM_START_S),
        "-i",
        str(SOURCE_MP4),
        "-t",
        str(TRIM_DURATION_S),
        "-c",
        "copy",
        "-movflags",
        "+faststart",
        str(out),
    ]
    subprocess.run(cmd, check=True)
    return out


def package_lecture(trimmed: Path) -> Path:
    out = ASSETS / "lecture.mp4"
    if out.exists():
        out.unlink()
    cmd = [
        "ffmpeg",
        "-y",
        "-loglevel",
        "error",
        "-i",
        str(trimmed),
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "23",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-b:a",
        "96k",
        "-ar",
        "24000",
        "-ac",
        "1",
        "-movflags",
        "+faststart",
        str(out),
    ]
    subprocess.run(cmd, check=True)
    return out


# ---------------------------------------------------------------------------
# Step 2: faster-whisper word-level timestamps (build-time only — used to
# pair each slide window with a gold phrase candidate the verifier accepts
# as a canonical example. The verifier does substring match of the agent's
# quote against the FULL transcript inside the agent's window, not against
# this gold phrase, so multiple valid phrases per window are accepted.)
# ---------------------------------------------------------------------------


def whisper_word_timestamps(trimmed: Path) -> list[dict]:
    cache = WORK / "whisper_words.json"
    if cache.exists():
        return json.loads(cache.read_text())

    print("  running faster-whisper medium int8 word timestamps…", flush=True)
    from faster_whisper import WhisperModel  # type: ignore

    model = WhisperModel("medium", device="cpu", compute_type="int8")
    segments, _info = model.transcribe(
        str(trimmed),
        word_timestamps=True,
        condition_on_previous_text=False,
        beam_size=5,
        language="en",
    )
    words = []
    seg_count = 0
    for seg in segments:
        seg_count += 1
        for w in seg.words or []:
            words.append(
                {
                    "word": (w.word or "").strip(),
                    "start": float(w.start),
                    "end": float(w.end),
                }
            )
    cache.write_text(json.dumps(words))
    print(
        f"  faster-whisper produced {len(words)} word-tokens "
        f"across {seg_count} segments",
        flush=True,
    )
    return words


def transcript_inside(words: list[dict], start: float, end: float) -> str:
    """Concatenated transcript of all words whose start time falls in [start, end]."""
    chunk = [w["word"] for w in words if start <= w["start"] <= end]
    return _normalize_ws(" ".join(chunk))


def pick_gold_phrase(words: list[dict], start: float, end: float) -> str:
    """Pick a distinctive 7-12 word phrase from inside [start, end] as the
    'canonical' phrase. We chunk transcript at 8-12 word windows and pick
    the one with the most content-word density (rare words = better)."""
    chunk = [w for w in words if start <= w["start"] <= end]
    if not chunk:
        return ""
    # Sliding 9-word windows
    best_phrase = ""
    best_score = -1.0
    for i in range(0, max(1, len(chunk) - 9)):
        window_words = chunk[i : i + 9]
        text = " ".join(w["word"].strip() for w in window_words)
        text_norm = _normalize(text)
        words_in = text_norm.split()
        # Score by alphabetic word length (more letters = more content)
        score = sum(len(w) for w in words_in if w.isalpha() and len(w) > 4)
        if score > best_score:
            best_score = score
            best_phrase = _normalize_ws(text)
    return best_phrase


# ---------------------------------------------------------------------------
# Main.
# ---------------------------------------------------------------------------


def main():
    print("Step 1: trim source video", flush=True)
    trimmed = trim_source()
    print(f"  trimmed: {trimmed} ({trimmed.stat().st_size / 1e6:.1f} MB)", flush=True)

    print("\nStep 2: faster-whisper word-level timestamps", flush=True)
    words = whisper_word_timestamps(trimmed)

    print("\nStep 3: derive gold phrases per slide window", flush=True)
    gold_with_phrases = []
    for slide in SLIDE_TIMELINE:
        phrase = pick_gold_phrase(words, slide["start_sec"], slide["end_sec"])
        gold_with_phrases.append(
            {
                **slide,
                "gold_phrase_example": phrase,
            }
        )
        print(
            f"  {slide['slide_title']:35} "
            f"[{slide['start_sec']:4.0f}-{slide['end_sec']:4.0f}s] "
            f"phrase example: {phrase!r}",
            flush=True,
        )

    print("\nStep 4: package lecture.mp4", flush=True)
    lecture_mp4 = package_lecture(trimmed)
    sha = hashlib.sha256(lecture_mp4.read_bytes()).hexdigest()
    print(
        f"  lecture.mp4 → {lecture_mp4.stat().st_size / 1e6:.1f} MB  "
        f"sha256={sha[:12]}…",
        flush=True,
    )

    print("\nStep 5: write hidden manifest + transcript", flush=True)
    hidden_manifest = {
        "iou_tol": IOU_TOL,
        "pass_threshold": PASS_THRESHOLD,
        "min_quote_words": MIN_QUOTE_WORDS,
        "n_gold": len(gold_with_phrases),
        "gold": gold_with_phrases,
    }
    tests_dir = ROOT / "tests"
    (tests_dir / "hidden_manifest.json").write_text(
        json.dumps(hidden_manifest, indent=2)
    )
    # Ship the full word-level transcript to the verifier so it can do
    # substring-checks on agent quotes without re-running Whisper.
    (tests_dir / "transcript_words.json").write_text(json.dumps(words))
    print(f"  hidden manifest: {tests_dir / 'hidden_manifest.json'}", flush=True)
    print(
        f"  transcript words: {len(words)} tokens "
        f"({(tests_dir / 'transcript_words.json').stat().st_size / 1e3:.1f} KB)",
        flush=True,
    )


if __name__ == "__main__":
    main()
