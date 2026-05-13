#!/usr/bin/env python3
"""Build T014 v3 multi-speaker debate attribution asset.

v3 corrects v2's unrealism: v2 had idle Wav2Lip on every tile, which
made all four panellists look like they were silently mouthing words
while not their turn — not how humans behave on a video call.

v3 design:
  - Active speaker's tile during their utterance: Wav2Lip lip-sync
    (realistic, the actor's mouth moves while talking).
  - Inactive tiles: STATIC still on the actor's neutral frame. Mouth
    closed, no fake lip motion. Realistic for video-conferencing.
  - Two utterance pairs OVERLAP (utt 4 starts 1.0 s before utt 3
    ends; utt 7 starts 0.7 s before utt 6 ends). Audio is mixed and
    BOTH active speakers' tiles show lip-sync during overlap.
  - Speaker order A,B,C,A,D,B,C,D (debate-rebuttal flow).

Voices remain PAIRED across positions (A&C use am_michael; B&D use
am_eric) — voice alone gives 50% pair-ambiguity.

Honest trade-off
================

With realistic frozen-mouth idle, a vision-only agent can identify
the active speaker by "find the tile with lip movement". This is a
calibration-anchor task, not an MM<0.5 task. The MM<0.5 goal and the
realism guidance are fundamentally in tension for video-conference
attribution: any visible cue that disambiguates the active speaker
without fake lip motion (border highlight, name banner, microphone
icon) is also OCR-able by frontier MMs. Documented for record.

Voices remain PAIRED across positions (A&C use am_michael; B&D use
am_eric) — voice alone gives 50% pair-ambiguity.

Sources
=======

  - CREMA-D actors 1011, 1014, 1017, 1001 — ODbL 1.0
    (CheyneyComputerScience/CREMA-D)
  - Kokoro-82M — Apache 2.0
  - Wav2Lip checkpoint at /tmp/wav2lip_setup/ (from T125 build)

Run
===
    uv run --with kokoro --with soundfile --with pillow --with numpy \\
           python -u build_assets.py
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import numpy as np
import soundfile as sf

ROOT = Path(__file__).parent
ASSETS = ROOT / "environment" / "assets"
WORK = ROOT / ".build"
ASSETS.mkdir(parents=True, exist_ok=True)
WORK.mkdir(parents=True, exist_ok=True)

CREMA_DIR = ROOT.parent.parent.parent / "datasets" / "sample-media" / "crema-d"
WAV2LIP_DIR = Path("/tmp/wav2lip_setup/.claude/tmp/wav2lip_setup/Wav2Lip")
WAV2LIP_VENV = Path("/tmp/wav2lip_setup/.venv/bin/python3")
WAV2LIP_CKPT = WAV2LIP_DIR / "checkpoints" / "wav2lip_gan.pth"

W, H = 1280, 720
FPS = 25
SAMPLE_RATE = 24_000

# 2x2 panel tile sizes.
TILE_W, TILE_H = W // 2, H // 2

SPEAKERS = {
    "A": {"actor": 1011, "voice": "am_michael"},
    "B": {"actor": 1014, "voice": "am_eric"},
    "C": {"actor": 1017, "voice": "am_michael"},  # same voice as A
    "D": {"actor": 1001, "voice": "am_eric"},  # same voice as B
}

# Utterance specs. The schedule places turns one after another with the
# `gap_s` field controlling spacing relative to the previous utterance's
# end. Negative gap = overlap (next speaker cuts in early).
UTTERANCES = [
    # id, speaker, gap_s, text
    (
        1,
        "A",
        0.5,
        "I think every pull request needs at least two reviewers, no exceptions, even for one-line bug fixes. The discipline matters more than the cost.",
    ),
    (
        2,
        "B",
        0.6,
        "I disagree on one-line fixes. Forcing two reviewers on a typo correction is wasted attention. Tier the policy by surface area, not by line count.",
    ),
    (
        3,
        "C",
        0.6,
        "What about test files? My team treats those as zero-risk and skips review entirely. We have not been bitten yet, so the cost-benefit holds.",
    ),
    (
        4,
        "A",
        -1.0,  # OVERLAP: A cuts back in over C
        "Hold on, that is exactly the kind of false confidence I was warning about. A bad test passes silently for years and gives you nothing.",
    ),
    (
        5,
        "D",
        0.6,
        "Agreed with that. We caught a regression last quarter that had been wrong in tests since launch. Tests are critical infrastructure.",
    ),
    (
        6,
        "B",
        0.6,
        "Sure, but the answer there is automation. Static analysis, lint rules, and policy-as-code catch the structural issues, leaving humans for design and intent.",
    ),
    (
        7,
        "C",
        -0.7,  # OVERLAP: C interjects on B
        "Automation has limits, though. A linter cannot tell you that the abstraction is wrong, only that the syntax is right. Some judgement still requires a person.",
    ),
    (
        8,
        "D",
        0.6,
        "And the judgement should be written down. Every code review should produce a written rationale, not just a thumbs-up. Otherwise the review is theater.",
    ),
]


# ---------------------------------------------------------------------------
# Step 1: extract a clean neutral still frame for each actor.
# ---------------------------------------------------------------------------


def extract_actor_still(actor_id: int) -> Path:
    src = CREMA_DIR / f"{actor_id}_IEO_NEU_XX.flv"
    out = WORK / f"actor_{actor_id}_still.png"
    if out.exists():
        return out
    if not src.exists():
        raise FileNotFoundError(f"missing CREMA-D source: {src}")
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-loglevel",
            "error",
            "-ss",
            "1.0",
            "-i",
            str(src),
            "-frames:v",
            "1",
            str(out),
        ],
        check=True,
    )
    return out


# ---------------------------------------------------------------------------
# Step 2: idle-driver audio per actor (their own CREMA-D neutral speech,
# concat to fill ~120 s so we have plenty of duration for the panel).
# ---------------------------------------------------------------------------

NEUTRAL_SENTENCES = ["IEO", "MTI", "TIE", "IOM", "ITS", "DFA", "ITH"]


def build_idle_audio(actor_id: int, target_dur_s: float = 120.0) -> Path:
    out = WORK / f"actor_{actor_id}_idle_audio.wav"
    if out.exists():
        return out
    # Find available neutral CREMA-D wavs for this actor.
    avail = []
    for s in NEUTRAL_SENTENCES:
        cand = CREMA_DIR / f"{actor_id}_{s}_NEU_XX.wav"
        if cand.exists():
            avail.append(cand)
    if not avail:
        raise FileNotFoundError(f"no neutral CREMA-D audio for actor {actor_id}")
    # Concat with small gaps until we exceed target duration.
    pieces: list[np.ndarray] = []
    total = 0.0
    while total < target_dur_s:
        for src in avail:
            data, sr = sf.read(str(src))
            if data.ndim > 1:
                data = data.mean(axis=1)
            if sr != SAMPLE_RATE:
                # Resample naively via numpy (CREMA-D wavs are typically
                # 16 kHz; we want 24 kHz).
                ratio = SAMPLE_RATE / sr
                n_out = int(len(data) * ratio)
                data = np.interp(
                    np.linspace(0, len(data), n_out, endpoint=False),
                    np.arange(len(data)),
                    data,
                ).astype(np.float32)
            pieces.append(data.astype(np.float32))
            # Small breath pause.
            pieces.append(np.zeros(int(SAMPLE_RATE * 0.4), dtype=np.float32))
            total += (len(data) / SAMPLE_RATE) + 0.4
            if total >= target_dur_s:
                break
    full = np.concatenate(pieces)[: int(target_dur_s * SAMPLE_RATE)]
    sf.write(str(out), full, SAMPLE_RATE)
    return out


# ---------------------------------------------------------------------------
# Step 3: synthesise per-utterance Kokoro TTS.
# ---------------------------------------------------------------------------


def synth_utterance_audio(utt_id: int, voice: str, text: str) -> Path:
    out = WORK / f"utt_{utt_id:02d}_{voice}.wav"
    if out.exists():
        return out
    from kokoro import KPipeline

    pipeline = KPipeline(lang_code="a")
    chunks = []
    for _gs, _ps, audio in pipeline(text, voice=voice):
        chunks.append(audio)
    if not chunks:
        raise RuntimeError(f"empty Kokoro output for utt {utt_id}")
    wav = np.concatenate(chunks).astype(np.float32)
    sf.write(str(out), wav, SAMPLE_RATE)
    return out


# ---------------------------------------------------------------------------
# Step 4: Wav2Lip lip-sync — both idle (long, per-actor) and per-utterance.
# ---------------------------------------------------------------------------


def wav2lip(face_image: Path, audio_wav: Path, out_mp4: Path) -> None:
    if out_mp4.exists():
        return
    print(f"[Wav2Lip] face={face_image.name} audio={audio_wav.name} -> {out_mp4.name}")
    subprocess.run(
        [
            str(WAV2LIP_VENV),
            "inference.py",
            "--checkpoint_path",
            str(WAV2LIP_CKPT),
            "--face",
            str(face_image),
            "--audio",
            str(audio_wav),
            "--outfile",
            str(out_mp4),
            "--pads",
            "0",
            "20",
            "0",
            "0",
            "--wav2lip_batch_size",
            "64",
            "--static",
            "True",
        ],
        cwd=str(WAV2LIP_DIR),
        check=True,
    )


# ---------------------------------------------------------------------------
# Step 5: build per-actor full-duration tile video by overlaying utterance
# lip-sync clips on top of the idle clip at their scheduled times.
# ---------------------------------------------------------------------------


def build_tile_video(
    actor_id: int,
    speaker_id: str,
    still_image: Path,
    utterances_for_speaker: list[dict],
    total_dur_s: float,
    out: Path,
) -> None:
    """Per-actor tile video: static still as the base + each utterance's
    Wav2Lip lip-sync clip overlaid during its [start_sec, end_sec]
    window. Inactive moments show the still — no fake lip movement."""
    if out.exists():
        return
    cmd = [
        "ffmpeg",
        "-y",
        "-loglevel",
        "error",
        "-loop",
        "1",
        "-framerate",
        str(FPS),
        "-t",
        str(total_dur_s),
        "-i",
        str(still_image),
    ]
    for u in utterances_for_speaker:
        cmd += ["-itsoffset", f"{u['start_sec']:.3f}", "-i", str(u["lipsync_clip"])]

    filter_parts = []
    current = "[0:v]"
    for i, u in enumerate(utterances_for_speaker, start=1):
        next_label = f"[v{i}]"
        ts = u["start_sec"]
        te = u["end_sec"]
        filter_parts.append(
            f"{current}[{i}:v]overlay=enable='between(t,{ts:.3f},{te:.3f})'{next_label}"
        )
        current = next_label

    cmd += [
        "-filter_complex",
        ";".join(filter_parts) if filter_parts else "[0:v]copy[final]",
        "-map",
        current if filter_parts else "[final]",
        "-an",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "23",
        "-r",
        str(FPS),
        "-pix_fmt",
        "yuv420p",
        "-t",
        str(total_dur_s),
        str(out),
    ]
    subprocess.run(cmd, check=True)


# ---------------------------------------------------------------------------
# Step 6: mix utterance audios into a single track (with overlaps where
# the gap_s schedule was negative).
# ---------------------------------------------------------------------------


def mix_audio_track(utterances: list[dict], total_dur_s: float, out: Path) -> None:
    cmd = ["ffmpeg", "-y", "-loglevel", "error"]
    for u in utterances:
        cmd += ["-i", str(u["audio"])]
    # Silent base.
    cmd += [
        "-f",
        "lavfi",
        "-t",
        str(total_dur_s),
        "-i",
        f"anullsrc=r={SAMPLE_RATE}:cl=mono",
    ]

    filter_parts = []
    delayed = []
    for i, u in enumerate(utterances):
        delay_ms = int(u["start_sec"] * 1000)
        filter_parts.append(f"[{i}:a]adelay={delay_ms}|{delay_ms}[a{i}]")
        delayed.append(f"[a{i}]")
    base_idx = len(utterances)
    delayed.append(f"[{base_idx}:a]")
    n_inputs = len(delayed)
    filter_parts.append(
        "".join(delayed)
        + f"amix=inputs={n_inputs}:dropout_transition=0:normalize=0[mixed]"
    )

    cmd += [
        "-filter_complex",
        ";".join(filter_parts),
        "-map",
        "[mixed]",
        "-c:a",
        "pcm_s16le",
        "-ar",
        str(SAMPLE_RATE),
        "-ac",
        "1",
        "-t",
        str(total_dur_s),
        str(out),
    ]
    subprocess.run(cmd, check=True)


# ---------------------------------------------------------------------------
# Step 7: xstack 4 tiles + audio.
# ---------------------------------------------------------------------------


def compose_panel_v2(
    tile_videos: dict[str, Path], audio_track: Path, total_dur_s: float, out: Path
) -> None:
    cmd = ["ffmpeg", "-y", "-loglevel", "error"]
    for sid in "ABCD":
        cmd += ["-i", str(tile_videos[sid])]
    cmd += ["-i", str(audio_track)]

    filter_parts = []
    for i, sid in enumerate("ABCD"):
        filter_parts.append(
            f"[{i}:v]scale={TILE_W}:{TILE_H}:force_original_aspect_ratio=decrease,"
            f"pad={TILE_W}:{TILE_H}:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1[s{i}]"
        )
    filter_parts.append(
        "[s0][s1][s2][s3]xstack=inputs=4:layout=0_0|w0_0|0_h0|w0_h0[panel]"
    )

    cmd += [
        "-filter_complex",
        ";".join(filter_parts),
        "-map",
        "[panel]",
        "-map",
        f"{4}:a",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "23",
        "-pix_fmt",
        "yuv420p",
        "-r",
        str(FPS),
        "-c:a",
        "aac",
        "-b:a",
        "128k",
        "-ar",
        str(SAMPLE_RATE),
        "-ac",
        "1",
        "-movflags",
        "+faststart",
        "-t",
        str(total_dur_s),
        str(out),
    ]
    subprocess.run(cmd, check=True)


# ---------------------------------------------------------------------------
# Main.
# ---------------------------------------------------------------------------


def main():
    print("Step 1: extract actor stills", flush=True)
    stills = {}
    for sid, info in SPEAKERS.items():
        stills[sid] = extract_actor_still(info["actor"])
        print(f"  {sid}: actor {info['actor']}", flush=True)

    print("Step 2: synth Kokoro TTS for utterances", flush=True)
    utt_audios = {}
    utt_durations = {}
    for utt_id, speaker, _gap, text in UTTERANCES:
        voice = SPEAKERS[speaker]["voice"]
        utt_audios[utt_id] = synth_utterance_audio(utt_id, voice, text)
        with sf.SoundFile(str(utt_audios[utt_id])) as f:
            utt_durations[utt_id] = f.frames / f.samplerate
        print(
            f"  utt {utt_id} ({speaker}, {voice}): {utt_durations[utt_id]:.2f}s",
            flush=True,
        )

    print(
        "Step 4: Wav2Lip — per-utterance only (no idle, realistic frozen idle)",
        flush=True,
    )
    utt_lipsync = {}
    for utt_id, speaker, _gap, _text in UTTERANCES:
        clip = WORK / f"lipsync_utt_{utt_id:02d}.mp4"
        wav2lip(stills[speaker], utt_audios[utt_id], clip)
        utt_lipsync[utt_id] = clip

    print("Step 5: compute schedule (with overlaps)", flush=True)
    cursor = 0.0
    schedule = []
    for utt_id, speaker, gap_s, _text in UTTERANCES:
        start = cursor + gap_s if cursor > 0 else gap_s
        # gap_s is relative to previous utterance's END. cursor = previous utt's end.
        end = start + utt_durations[utt_id]
        schedule.append(
            {
                "id": utt_id,
                "speaker": speaker,
                "start_sec": round(start, 3),
                "end_sec": round(end, 3),
                "audio": utt_audios[utt_id],
                "lipsync_clip": utt_lipsync[utt_id],
            }
        )
        # Cursor advances to this utt's end (negative gap_s = overlap, but
        # the cursor is the latest end so far).
        cursor = max(cursor, end)
        print(f"  utt {utt_id} ({speaker}): {start:.2f} → {end:.2f}", flush=True)
    total_dur_s = cursor + 0.5  # half-second tail

    print(
        "Step 6: build per-actor tile videos (still + utterance overlays)", flush=True
    )
    tile_videos = {}
    for sid in "ABCD":
        tile = WORK / f"tile_{sid}.mp4"
        utts_for_speaker = [u for u in schedule if u["speaker"] == sid]
        build_tile_video(
            SPEAKERS[sid]["actor"],
            sid,
            stills[sid],
            utts_for_speaker,
            total_dur_s,
            tile,
        )
        tile_videos[sid] = tile
        print(f"  tile {sid}: {len(utts_for_speaker)} utterance overlays", flush=True)

    print("Step 7: mix audio track", flush=True)
    audio_track = WORK / "panel_audio.wav"
    if audio_track.exists():
        audio_track.unlink()
    mix_audio_track(schedule, total_dur_s, audio_track)

    print("Step 8: compose final 2x2 panel", flush=True)
    out_mp4 = ASSETS / "panel.mp4"
    if out_mp4.exists():
        out_mp4.unlink()
    compose_panel_v2(tile_videos, audio_track, total_dur_s, out_mp4)
    print(f"Wrote {out_mp4} ({out_mp4.stat().st_size / 1e6:.1f} MB)", flush=True)
    sha = hashlib.sha256(out_mp4.read_bytes()).hexdigest()
    print(f"sha256 = {sha}", flush=True)

    # Sidecar.
    utterance_index = {
        "utterances": [
            {"id": u["id"], "start_sec": u["start_sec"], "end_sec": u["end_sec"]}
            for u in schedule
        ],
        "speakers": ["A", "B", "C", "D"],
    }
    sidecar = ASSETS / "utterance_index.json"
    sidecar.write_text(json.dumps(utterance_index, indent=2))
    print(
        f"Wrote {sidecar} (sha256 {hashlib.sha256(sidecar.read_bytes()).hexdigest()})",
        flush=True,
    )

    # Hidden gold.
    gold = {"utterances": [{"id": u["id"], "speaker": u["speaker"]} for u in schedule]}
    (WORK / "gold.json").write_text(json.dumps(gold, indent=2))
    print(f"Gold: {[(g['id'], g['speaker']) for g in gold['utterances']]}")


if __name__ == "__main__":
    main()
