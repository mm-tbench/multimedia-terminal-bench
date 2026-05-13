"""Build T131 v1 multicam-active-speaker-cut asset.

Three-camera dialogue scene proxy. Three real senators are filmed in
their own ISO close-ups; a separate boom mix carries only the active
speaker per second. The agent must, second-by-second, identify which
ISO camera frames the active speaker and emit a cut list.

Joint-AV gate (anti-shortcut design):
  - Each ISO's AUDIO TRACK carries the boom mix (a realistic production
    practice where the boom is mixed into ISO backups), so per-camera
    audio cross-correlation cannot identify which cam frames whom.
  - Each ISO has a CHYRON name overlay (e.g., "SEN. CORY BOOKER")
    burned into the lower third — this anchors face-to-name per cam.
  - Boom audio carries the active senator's natural voice each second.
  - Agent must combine: (1) read chyron from each ISO to learn the
    cam → senator name mapping (visual), (2) identify the active
    senator from boom audio (voice ID per second), (3) cut to the
    matching cam.

Sources (PD US Govt, Wikimedia Commons):
  - Cory Booker / Mazie Hirono / Pat Toomey 2020 Senate floor speeches.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import soundfile as sf

REPO_ROOT = Path(__file__).resolve().parents[4]
SAMPLE = REPO_ROOT / "datasets/sample-media/wikimedia-commons"
HERE = Path(__file__).resolve().parents[1]
OUT_ASSETS = HERE / "environment/assets"
TESTS_GOLD = HERE / "tests/hidden_manifest.json"

OUT_ASSETS.mkdir(parents=True, exist_ok=True)
TMP = Path("/tmp/t131_build")
TMP.mkdir(exist_ok=True)

SR = 22050
FPS = 24
W, H = 480, 270
DURATION_S = 30

CAMS = {
    "cam1": {
        "src": SAMPLE / "cory_booker_senate_2020.webm",
        "chyron": "SEN. CORY BOOKER (D-NJ)",
    },
    "cam2": {
        "src": SAMPLE / "mazie_hirono_senate_2020.webm",
        "chyron": "SEN. MAZIE HIRONO (D-HI)",
    },
    "cam3": {
        "src": SAMPLE / "pat_toomey_senate_2020.webm",
        "chyron": "SEN. PAT TOOMEY (R-PA)",
    },
}

# Per-second active camera schedule (30 entries, one per second).
SCHEDULE = (
    ["cam1"] * 5
    + ["cam2"] * 5
    + ["cam3"] * 5
    + ["cam2"] * 3
    + ["cam1"] * 2
    + ["cam3"] * 6
    + ["cam2"] * 2
    + ["cam1"] * 2
)
assert len(SCHEDULE) == DURATION_S


def encode_iso(src: Path, chyron: str, boom_path: Path, out: Path) -> None:
    """Encode a 30 s ISO. Video = source close-up + chyron overlay
    burned into lower third. Audio = boom mix (same in all 3 ISOs)."""
    drawtext = (
        f"drawtext=text='{chyron}'"
        f":fontfile=/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
        f":fontcolor=white:fontsize=18:borderw=2:bordercolor=black"
        f":x=(w-text_w)/2:y=h-32:box=1:boxcolor=black@0.55:boxborderw=6"
    )
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-ss",
            "0",
            "-i",
            str(src),
            "-i",
            str(boom_path),
            "-t",
            str(DURATION_S),
            "-vf",
            f"scale={W}:{H}:force_original_aspect_ratio=decrease,"
            f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2,fps={FPS},{drawtext}",
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-c:v",
            "libx264",
            "-preset",
            "medium",
            "-crf",
            "22",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            "-ar",
            str(SR),
            "-ac",
            "1",
            "-r",
            str(FPS),
            "-shortest",
            str(out),
        ],
        check=True,
        capture_output=True,
    )


def extract_full_audio(src: Path, dur: float) -> np.ndarray:
    raw = TMP / f"_aud_{src.stem}.wav"
    if not raw.exists():
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-ss",
                "0",
                "-i",
                str(src),
                "-t",
                str(int(dur) + 5),
                "-vn",
                "-c:a",
                "pcm_s16le",
                "-ar",
                str(SR),
                "-ac",
                "1",
                str(raw),
            ],
            check=True,
            capture_output=True,
        )
    audio, _ = sf.read(str(raw), dtype="float32", always_2d=False)
    if audio.ndim == 2:
        audio = audio.mean(axis=1)
    return audio


def normalize_to_dbfs(wav: np.ndarray, target_db: float) -> np.ndarray:
    rms = float(np.sqrt(np.mean(wav.astype(np.float64) ** 2)))
    if rms < 1e-9:
        return wav
    target = 10.0 ** (target_db / 20.0)
    return (wav * (target / rms)).astype(np.float32)


def main() -> None:
    for cam, spec in CAMS.items():
        if not spec["src"].exists():
            print(f"missing source: {spec['src']}", file=sys.stderr)
            sys.exit(1)

    # 1. Build the boom mix first (we need it to lay into each ISO).
    # KEY: boom audio at clip-second t = active speaker's source audio
    # AT SECOND t (not at a cumulative-active cursor). This makes the
    # active cam's visible mouth motion at clip-sec t lip-sync with the
    # boom audio words, so the editor can do per-second lipsync matching
    # to identify the active cam — the universal joint-AV path that
    # doesn't require recognising any specific senator's voice.
    speaker_audio = {
        cam: extract_full_audio(spec["src"], DURATION_S) for cam, spec in CAMS.items()
    }
    n_total = DURATION_S * SR
    boom = np.zeros(n_total, dtype=np.float32)
    for t, cam in enumerate(SCHEDULE):
        i0 = t * SR
        i1 = i0 + SR
        src_audio = speaker_audio[cam]
        chunk = src_audio[i0:i1]
        if len(chunk) < SR:
            chunk = np.pad(chunk, (0, SR - len(chunk)))
        boom[i0:i1] = chunk

    boom = normalize_to_dbfs(boom, -14.0)
    peak = float(np.max(np.abs(boom)))
    if peak > 0.95:
        boom = (boom * (0.95 / peak)).astype(np.float32)
    boom_path = OUT_ASSETS / "boom.wav"
    sf.write(str(boom_path), boom, SR, subtype="PCM_16")
    print(f"wrote {boom_path}")

    # 2. Encode each ISO with chyron overlay + boom audio mixed in.
    for cam, spec in CAMS.items():
        out = OUT_ASSETS / f"{cam}.mp4"
        encode_iso(spec["src"], spec["chyron"], boom_path, out)
        print(f"wrote {out}")

    # 3. Task prompt.
    (OUT_ASSETS / "task_prompt.txt").write_text(
        "You are editing a 3-camera dialogue scene featuring three "
        "U.S. senators (their names are burned in as chyron overlays "
        "on each ISO camera). ./assets/cam1.mp4, cam2.mp4, cam3.mp4 "
        "are the 3 ISO angles, each framing one named senator "
        "continuously. ./assets/boom.wav is a separate clean boom-mic "
        "mix carrying only the active senator's voice each second. "
        "Per second, decide which ISO camera frames the currently "
        "active speaker, then write your decisions to "
        "./output/cut_list.json and produce a cut.mp4 that switches "
        "to the chosen camera each second.\n"
    )

    # 4. Hidden gold cut list.
    gold = [{"t": t, "cam": cam} for t, cam in enumerate(SCHEDULE)]
    TESTS_GOLD.write_text(json.dumps({"cut_list": gold}, indent=2))
    print(f"\nhidden manifest ({len(gold)} per-second labels):")
    for entry in gold[:5]:
        print(f"  {entry}")
    print(f"  ... ({len(gold) - 5} more)")


if __name__ == "__main__":
    main()
