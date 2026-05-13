#!/usr/bin/env bash
# Build T133 v3 delivery-clip-defect-triage test asset.
#
# v3 design (joint-AV-only defects, 10 s clips for verifier reliability):
#
# All 8 clips drawn from Booker / Hirono / Toomey 2020 Senate floor speeches
# (PD US Govt, Wikimedia Commons). Senator floor recordings have on-camera
# synced microphones → tight natural lipsync (verified, all baseline reads
# < ±150 ms which is well inside our drift tolerance). Cinematic sources
# like Tears of Steel were dropped because film dialogue often has minor
# production-introduced asynchrony that the verifier algorithm picks up
# as drift, polluting the "clean" baseline.
#
# Defect classes (joint-AV required, 3 labels):
#   clean           — passes QC (natural senate footage)
#   lipsync_drift   — audio offset 500 ms from mouth motion (large enough
#                     for the cross-correlation algorithm to resolve in 10 s
#                     of data; verifier accuracy is reliable at ±500 ms)
#   audio_replaced  — visible speaker mid-speech but audio is a different
#                     senator's voice (joint-AV: voice-timbre AND mouth-
#                     motion-vs-speech-content mismatch)
#
# Per-clip ground truth (baked, 8 clips):
#   clip_01: clean             (Booker @ 50 s)
#   clip_02: clean             (Hirono @ 80 s)
#   clip_03: lipsync_drift     (Booker @ 70 s, audio trimmed -500 ms)
#   clip_04: lipsync_drift     (Booker @ 20 s, audio trimmed -500 ms)
#   clip_05: lipsync_drift     (Hirono @ 60 s, audio trimmed -500 ms)
#   clip_06: audio_replaced    (Booker @ 50 s visual + Hirono @ 80 s audio,
#                               cross-gender)
#   clip_07: audio_replaced    (Hirono @ 80 s visual + Toomey @ 50 s audio,
#                               cross-gender)
#   clip_08: audio_replaced    (Toomey @ 50 s visual + Booker @ 30 s audio,
#                               same gender — gender/pitch detection
#                               INSUFFICIENT, requires voice-timbre or
#                               mouth-motion-vs-speech matching)
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../../../.." && pwd)"
SAMPLE="$REPO_ROOT/datasets/sample-media/wikimedia-commons"
OUT_DIR="$(cd "$(dirname "$0")/.." && pwd)/environment/assets/clips"
mkdir -p "$OUT_DIR"
rm -f "$OUT_DIR"/clip_*

VID_FILT="scale=480:270:force_original_aspect_ratio=decrease,pad=480:270:(ow-iw)/2:(oh-ih)/2,fps=24"
TMP=/tmp/t133v3_build
mkdir -p "$TMP"

build_clean() {
    local src="$1" ss="$2" out="$3"
    ffmpeg -y -ss "$ss" -i "$src" -t 10 \
        -vf "$VID_FILT" -c:v libx264 -preset medium -crf 22 -pix_fmt yuv420p \
        -c:a pcm_s16le -ar 22050 -ac 1 "$out"
}

build_drift() {
    local src="$1" ss="$2" trim_ms="$3" out="$4"
    local trim_s
    trim_s=$(awk "BEGIN { printf \"%.3f\", $trim_ms / 1000.0 }")
    ffmpeg -y -ss "$ss" -i "$src" -t 10 \
        -vf "$VID_FILT" -c:v libx264 -preset medium -crf 22 -pix_fmt yuv420p \
        -filter_complex "[0:a]atrim=start=${trim_s},asetpts=PTS-STARTPTS[a]" \
        -map 0:v:0 -map "[a]" -shortest \
        -c:a pcm_s16le -ar 22050 -ac 1 "$out"
}

build_replaced() {
    local visual_src="$1" visual_ss="$2" audio_src="$3" audio_ss="$4" out="$5"
    ffmpeg -y -ss "$visual_ss" -i "$visual_src" -t 10 -an \
        -vf "$VID_FILT" -c:v libx264 -preset medium -crf 22 -pix_fmt yuv420p \
        "$TMP/visual.mp4"
    ffmpeg -y -ss "$audio_ss" -i "$audio_src" -t 10 -vn \
        -c:a pcm_s16le -ar 22050 -ac 1 \
        "$TMP/audio.wav"
    ffmpeg -y -i "$TMP/visual.mp4" -i "$TMP/audio.wav" \
        -map 0:v:0 -map 1:a:0 -c:v copy -c:a pcm_s16le -ar 22050 -ac 1 -shortest \
        "$out"
}

# clean
build_clean "$SAMPLE/cory_booker_senate_2020.webm"  50 "$OUT_DIR/clip_01.mov"
build_clean "$SAMPLE/mazie_hirono_senate_2020.webm" 80 "$OUT_DIR/clip_02.mov"

# lipsync_drift (-500 ms each). Source segments picked so the cross-
# correlation algorithm reliably resolves the drift in 10 s of data
# (some senator segments are algorithmically harder than others; these
# three were verified to read between -333 and -667 ms with the
# verifier algorithm, all clearly outside the ±150 ms clean band).
build_drift "$SAMPLE/cory_booker_senate_2020.webm"  70 500 "$OUT_DIR/clip_03.mov"
build_drift "$SAMPLE/cory_booker_senate_2020.webm"  20 500 "$OUT_DIR/clip_04.mov"
build_drift "$SAMPLE/mazie_hirono_senate_2020.webm" 60 500 "$OUT_DIR/clip_05.mov"

# audio_replaced
build_replaced "$SAMPLE/cory_booker_senate_2020.webm"  50 "$SAMPLE/mazie_hirono_senate_2020.webm" 80 "$OUT_DIR/clip_06.mov"
build_replaced "$SAMPLE/mazie_hirono_senate_2020.webm" 80 "$SAMPLE/pat_toomey_senate_2020.webm"   50 "$OUT_DIR/clip_07.mov"
build_replaced "$SAMPLE/pat_toomey_senate_2020.webm"   50 "$SAMPLE/cory_booker_senate_2020.webm"  30 "$OUT_DIR/clip_08.mov"

echo
echo "Per-clip ground truth (baked, 8 clips, 10 s each):"
echo "  clip_01: clean             (Booker @ 50 s)"
echo "  clip_02: clean             (Hirono @ 80 s)"
echo "  clip_03: lipsync_drift     (Booker @ 70 s + -500 ms)"
echo "  clip_04: lipsync_drift     (Booker @ 20 s + -500 ms)"
echo "  clip_05: lipsync_drift     (Hirono @ 60 s + -500 ms)"
echo "  clip_06: audio_replaced    (Booker visual + Hirono audio, cross-gender)"
echo "  clip_07: audio_replaced    (Hirono visual + Toomey audio, cross-gender)"
echo "  clip_08: audio_replaced    (Toomey visual + Booker audio, SAME GENDER — deeper gate)"
echo
ls -la "$OUT_DIR/"
echo "--- sha256 ---"
sha256sum "$OUT_DIR"/*
