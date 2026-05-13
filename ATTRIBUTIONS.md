# Attributions

MMTB task media is sourced from openly-licensed works. This file records
attribution as required by the licenses. Per-file provenance (source URL,
license text, sha256) is recorded in each task's `media.toml`; this file
is the human-readable aggregate.

## Path conventions

Files referenced below live under `mmtb-core/<task>/environment/assets/`
in this Hugging Face dataset, and under
`datasets/mmtb-core/<task>/environment/assets/` in the companion code
repository. The companion code repository hosts the per-task `media.toml`
files that record the canonical source URL, license, and sha256 for every
file.

---

## CC-BY 3.0 — attribution required

### Blender Foundation open movies

Used in `av-desync-detection`. Derivative clips (60 s segments,
re-encoded H.264 / AAC, with controlled audio-video modifications applied
for benchmark evaluation) are distributed under the same CC-BY 3.0
license.

- **Sintel** (2010) — © Blender Foundation / durian.blender.org. Licensed
  under CC-BY 3.0. Source: <https://archive.org/details/Sintel>. Clips:
  `sintel_180_240.mp4`, `sintel_480_540.mp4`.
- **Tears of Steel** (2012) — © Blender Foundation / mango.blender.org.
  Licensed under CC-BY 3.0. Source:
  <https://archive.org/details/Tears-of-Steel>. Clips:
  `tears_of_steel_120_180.mp4`, `tears_of_steel_300_360.mp4`.
- **Elephants Dream** (2006) — © Blender Foundation /
  orange.blender.org. Licensed under CC-BY 3.0 (US). Source:
  <https://archive.org/details/ElephantsDream>. Clip:
  `elephants_dream_30_90.mp4`.
- **Big Buck Bunny** (2008) — © Blender Foundation /
  peach.blender.org. Licensed under CC-BY 3.0. Source:
  <https://archive.org/details/BigBuckBunny_124>. Clip:
  `big_buck_bunny_60_120.mp4`.

License text: <https://creativecommons.org/licenses/by/3.0/>.

---

## CC-BY-SA-4.0 — attribution + share-alike required

### Wikimedia Commons (Category: Receipts)

Used in `receipt-photo-to-json`. The task redistributes printed-receipt
images from Wikimedia Commons. Each redistributed image carries
attribution to its original Wikimedia uploader as recorded in the task's
`media.toml`. Derivative work in this dataset is distributed under the
same CC-BY-SA-4.0 license.

- Source category:
  <https://commons.wikimedia.org/wiki/Category:Receipts>.
- License text: <https://creativecommons.org/licenses/by-sa/4.0/>.

### Other Wikimedia Commons content

Wikimedia Commons content (CC-BY family or Public Domain depending on the
file) appears in additional tasks. Per-file source URL, original
contributor, and license string are recorded in each task's `media.toml`.

---

## ODbL-1.0 — attribution + share-alike for derived data

### CREMA-D (Cao et al. 2014)

Three tasks redistribute specific actor recordings from CREMA-D as audio
bytes:

- `debate-attribution`
- `prosody-take-selection`
- `prosody-multi-dim-selection`

Five additional tasks reuse only sentence text (without the audio) from
the corpus, without redistributing the actor recordings.

Citation: H. Cao, D. G. Cooper, M. K. Keutmann, R. C. Gur, A. Nenkova,
and R. Verma. "CREMA-D: Crowd-sourced Emotional Multimodal Actors
Dataset." *IEEE Transactions on Affective Computing*, 5(4): 377–390,
2014.

- Repository: <https://github.com/CheyneyComputerScience/CREMA-D>
- License text: <https://opendatacommons.org/licenses/odbl/1-0/>

Specific actor IDs reused per task are recorded in each task's
`media.toml`.

---

## Public Domain — credited for provenance (no attribution required)

### LibriSpeech / LibriVox

Five tasks reuse audio segments from LibriSpeech test-clean (originating
from LibriVox volunteer-reader recordings, explicitly contributed to the
Public Domain by the readers):

- `2-speaker-diarized-transcript-from-podcast-audio`
- `adr-edit-detection`
- `dead-air-removal`
- `dub-speaker-mismatch`
- `speaker-roster-identification`

Citation: V. Panayotov, G. Chen, D. Povey, and S. Khudanpur.
"LibriSpeech: An ASR corpus based on public-domain audiobooks." In *2015
IEEE International Conference on Acoustics, Speech and Signal Processing
(ICASSP)*, 2015.

- Corpus: <https://www.openslr.org/12>
- LibriVox volunteer-reader source: <https://librivox.org/>

Audio is Public Domain via LibriVox; corpus segmentation/metadata is
CC-BY-4.0. Specific reader IDs reused per task are recorded in each
task's `media.toml`.

### audience-ringtone-detection

Five historical recordings of Public Domain classical works from
archive.org. One of the released clips contains a derivative mix with a
synthesized telephone ring tone overlay; the specific overlay target is
not disclosed in this attribution document.

- Beethoven, Piano Sonata No. 8 *Pathétique* —
  <https://archive.org/details/BeethovenPianoSonataNo.8>
- Beethoven, Piano Sonata No. 21 *Waldstein* —
  <https://archive.org/details/BeethovenPianoSonataNo.21Waldstein>
- Beethoven, Piano Sonata No. 27 —
  <https://archive.org/details/BeethovenPianoSonataNo.27>
- Beethoven, Piano Sonata No. 32 —
  <https://archive.org/details/BeethovenPianoSonataNo.32>
- Mozart, Piano Sonata K.333 (Uchida) —
  <https://archive.org/details/mozart-piano-sonata-no.-13-in-b-major-k.-333-uchida-20250127022911111-mozart-pia>
- Mozart, Violin Sonata No. 2 K.7 —
  <https://archive.org/details/K.7ViolinSonataNo.2InD>

### travel-clip-retrieval

Ten short Public Domain travel clips from the archive.org
`world-travel-videos` collection:
<https://archive.org/details/world-travel-videos>. Per-clip source URLs
are in `mmtb-core/travel-clip-retrieval/media.toml`.

### NASA multimedia

NASA mission audio and other NASA-produced multimedia are Public Domain
under U.S. federal-government works. Specific NASA-sourced files are
identified in each consuming task's `media.toml`.

- Source: <https://www.nasa.gov/multimedia/>

### archive.org other public-domain content

Additional Public Domain audio recordings from archive.org (historical
speeches, concert recordings, vintage shorts) appear in other tasks.
Per-file source URL is recorded in each task's `media.toml`.

---

## CC0-1.0 — no attribution required (credited for provenance)

### Kenney game-asset packs

Game-task graphics, sprites, and sound effects from
<https://kenney.nl/assets> are released under CC0-1.0 (Public Domain
Dedication) by Kenney.nl. Specific pack names are recorded in each
consuming task's `media.toml`.

### Quaternius 3D models

CC0 3D models from <https://quaternius.com/>. Specific model names are
recorded in each consuming task's `media.toml`.

### OpenGameArt CC0 collection

CC0 game assets from <https://opengameart.org/>. Specific asset URLs are
recorded in each consuming task's `media.toml`.

### Freesound CC0 sound effects

CC0 sound effects from <https://freesound.org/>. Specific clip URLs are
recorded in each consuming task's `media.toml`.

License text: <https://creativecommons.org/publicdomain/zero/1.0/>.

---

## Apache-2.0 — Kokoro-82M TTS

Kokoro-82M (Apache-2.0) is used as the text-to-speech engine for the
benchmark's spoken-content synthesis. Synthesised speech files are
distributed as Apache-2.0 byte outputs.

- Model: Kokoro-82M (Apache-2.0).
- License text: <https://www.apache.org/licenses/LICENSE-2.0>.

---

## Non-commercial research-use license — Wav2Lip

A single task (`debate-attribution`) uses Rudrabha/Wav2Lip for lip-sync
video synthesis. The Wav2Lip distribution is licensed for
non-commercial research use only; the lip-synced video output
redistributed in this task therefore inherits the non-commercial
research-use restriction.

- Source: <https://github.com/Rudrabha/Wav2Lip>
- License terms: see the LICENSE file in the upstream repository.

The redistributed file is recorded with
`license = "non-commercial-research-only"` in
`mmtb-core/debate-attribution/media.toml`.

---

## GFDL 1.2 — legacy Wikimedia items

Two files originate from Wikimedia Commons items released under GFDL
1.2; these carry GFDL attribution and copyleft requirements. Per-file
attribution and source URL are recorded in the consuming task's
`media.toml`.

- License text: <https://www.gnu.org/licenses/old-licenses/fdl-1.2.html>.

---

## GPL family — engine / font derivatives

A small number of files are produced by GPL-licensed tooling (e.g.
LilyPond GPL-3.0+ for music engraving, certain `--enable-gpl` ffmpeg
build paths, GPL-licensed engine/font derivatives) and inherit GPL
copyleft. Per-file license string is recorded in the consuming task's
`media.toml`.

- LilyPond license text:
  <https://www.gnu.org/licenses/old-licenses/gpl-2.0.html> /
  <https://www.gnu.org/licenses/gpl-3.0.html>.

---

## Author-contributed media

A small number of clips and one human-recorded voice are contributed by
the MMTB authors under explicit licenses (CC-BY-4.0 or MIT). Each such
file is flagged in the consuming task's `media.toml` with the explicit
license string. Examples include:

- Tokyo train footage in `stereo-channel-flip-repair`.
- A self-recorded learner-speech clip in
  `pronunciation-error-flagging`.

---

## Where to find the per-file canonical record

The canonical per-file `media.toml` manifest lives in the companion code
repository at `datasets/mmtb-core/<task-id>/media.toml`. Each
`[[media.entries]]` block records `source.url`, `source.license`,
`source.description`, and `output.sha256`. The `fetch_media.py` resolver
enforces sha256 equality at build time. This Hugging Face dataset hosts
the media bytes referenced by those manifests, mirrored under
`mmtb-core/<task-id>/environment/assets/`.

For the aggregate license-family summary, see
[`LICENSE_MATRIX.md`](LICENSE_MATRIX.md).
