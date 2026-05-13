"""Build T161 v3 creator-voiceover-lipsync-mismatch asset.

v3 hardening (all-real-footage joint-AV gate):

  - v1 collapsed to audio-only (every voiceover IS a perturbation).
  - v2 added PIL B-roll cards as cutaway distractors. MM solved 6/6
    cleanly because the visual gap (Senate close-up vs solid logo card)
    + audio gap (real voice vs Kokoro TTS) made both signals trivially
    classifiable. Joint-AV passed but the gates were each individually
    easy.
  - v3 closes BOTH shortcut paths:

    1. B-roll = a DIFFERENT real senator's close-up (also mouth-moving),
       not a logo card. Agent must do face identity ("is this the
       creator we saw in the native segments?"), not just "is there a
       human on screen vs a graphic".

    2. Voiceover = a THIRD real senator's clean speech excerpt, not
       Kokoro TTS. Agent must do voice identity ("does this voice
       match the creator's voice from native segments?"), not just
       "is this a TTS timbre".

Joint-AV truth table (per segment):
    creator face + creator's own voice  → no flag (normal speaking)
    creator face + voiceover-narrator   → FLAG (mismatch)
    B-roll face  + voiceover-narrator   → distractor (don't flag —
                                          B-roll cutaway, sponsor read)
    B-roll face  + creator's own voice  → not used

Per clip uses 3 distinct speakers (rotated across the 4 clips):
    clip_01: creator=Booker  broll=Hirono   voiceover=Toomey
    clip_02: creator=Hirono  broll=Toomey   voiceover=Booker
    clip_03: creator=Toomey  broll=Booker   voiceover=Hirono
    clip_04: creator=Booker  broll=Toomey   voiceover=Hirono
             (different src offsets so the agent can't shortcut by
              memorising creator face from clip_01)

Audio-only over-flags every voiceover window (catches B-roll
distractors). Visual-only can identify creator face but can't tell
native voice from voiceover. Joint-AV is the only path to clean strict
pass.

Sources (all PD US Govt, Wikimedia Commons): Cory Booker / Mazie
Hirono / Pat Toomey 2020 Senate floor speeches.
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
OUT_VIDEO = HERE / "environment/assets/shorts"
OUT_PROMPT = HERE / "environment/assets/task_prompt.txt"
TESTS_GOLD = HERE / "tests/hidden_manifest.json"

OUT_VIDEO.mkdir(parents=True, exist_ok=True)
TMP = Path("/tmp/t161v3_build")
TMP.mkdir(exist_ok=True)

SR = 22050
FPS = 24
W, H = 480, 270


def seg(start, end, video, audio, flag=False):
    return {
        "start": start,
        "end": end,
        "video_mode": video,
        "audio_mode": audio,
        "flag": flag,
    }


# Per-clip schedule. broll_offsets[i] gives the absolute source start
# (seconds) used for the i-th B-roll segment in the clip, so successive
# cutaways come from distinct moments of the broll source (visually
# different shots, not a freeze-frame).
# Each Senate source clip is ~100 s. broll_offsets must be ≤ ~95 s (5 s
# segment); voiceover_ss must be ≤ ~75 s (25 s extract per clip).
# clip_01 and clip_04 both use Booker as creator but at non-overlapping
# windows so the visible mouth motion content differs across the two
# clips.
CLIPS = [
    {
        "clip_id": "clip_01",
        "creator_video": SAMPLE / "cory_booker_senate_2020.webm",
        "creator_ss": 50,
        "broll_video": SAMPLE / "mazie_hirono_senate_2020.webm",
        "broll_offsets": [30.0, 70.0],
        "voiceover_audio_src": SAMPLE / "pat_toomey_senate_2020.webm",
        "voiceover_ss": 0,
        "duration_s": 30,
        "segments": [
            seg(0.0, 5.0, "creator", "native"),
            seg(5.0, 9.0, "broll", "voiceover"),
            seg(9.0, 13.0, "creator", "voiceover", flag=True),
            seg(13.0, 18.0, "creator", "native"),
            seg(18.0, 22.0, "broll", "voiceover"),
            seg(22.0, 26.0, "creator", "voiceover", flag=True),
            seg(26.0, 30.0, "creator", "native"),
        ],
    },
    {
        "clip_id": "clip_02",
        "creator_video": SAMPLE / "mazie_hirono_senate_2020.webm",
        "creator_ss": 50,
        "broll_video": SAMPLE / "pat_toomey_senate_2020.webm",
        "broll_offsets": [20.0, 80.0],
        "voiceover_audio_src": SAMPLE / "cory_booker_senate_2020.webm",
        "voiceover_ss": 0,
        "duration_s": 30,
        "segments": [
            seg(0.0, 6.0, "creator", "native"),
            seg(6.0, 11.0, "broll", "voiceover"),
            seg(11.0, 15.0, "creator", "voiceover", flag=True),
            seg(15.0, 22.0, "creator", "native"),
            seg(22.0, 27.0, "broll", "voiceover"),
            seg(27.0, 30.0, "creator", "native"),
        ],
    },
    {
        "clip_id": "clip_03",
        "creator_video": SAMPLE / "pat_toomey_senate_2020.webm",
        "creator_ss": 60,
        "broll_video": SAMPLE / "cory_booker_senate_2020.webm",
        "broll_offsets": [10.0, 30.0],
        "voiceover_audio_src": SAMPLE / "mazie_hirono_senate_2020.webm",
        "voiceover_ss": 0,
        "duration_s": 30,
        "segments": [
            seg(0.0, 4.0, "creator", "native"),
            seg(4.0, 9.0, "creator", "voiceover", flag=True),
            seg(9.0, 14.0, "broll", "voiceover"),
            seg(14.0, 20.0, "creator", "native"),
            seg(20.0, 24.0, "creator", "voiceover", flag=True),
            seg(24.0, 30.0, "broll", "voiceover"),
        ],
    },
    {
        "clip_id": "clip_04",
        "creator_video": SAMPLE / "cory_booker_senate_2020.webm",
        "creator_ss": 10,
        "broll_video": SAMPLE / "pat_toomey_senate_2020.webm",
        "broll_offsets": [50.0, 90.0],
        "voiceover_audio_src": SAMPLE / "mazie_hirono_senate_2020.webm",
        "voiceover_ss": 50,
        "duration_s": 30,
        "segments": [
            seg(0.0, 7.0, "creator", "native"),
            seg(7.0, 12.0, "broll", "voiceover"),
            seg(12.0, 17.0, "creator", "voiceover", flag=True),
            seg(17.0, 25.0, "creator", "native"),
            seg(25.0, 30.0, "broll", "voiceover"),
        ],
    },
]


def encode_video_segment(src: Path, abs_start: float, dur: float, out: Path) -> None:
    """Re-encode a video segment from `src` starting at `abs_start` for
    `dur` seconds. Uniform encoder params so all segments concat-copy
    cleanly. Audio is dropped; the composed audio is muxed at the end."""
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-ss",
            f"{abs_start:.3f}",
            "-i",
            str(src),
            "-t",
            f"{dur:.3f}",
            "-vf",
            f"scale={W}:{H}:force_original_aspect_ratio=decrease,"
            f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2,fps={FPS}",
            "-c:v",
            "libx264",
            "-preset",
            "medium",
            "-crf",
            "22",
            "-pix_fmt",
            "yuv420p",
            "-an",
            "-r",
            str(FPS),
            str(out),
        ],
        check=True,
        capture_output=True,
    )


def extract_audio(src: Path, abs_start: float, dur: float) -> np.ndarray:
    """Extract `dur` seconds of mono PCM audio from `src` starting at
    `abs_start`. Returns float32 array at SR."""
    raw = TMP / f"_aud_{src.stem}_{abs_start:.0f}_{dur:.0f}.wav"
    if not raw.exists():
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-ss",
                f"{abs_start:.3f}",
                "-i",
                str(src),
                "-t",
                f"{dur:.3f}",
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
    n_total = int(SR * dur)
    if len(audio) < n_total:
        audio = np.pad(audio, (0, n_total - len(audio)))
    else:
        audio = audio[:n_total]
    return audio


def normalize_to_dbfs(wav: np.ndarray, target_db: float) -> np.ndarray:
    rms = float(np.sqrt(np.mean(wav.astype(np.float64) ** 2)))
    if rms < 1e-9:
        return wav
    target = 10.0 ** (target_db / 20.0)
    return (wav * (target / rms)).astype(np.float32)


def build_clip(clip: dict) -> list[dict]:
    cid = clip["clip_id"]
    creator_src = clip["creator_video"]
    broll_src = clip["broll_video"]
    voiceover_src = clip["voiceover_audio_src"]
    for src in (creator_src, broll_src, voiceover_src):
        if not src.exists():
            print(f"missing source: {src}", file=sys.stderr)
            sys.exit(1)
    duration_s = clip["duration_s"]

    # 1. Per-segment video. Creator segments come from creator_src at the
    # corresponding offset within the clip's window. B-roll segments
    # come from broll_src at distinct offsets so each cutaway is a
    # different shot.
    seg_files: list[Path] = []
    broll_idx = 0
    for i, s in enumerate(clip["segments"]):
        seg_path = TMP / f"{cid}_v{i:02d}.mp4"
        dur = s["end"] - s["start"]
        if s["video_mode"] == "creator":
            abs_start = clip["creator_ss"] + s["start"]
            encode_video_segment(creator_src, abs_start, dur, seg_path)
        else:
            abs_start = clip["broll_offsets"][broll_idx]
            broll_idx += 1
            encode_video_segment(broll_src, abs_start, dur, seg_path)
        seg_files.append(seg_path)

    # 2. Concat segments into one video stream.
    concat_list = TMP / f"{cid}_concat.txt"
    concat_list.write_text("\n".join(f"file '{p}'" for p in seg_files))
    full_video = TMP / f"{cid}_video.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(concat_list),
            "-c",
            "copy",
            str(full_video),
        ],
        check=True,
        capture_output=True,
    )

    # 3. Composed audio. Native = creator's own audio (paired with
    # creator video segments). Voiceover = ONE continuous chunk of the
    # voiceover_audio_src, sliced sequentially across this clip's
    # voiceover slots so the narrator sounds like one continuous human
    # voice.
    n_total = int(SR * duration_s)
    creator_audio_full = extract_audio(creator_src, clip["creator_ss"], duration_s)

    voiceover_total_s = sum(
        s["end"] - s["start"]
        for s in clip["segments"]
        if s["audio_mode"] == "voiceover"
    )
    voiceover_audio = extract_audio(
        voiceover_src, clip["voiceover_ss"], voiceover_total_s + 1.0
    )
    voiceover_audio = normalize_to_dbfs(voiceover_audio, -14.0)
    creator_audio_full = normalize_to_dbfs(creator_audio_full, -14.0)

    out_audio = np.zeros(n_total, dtype=np.float32)
    vo_cursor = 0
    for s in clip["segments"]:
        i0 = int(s["start"] * SR)
        i1 = min(int(s["end"] * SR), n_total)
        if i1 <= i0:
            continue
        win = i1 - i0
        if s["audio_mode"] == "native":
            out_audio[i0:i1] = creator_audio_full[i0:i1]
        else:
            chunk = voiceover_audio[vo_cursor : vo_cursor + win]
            if len(chunk) < win:
                chunk = np.pad(chunk, (0, win - len(chunk)))
            out_audio[i0:i1] = chunk
            vo_cursor += win
    peak = float(np.max(np.abs(out_audio)))
    if peak > 0.95:
        out_audio = (out_audio * (0.95 / peak)).astype(np.float32)
    audio_path = TMP / f"{cid}_audio.wav"
    sf.write(str(audio_path), out_audio, SR, subtype="PCM_16")

    # 4. Mux video + composed audio.
    out = OUT_VIDEO / f"{cid}.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(full_video),
            "-i",
            str(audio_path),
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-c:v",
            "copy",
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            "-ar",
            str(SR),
            "-ac",
            "1",
            "-shortest",
            str(out),
        ],
        check=True,
        capture_output=True,
    )
    print(f"wrote {out}")

    flags = []
    for s in clip["segments"]:
        if s["flag"]:
            flags.append(
                {
                    "clip": f"{cid}.mp4",
                    "start": round(s["start"], 3),
                    "end": round(s["end"], 3),
                    "mismatch_type": "voiceover_during_lip_motion",
                }
            )
    return flags


def main() -> None:
    manifest: list[dict] = []
    for clip in CLIPS:
        manifest.extend(build_clip(clip))
    OUT_PROMPT.write_text(
        "Each clip in ./assets/shorts/ shows a creator on camera at "
        "times and cuts to other people (B-roll) at other times. The "
        "audio is sometimes the creator's own voice (lip motion "
        "matches) and sometimes a voiceover narrator with a different "
        "voice from the creator. Flag every interval where the "
        "on-camera creator is visibly speaking AND the audio is the "
        "voiceover narrator (the narrator says one thing while the "
        "creator's lips form a different sentence). Do NOT flag "
        "voiceover that plays over a B-roll cutaway showing someone "
        "other than the creator — that is normal sponsor production.\n"
    )
    TESTS_GOLD.write_text(json.dumps({"flags": manifest}, indent=2))
    print(f"\nhidden manifest ({len(manifest)} flags):")
    for w in manifest:
        print(f"  {w}")


if __name__ == "__main__":
    main()
