"""Oracle solver for T132 v2 — cutaway/B-roll lipsync drift.

The asset is a single-frame composite where the active speaker (Booker)
appears in 24 s of the 30 s timeline and a different senator (Hirono)
fills the 6 s of cutaway B-roll. Audio is Booker's voice continuous,
drifted by some unknown offset.

A naive whole-clip cross-correlation works because the active speaker's
windows dominate: their mouth motion correlates with the audio at the
true lag, while the cutaway frames contribute uncorrelated noise that
doesn't bias the peak away from the true offset.

Pipeline:
  1. Open the video. Run mediapipe FaceLandmarker over every frame and
     compute mouth-aspect-ratio (vertical lip-landmark distance).
  2. Cross-correlate the visual-derivative signal against the librosa
     onset-strength envelope of the audio (start_time-aware) to find
     the global lag.
  3. Apply the inverse correction via ffmpeg adelay (positive correction)
     or atrim (negative correction). Re-mux as PCM-in-MOV.
  4. Write sync_report.json.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import cv2
import librosa
import mediapipe as mp
import numpy as np
from mediapipe.tasks import python as mp_python
from mediapipe.tasks.python import vision
from scipy.signal import correlate

UPPER_LIP_IDX = 13
LOWER_LIP_IDX = 14
MODEL_PATH = os.environ.get("FACE_LANDMARKER_MODEL", "/app/assets/face_landmarker.task")


def measure_offset_ms(video_path: str) -> dict:
    options = vision.FaceLandmarkerOptions(
        base_options=mp_python.BaseOptions(model_asset_path=MODEL_PATH),
        running_mode=vision.RunningMode.VIDEO,
        num_faces=1,
        min_face_detection_confidence=0.4,
        min_face_presence_confidence=0.4,
        min_tracking_confidence=0.4,
    )
    landmarker = vision.FaceLandmarker.create_from_options(options)
    cap = cv2.VideoCapture(video_path)
    fps = cap.get(cv2.CAP_PROP_FPS)
    n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    mar = np.full(n, np.nan, dtype=np.float64)
    for i in range(n):
        ok, frame = cap.read()
        if not ok:
            break
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        mp_img = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
        res = landmarker.detect_for_video(mp_img, int(round(i * 1000.0 / fps)))
        if res.face_landmarks:
            face = res.face_landmarks[0]
            mar[i] = abs(face[UPPER_LIP_IDX].y - face[LOWER_LIP_IDX].y)
    cap.release()
    landmarker.close()

    valid = ~np.isnan(mar)
    if valid.sum() < 30:
        raise RuntimeError(f"insufficient face frames: {int(valid.sum())}/{n}")
    visual = np.zeros(n)
    visual[valid] = mar[valid] - np.nanmean(mar[valid])
    visual = np.interp(np.arange(n), np.arange(n)[valid], visual[valid])
    visual_d = np.abs(np.diff(visual, prepend=visual[0]))

    y, sr = librosa.load(video_path, sr=16000, mono=True)
    out = subprocess.check_output(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "a:0",
            "-show_entries",
            "stream=start_time",
            "-of",
            "default=nw=1:nk=1",
            video_path,
        ],
        text=True,
    ).strip()
    try:
        astart = float(out) if out else 0.0
    except ValueError:
        astart = 0.0
    if astart > 0:
        y = np.concatenate([np.zeros(int(round(astart * sr)), dtype=y.dtype), y])
    hop = max(1, int(round(sr / fps)))
    audio_oe = librosa.onset.onset_strength(y=y, sr=sr, hop_length=hop)
    if len(audio_oe) >= n:
        audio_oe = audio_oe[:n]
    else:
        audio_oe = np.pad(audio_oe, (0, n - len(audio_oe)))

    L = min(len(visual_d), len(audio_oe))
    v = visual_d[:L].astype(np.float64) - visual_d[:L].mean()
    a = audio_oe[:L].astype(np.float64) - audio_oe[:L].mean()
    v /= max(np.linalg.norm(v), 1e-9)
    a /= max(np.linalg.norm(a), 1e-9)
    xc = correlate(a, v, mode="full")
    lags = np.arange(-L + 1, L)
    max_lag = int(round(fps * 1.0))
    mid = L - 1
    xc_w = xc[mid - max_lag : mid + max_lag + 1]
    lags_w = lags[mid - max_lag : mid + max_lag + 1]
    peak = int(np.argmax(xc_w))
    confidence = float(xc_w[peak]) / max(float(np.median(np.abs(xc_w))), 1e-9)
    return {
        "offset_ms": float(lags_w[peak] / fps * 1000.0),
        "confidence": confidence,
        "fps": float(fps),
        "face_coverage": float(valid.sum() / n),
    }


def apply_correction(src: str, dst: str, offset_ms: float) -> None:
    correction_ms = -offset_ms
    abs_ms = abs(correction_ms)
    if correction_ms >= 0:
        af = f"adelay={int(round(abs_ms))}|{int(round(abs_ms))}"
    else:
        af = f"atrim=start={abs_ms / 1000.0:.3f},asetpts=PTS-STARTPTS"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-i",
            src,
            "-filter_complex",
            f"[0:a]{af}[a]",
            "-map",
            "0:v:0",
            "-map",
            "[a]",
            "-c:v",
            "copy",
            "-c:a",
            "pcm_s16le",
            "-ar",
            "22050",
            "-ac",
            "1",
            "-shortest",
            dst,
        ],
        check=True,
        capture_output=True,
    )


def main() -> None:
    src, dst, report_path = sys.argv[1], sys.argv[2], sys.argv[3]
    result = measure_offset_ms(src)
    apply_correction(src, dst, result["offset_ms"])
    Path(report_path).write_text(
        json.dumps(
            {
                "measured_offset_ms": round(result["offset_ms"], 2),
                "applied_correction_ms": round(-result["offset_ms"], 2),
                "fps": result["fps"],
                "face_coverage": round(result["face_coverage"], 3),
                "confidence": round(result["confidence"], 3),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
