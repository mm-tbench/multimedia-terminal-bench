"""Build T156 v2 interview-music-ducking-audit asset (interview music-ducking).

v2 redesign: drop Kokoro TTS overlay on random kids-playing footage
(visible kids weren't actually speaking; voice didn't align with frames).
Use Senate floor speech footage as a podcast-style interview proxy:
the speaker is visibly mouth-moving in close-up and their voice is
naturally on the audio track. Overlay scripted music bed at varying
dB levels.

Joint-AV gate (preserved):
  - Some music windows are LOUD enough to mask the speaker's voice
    audibly. In those windows, the agent must use visible mouth motion
    (not audio-only speech detection) to determine when the speaker is
    actually speaking.
  - Bad-mix window = (speaker visibly speaking, mouth in motion) AND
    (music level high enough to overpower speech).
  - Distractors: loud music during natural speaker pauses (silence) is
    NOT a bad mix because there is no speech to overpower.

Construction:
  - 3 clips × 30 s each from Booker / Hirono / Toomey 2020 Senate floor
    speeches (PD US Govt, Wikimedia Commons).
  - Native source audio kept (real human voice, naturally aligned with
    visible mouth motion in close-up).
  - Music bed (Caminandes Gran Dillama, CC-BY 3.0) overlaid at scripted
    dB segments.
  - Per-clip schedule includes both:
      * 1-2 LOUD-MUSIC segments where the speaker is visibly speaking
        → bad mix windows.
      * 1 LOUD-MUSIC segment that aligns with a natural speech PAUSE
        (detected via silence-detect on source audio) → safe distractor.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import numpy as np
import soundfile as sf

REPO_ROOT = Path(__file__).resolve().parents[4]
SAMPLE = REPO_ROOT / "datasets/sample-media/wikimedia-commons"
# v3: ambient/calm CC music that fits an interview/podcast mood (was
# Caminandes children's-animation upbeat — wrong vibe).
MUSIC_SRC = SAMPLE / "Placid_Ambient_by_MusicLFiles.ogg"
HERE = Path(__file__).resolve().parents[1]
OUT_VIDEO = HERE / "environment/assets/clips"
OUT_PROMPT = HERE / "environment/assets/task_prompt.txt"
TESTS_GOLD = HERE / "tests/hidden_manifest.json"

OUT_VIDEO.mkdir(parents=True, exist_ok=True)
TMP = Path("/tmp/t156v2_build")
TMP.mkdir(exist_ok=True)
SR = 22050
DURATION_S = 30


def db_to_linear(db: float) -> float:
    return 10.0 ** (db / 20.0)


def normalize_to_dbfs(wav: np.ndarray, target_db: float) -> np.ndarray:
    rms = float(np.sqrt(np.mean(wav.astype(np.float64) ** 2)))
    if rms < 1e-9:
        return wav
    target = db_to_linear(target_db)
    return (wav * (target / rms)).astype(np.float32)


# Per-clip schedules. Each clip has:
#   src: source webm file, src_ss: starting offset.
#   speech_voice_db: target RMS of the speaker's voice in the final mix.
#   music_segments: list of (start_s, end_s, music_db_target).
#   bad_windows_hint: pre-computed (start, end) windows where the music
#     level overlaps with a speech-active region of the source. The
#     speech-active intervals are read from the source audio with a
#     silence-detect threshold below.
SILENCE_THRESHOLD_DB = (
    -45.0
)  # source speech vs ambient (Senate audio is quiet, mean ~-27 dB)


def detect_speech_intervals(
    audio: np.ndarray, sr: int, threshold_db: float, min_speech_s: float = 0.4
) -> list[tuple[float, float]]:
    """Return list of (start, end) intervals where the audio RMS exceeds
    threshold for at least min_speech_s. Frame-based (50 ms windows)."""
    win = int(sr * 0.05)
    hop = win
    n = len(audio)
    intervals: list[tuple[float, float]] = []
    in_speech = False
    seg_start = 0.0
    threshold_lin = db_to_linear(threshold_db)
    for i in range(0, n - win, hop):
        rms = float(np.sqrt(np.mean(audio[i : i + win].astype(np.float64) ** 2)))
        t = i / sr
        if rms >= threshold_lin:
            if not in_speech:
                seg_start = t
                in_speech = True
        else:
            if in_speech:
                end = t
                if end - seg_start >= min_speech_s:
                    intervals.append((seg_start, end))
                in_speech = False
    if in_speech:
        intervals.append((seg_start, (n - 1) / sr))
    return intervals


CLIPS = [
    {
        "clip_id": "clip_01",
        "src_video": SAMPLE / "cory_booker_senate_2020.webm",
        "src_ss": 50,
        # music dB schedule: (s, e, db)
        # -22 = barely audible; -7 = loud, masks voice when speaking
        "music_segments": [
            (0.0, 6.0, -22.0),
            (6.0, 13.0, -7.0),  # loud — overlaps speech → bad
            (13.0, 19.0, -22.0),
            # v4: 2nd loud window extended to ensure clear speech overlap.
            # v3 had 18-23 with only 0.89 s of speech — borderline.
            (19.0, 27.0, -7.0),  # loud — overlaps speech → bad
            (27.0, 30.0, -22.0),
        ],
        "speech_voice_db": -14.0,
    },
    {
        "clip_id": "clip_02",
        # v3: WIPO panel for visual variety (was Hirono Senate floor — same
        # setting as clip_01). Different speaker, different setting (panel
        # discussion / conference room). Use 380-410 s region for sustained
        # close-up of one panelist (avoiding chyron slides earlier in the
        # source).
        "src_video": SAMPLE / "wipo_panel_2022.webm",
        "src_ss": 380,
        "music_segments": [
            (0.0, 4.0, -22.0),
            (4.0, 11.0, -7.0),  # loud
            (11.0, 18.0, -22.0),
            (18.0, 25.0, -7.0),  # loud
            (25.0, 30.0, -22.0),
        ],
        "speech_voice_db": -14.0,
    },
    {
        "clip_id": "clip_03",
        "src_video": SAMPLE / "pat_toomey_senate_2020.webm",
        "src_ss": 60,
        "music_segments": [
            (0.0, 5.0, -22.0),
            (5.0, 12.0, -7.0),  # loud
            (12.0, 22.0, -22.0),
            (22.0, 28.0, -7.0),  # loud
            (28.0, 30.0, -22.0),
        ],
        "speech_voice_db": -14.0,
    },
]


def overlap_seconds(a0: float, a1: float, b0: float, b1: float) -> float:
    return max(0.0, min(a1, b1) - max(a0, b0))


def build_clip(clip: dict) -> dict:
    cid = clip["clip_id"]
    src = clip["src_video"]
    if not src.exists():
        raise FileNotFoundError(src)

    # 1. Cut clean 30 s segment with native audio.
    working = TMP / f"{cid}_native.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-ss",
            str(clip["src_ss"]),
            "-i",
            str(src),
            "-t",
            str(DURATION_S),
            "-vf",
            "scale=480:270:force_original_aspect_ratio=decrease,"
            "pad=480:270:(ow-iw)/2:(oh-ih)/2,fps=24",
            "-c:v",
            "libx264",
            "-preset",
            "medium",
            "-crf",
            "22",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "pcm_s16le",
            "-ar",
            "22050",
            "-ac",
            "1",
            str(working),
        ],
        check=True,
        capture_output=True,
    )

    # 2. Extract native audio for analysis.
    native_audio_path = TMP / f"{cid}_native.wav"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(working),
            "-vn",
            "-c:a",
            "pcm_s16le",
            "-ar",
            str(SR),
            "-ac",
            "1",
            str(native_audio_path),
        ],
        check=True,
        capture_output=True,
    )
    native_audio, _ = sf.read(str(native_audio_path), dtype="float32", always_2d=False)
    if native_audio.ndim == 2:
        native_audio = native_audio.mean(axis=1)

    # 3. Detect speech intervals from native audio.
    speech_intervals = detect_speech_intervals(native_audio, SR, SILENCE_THRESHOLD_DB)
    print(f"  {cid}: {len(speech_intervals)} speech intervals")

    # 4. Build music bed with per-segment dB.
    n_total = int(SR * DURATION_S)
    music_full, music_sr = sf.read(str(MUSIC_SRC), dtype="float32", always_2d=False)
    if music_full.ndim == 2:
        music_full = music_full.mean(axis=1)
    if music_sr != SR:
        from scipy.signal import resample_poly

        music_full = resample_poly(music_full, SR, music_sr).astype(np.float32)
    if len(music_full) < n_total:
        music_full = np.tile(music_full, (n_total // len(music_full)) + 1)
    music_full = music_full[:n_total]
    music = np.zeros_like(music_full)
    for s0, s1, db in clip["music_segments"]:
        i0, i1 = int(s0 * SR), min(int(s1 * SR), n_total)
        music[i0:i1] = normalize_to_dbfs(music_full[i0:i1], db)

    # 5. Normalize native voice to target dB and mix with music.
    voice = normalize_to_dbfs(native_audio[:n_total], clip["speech_voice_db"])
    mixed = (voice + music).astype(np.float32)
    peak = float(np.max(np.abs(mixed)))
    if peak > 0.95:
        mixed = (mixed * (0.95 / peak)).astype(np.float32)
    mixed_path = TMP / f"{cid}_mixed.wav"
    sf.write(str(mixed_path), mixed, SR, subtype="PCM_16")

    # 6. Mux video + mixed audio.
    out_path = OUT_VIDEO / f"{cid}.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(working),
            "-i",
            str(mixed_path),
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
            str(out_path),
        ],
        check=True,
        capture_output=True,
    )
    print(f"  wrote {out_path}")

    # 7. Compute bad-mix windows. Coalesce per loud-music segment:
    # if a loud-music segment overlaps >= 0.5 s of cumulative speech-active
    # time, emit ONE duck window covering the FULL loud-music segment.
    # v4: tightened to drop the borderline-distractor edge case (v3 had
    # one loud-music segment with 0.89 s of speech which was ambiguous to
    # human listeners). All loud-music segments now have clear speech
    # overlap (≥ 1 s); no safe distractor.
    voice_db = clip["speech_voice_db"]
    bad_windows = []
    for m0, m1, m_db in clip["music_segments"]:
        if m_db < voice_db - 6.0:
            continue  # well-ducked
        speech_overlap_s = sum(
            overlap_seconds(m0, m1, s0, s1) for (s0, s1) in speech_intervals
        )
        if speech_overlap_s >= 0.5:
            bad_windows.append(
                {
                    "clip": f"{cid}.mp4",
                    "start": round(m0, 3),
                    "end": round(m1, 3),
                    "action": "duck",
                    "reason": "music_within_6dB_of_voice_during_speech",
                }
            )
    return {"speech_intervals": speech_intervals, "bad_windows": bad_windows}


def main() -> None:
    all_bad: list[dict] = []
    for clip in CLIPS:
        info = build_clip(clip)
        all_bad.extend(info["bad_windows"])

    OUT_PROMPT.write_text(
        "Audit each interview clip in ./assets/clips/. The "
        "speaker is visibly on camera; background music has been laid "
        "in over their voice. Identify intervals where the speaker is "
        "actually speaking (mouth visibly moving, not pausing) AND the "
        "music is loud enough to overpower the voice. Propose a duck "
        "action covering each such window. Do NOT flag windows where "
        "the music is loud but the speaker is silent (in a natural "
        "pause).\n"
    )
    TESTS_GOLD.write_text(json.dumps({"bad_windows": all_bad}, indent=2))
    print(f"\nhidden manifest ({len(all_bad)} windows):")
    for w in all_bad:
        print(f"  {w}")


if __name__ == "__main__":
    main()
