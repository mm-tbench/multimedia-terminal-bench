#!/usr/bin/env bash
# Build T132 v2 lipsync-drift-correction test asset (cutaway / B-roll design).
#
# Realistic broadcast scenario: a single speaker delivery with periodic
# cutaways to audience reaction shots of OTHER senators. Audio is the
# active speaker (Booker) throughout — drifted by -500 ms. The cutaway
# segments show a DIFFERENT senator's face (Hirono) whose mouth motion
# does not correspond to the audio, so an agent that naively runs face
# detection over every frame and cross-correlates against the full audio
# gets a noisy / wrong drift estimate. Solving cleanly requires
# identifying the active speaker's face and restricting the
# cross-correlation window to the segments where their face is on screen.
#
# Sources (both PD US Govt, Wikimedia Commons):
#   - Cory Booker 2020 Senate floor speech (active speaker, audio source)
#   - Mazie Hirono 2020 Senate floor speech (used for B-roll cutaways)
#
# Output: environment/assets/clip.mov — 30 s 480x360 H.264 + PCM mono
#         22050 Hz @ 24 fps, MOV container.
#
# Timeline (gold):
#   0-7 s   : Booker full-screen (active)
#   7-10 s  : Hirono cutaway (B-roll)
#   10-20 s : Booker full-screen (active)
#   20-23 s : Hirono cutaway (B-roll)
#   23-30 s : Booker full-screen (active)
# Audio: Booker's voice continuous, drifted -500 ms (audio early).
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../../../.." && pwd)"
SRC_BOOKER="$REPO_ROOT/datasets/sample-media/wikimedia-commons/cory_booker_senate_2020.webm"
SRC_HIRONO="$REPO_ROOT/datasets/sample-media/wikimedia-commons/mazie_hirono_senate_2020.webm"
OUT_DIR="$(cd "$(dirname "$0")/.." && pwd)/environment/assets"
mkdir -p "$OUT_DIR"

[ -f "$SRC_BOOKER" ] || { echo "missing source: $SRC_BOOKER" >&2; exit 1; }
[ -f "$SRC_HIRONO" ] || { echo "missing source: $SRC_HIRONO" >&2; exit 1; }

TMP=/tmp/t132v2_build
mkdir -p "$TMP"

VID_FILT="scale=480:360:force_original_aspect_ratio=increase,crop=480:360,fps=24"

# Booker chunks: 0-7 s, 10-20 s, 23-30 s of OUTPUT timeline correspond to
# Booker source 60+0 .. 60+7, 60+10 .. 60+20, 60+23 .. 60+30 (continuous
# Booker speech, source-aligned audio).
ffmpeg -y -ss 60 -i "$SRC_BOOKER" -t 7  -an -c:v libx264 -preset medium -crf 22 -pix_fmt yuv420p -vf "$VID_FILT" "$TMP/b1.mp4"
ffmpeg -y -ss 70 -i "$SRC_BOOKER" -t 10 -an -c:v libx264 -preset medium -crf 22 -pix_fmt yuv420p -vf "$VID_FILT" "$TMP/b2.mp4"
ffmpeg -y -ss 83 -i "$SRC_BOOKER" -t 7  -an -c:v libx264 -preset medium -crf 22 -pix_fmt yuv420p -vf "$VID_FILT" "$TMP/b3.mp4"

# Hirono cutaway chunks: 3 s each, taken from source 60-63 and 65-68.
ffmpeg -y -ss 60 -i "$SRC_HIRONO" -t 3 -an -c:v libx264 -preset medium -crf 22 -pix_fmt yuv420p -vf "$VID_FILT" "$TMP/h1.mp4"
ffmpeg -y -ss 65 -i "$SRC_HIRONO" -t 3 -an -c:v libx264 -preset medium -crf 22 -pix_fmt yuv420p -vf "$VID_FILT" "$TMP/h2.mp4"

# Concatenate composite video: Booker7 + Hirono3 + Booker10 + Hirono3 + Booker7 = 30 s.
cat > "$TMP/concat.txt" <<EOF
file 'b1.mp4'
file 'h1.mp4'
file 'b2.mp4'
file 'h2.mp4'
file 'b3.mp4'
EOF
ffmpeg -y -f concat -safe 0 -i "$TMP/concat.txt" -c:v libx264 -preset medium -crf 22 -pix_fmt yuv420p \
    "$TMP/composite_video.mp4"

# Booker's full 30 s audio (continuous from source 60-90 s) — content used
# wholesale, Booker IS the audio source for the entire delivery, only the
# VIDEO contains cutaways to Hirono.
ffmpeg -y -ss 60 -i "$SRC_BOOKER" -t 30 -vn -ac 1 -ar 22050 -c:a pcm_s16le \
    "$TMP/booker_audio.wav"

# Bake -500 ms drift on the audio (atrim from start, asetpts to t=0).
APPLIED_OFFSET_MS=-500
ABS_S=0.500
ffmpeg -y -i "$TMP/booker_audio.wav" \
    -filter_complex "[0:a]atrim=start=${ABS_S},asetpts=PTS-STARTPTS[a]" \
    -map "[a]" -c:a pcm_s16le -ar 22050 -ac 1 "$TMP/audio_drifted.wav"

# Mux composite video + drifted audio → final asset.
ffmpeg -y -i "$TMP/composite_video.mp4" -i "$TMP/audio_drifted.wav" \
    -map 0:v:0 -map 1:a:0 -c:v copy -c:a pcm_s16le -ar 22050 -ac 1 \
    -shortest "$OUT_DIR/clip.mov"

echo
echo "Ground truth (cutaway design):"
echo "  active_speaker_intervals (s): [0,7) [10,20) [23,30)"
echo "  cutaway_intervals (s):        [7,10) [20,23)"
echo "  drift_ms: ${APPLIED_OFFSET_MS}"
echo "output: $OUT_DIR/clip.mov"
sha256sum "$OUT_DIR/clip.mov"
ffprobe -v error -show_entries format=duration -of default=nw=1:nk=1 "$OUT_DIR/clip.mov"
