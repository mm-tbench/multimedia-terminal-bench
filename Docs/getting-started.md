# Getting Started

How to set up and run MultiMedia-TerminalBench.

---

## What is MMTB?

MMTB is a benchmark for terminal-based AI agents that must understand media content
(video, audio, images) and produce output artifacts. It runs on top of
[Harbor](https://github.com/harbor-framework/harbor), the framework behind
Terminal-Bench.

**What makes MMTB different from Terminal-Bench:** tasks require the agent to actually
perceive media content — watch videos, listen to audio, view images — not just
manipulate files with metadata tools.

---

## How it works

```
Harbor (framework)          MMTB (this repo)
─────────────────           ────────────────
Container lifecycle         Benchmark tasks with media assets
Agent scaffolds             Terminus-MM agent (multimedia perception)
Model API routing           Verifiers for artifact evaluation
Task execution              Media storage on HuggingFace Hub
```

Harbor provides the execution infrastructure. MMTB provides:
1. **Tasks** — in `datasets/mmtb-core/`, each with media files, instructions, and verifiers
2. **Terminus-MM** — a custom agent that extends Harbor's Terminus-2 with `watch_video`,
   `listen_audio`, and `view_image` tools
3. **Scripts** — for running tasks, managing media files

### How Terminus-MM perceives media

Terminus-MM is an external Harbor agent (runs on the host, connects to the container
via tmux). When the LLM calls `watch_video`, the agent:

1. Reads the file from the container via `docker exec base64 <file>`
2. Injects the raw bytes as a multimodal content block into the LLM conversation
3. The model (e.g. Gemini, Claude) perceives the video/audio/image natively
4. The model writes its analysis, then media is collapsed from context to save tokens

This means the **model itself** sees and hears the media — no transcription, no frame
extraction, no text summary. Native perception.

### Agent baselines

| Agent | Perception | Install | Use for |
|---|---|---|---|
| `terminus-mm` | Video + audio + image (routed) | This repo | Omni model with tools |
| `terminus-iv` | Image + video (no standalone audio) | This repo | Audio-perception ablation |
| `terminus-ia` | Image + audio (no video) | This repo | Video-perception ablation |
| `terminus-a` | Audio only (`listen_audio`) | This repo | Single-modality (audio) baseline |
| `terminus-kira` | Image only | This repo (vendored from [KIRA](https://github.com/krafton-ai/KIRA)) | Single-modality (image) baseline |
| `terminus-2` | None (text only) | Built into Harbor | Text-only baseline |
| `claude-code` | Own capabilities | Built into Harbor | Installed agent baseline |

---

## Prerequisites

- **Docker** — Harbor runs tasks in containers
- **Python 3.11+**
- **Harbor** — the benchmark execution framework
- **API key** — OpenRouter key (recommended) or direct provider key (Gemini, OpenAI, Anthropic)

---

## Setup

### 1. Install Harbor

```bash
uv tool install harbor
# or: pip install harbor
```

### 2. Clone this repo

```bash
git clone https://github.com/mm-tbench/multimedia-terminal-bench.git
cd multimedia-terminal-bench
```

### 3. Download media files

Media assets are too large for git. Each task has a `media.toml` that tracks
provenance (source URLs, licenses). The download script reads it and fetches
from HuggingFace Hub (the `mm-tbench/mmtb-media` mirror is public — no
token needed).

```bash
# Download all tasks' media
uv run python scripts/download_media.py

# Or download for a specific task
uv run python scripts/download_media.py av-desync-detection
```

No extra tooling required — all MMTB tasks source from license-free
content mirrored on the public HF repo, so `download_media.py` just
works.

### 4. Set up API keys

We recommend **OpenRouter** — one API key gives access to all models (Gemini, Claude, GPT, etc.).

```bash
cp .env.example .env
# Edit .env and add your OpenRouter key:
#   export OPENROUTER_API_KEY=sk-or-...
# Get your key at https://openrouter.ai/keys
```

Alternatively, set direct provider keys (`GEMINI_API_KEY`, `ANTHROPIC_API_KEY`, etc.).

---

## Running tasks

### Model provider format

Models are specified as `-m <provider>/<model>`. With OpenRouter, prefix with `openrouter/`:

```
openrouter/google/gemini-3.1-pro-preview        # Gemini via OpenRouter
openrouter/google/gemini-2.5-flash      # Gemini Flash via OpenRouter
openrouter/anthropic/claude-sonnet-4-6  # Claude via OpenRouter
gemini/gemini-3.1-pro-preview           # Direct Gemini API (needs GEMINI_API_KEY)
anthropic/claude-sonnet-4-6             # Direct Anthropic API (needs ANTHROPIC_API_KEY)
```

### With Terminus-MM (video + audio + image perception)

```bash
source .env
./scripts/run_terminus_mm.sh -p datasets/mmtb-core/<task> -m openrouter/google/gemini-3.1-pro-preview
```

`run_terminus_mm.sh` sets `PYTHONPATH` and runs
`harbor run --agent-import-path "mmtb_runtime.agent:TerminusMM"`.

### With Terminus-KIRA (image-only perception)

```bash
source .env
./scripts/run_terminus_kira.sh -p datasets/mmtb-core/<task> -m openrouter/google/gemini-3.1-pro-preview
```

KIRA is vendored in this repo — no separate install needed. It has `image_read`
but no video/audio perception. Good baseline for non-omni models.

### With Terminus-A (audio-only perception)

```bash
source .env
./scripts/run_terminus_a.sh -p datasets/mmtb-core/<task> -m openrouter/google/gemini-3.1-pro-preview
```

Terminus-A has only `listen_audio` — no `watch_video`, no `view_image`. It's
the audio-only counterpart to KIRA's image-only profile, useful for isolating
audio-perception contribution from full-omni performance. For video tasks the
agent must extract audio first via ffmpeg.

The other ablation harnesses (`terminus-ia`, `terminus-iv`) follow the same
pattern — see the agent baselines table at the top of this guide.

### With Claude Code (installed agent)

```bash
source .env
./scripts/run_claude_code.sh -p datasets/mmtb-core/<task> -m anthropic/claude-sonnet-4-6
```

Claude Code runs inside the container with its own capabilities. It cannot use
Terminus-MM's perception tools. Requires `ANTHROPIC_API_KEY` (OpenRouter not supported
for Claude Code — it uses the Anthropic SDK directly).

### With Terminus-2 (text-only baseline)

```bash
source .env
harbor run -p datasets/mmtb-core/<task> -a terminus-2 -m openrouter/google/gemini-3.1-pro-preview
```

The agent can only use terminal tools (ffmpeg, python, etc.) — no native media
perception. This is the baseline to compare against.

---

## Viewing results

Results are saved to `jobs/<timestamp>/`:

```bash
# Quick check: reward
cat jobs/<timestamp>/<task>__*/verifier/reward.txt

# Detailed verification
cat jobs/<timestamp>/<task>__*/verifier/details.json

# Agent trajectory (what the agent did step by step)
cat jobs/<timestamp>/<task>__*/agent/trajectory.json

# Cost and token usage
python3 -c "
import json
with open('jobs/<timestamp>/<task>__*/agent/trajectory.json') as f:
    t = json.load(f)
print(f'Cost: \${t[\"final_metrics\"][\"total_cost_usd\"]:.4f}')
print(f'Tokens: {t[\"final_metrics\"][\"total_prompt_tokens\"]:,} prompt')
"
```

Or use Harbor's result viewer:

```bash
harbor view jobs
```

---

## Available tasks

The benchmark contains 105 tasks across 16 fine categories / 5 meta categories. A few examples:

| Task | Media | What the agent must do |
|---|---|---|
| `av-desync-detection` | Real video clips (Blender open movies, CC-BY) | Find which clips have noticeable A/V desynchronization |
| `audience-ringtone-detection` | Real recital audio (PD: Beethoven, Mozart) | Sort recordings, finding the one with an audience cellphone ringtone |
| `travel-clip-retrieval` | Real travel video clips (CC) | Find the clip matching a described scene |

See `datasets/mmtb-core/` for the full set.

---

## Troubleshooting

**"Error: file not found" during Harbor run**
→ Media assets not downloaded. Run `uv run python scripts/download_media.py <task-name>`.

**"BadRequestError: video corrupted"**
→ The video segment may have encoding issues. Re-encode with:
`ffmpeg -i input.mp4 -c:v libx264 -c:a aac output.mp4`

**Agent keeps calling watch_video in a loop**
→ This was fixed in Terminus-MM. Make sure you're using the latest code.

**"20MB limit" error**
→ The model API has inline data limits. The agent should use ffmpeg to extract
shorter segments. This is the intended behavior for large files.

**Harbor not found**
→ Install with `uv tool install harbor` or `pip install harbor`.
