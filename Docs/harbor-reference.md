# Harbor Framework Reference

Compiled from official docs for MMTB integration. Sources:
- https://www.harborframework.com/docs
- https://github.com/harbor-framework/harbor
- https://github.com/harbor-framework/harbor-cookbook

---

## Installation

```bash
uv tool install harbor
```

## CLI: `harbor run`

```bash
harbor run -d "<org>/<dataset>@<version>" -a "<agent>" -m "<provider>/<model>"
harbor run -p "<path/to/local/dataset>" -a "<agent>" -m "<provider>/<model>"
harbor run -c "<path/to/job.yaml>"
```

### Flags

| Flag | Alias | Purpose |
|---|---|---|
| `--dataset` | `-d` | Published dataset from registry |
| `--path` | `-p` | Local task/dataset directory |
| `--agent` | `-a` | Agent name (terminus-2, claude-code, codex, etc.) |
| `--model` | `-m` | Model in `provider/model` format |
| `--n-concurrent` | `-n` | Parallel task count |
| `--env` | | Execution environment (daytona, modal, e2b) |
| `--task-name` | | Filter tasks by name glob |
| `--agent-kwarg` | | Typed kwargs to agent constructor (repeatable) |
| `--agent-env` / `--ae` | | Env vars to agent runtime (repeatable) |
| `--env-file` | | Load env vars from file |
| `--agent-import-path` | | Custom agent class (`path.to:Agent`) |
| `-c` | | Job config YAML file |

### Model backends (LiteLLM)

Harbor uses LiteLLM for model abstraction. Provider-specific env vars:

| Agent | API Key Var | Base URL Var |
|---|---|---|
| Claude Code | `ANTHROPIC_API_KEY` | `ANTHROPIC_BASE_URL` |
| Codex / QwenCode | `OPENAI_API_KEY` | `OPENAI_BASE_URL` |
| OpenHands | `LLM_API_KEY` | `LLM_BASE_URL` |
| Gemini CLI | `GEMINI_API_KEY` | — |
| Terminus-2/MM/KIRA | (LiteLLM auto) | `api_base` kwarg |

### OpenRouter (recommended)

OpenRouter provides a single API key for all models. Set `OPENROUTER_API_KEY` in `.env`
and use the `openrouter/` prefix:

```bash
# Terminus-MM + Gemini via OpenRouter
./scripts/run_terminus_mm.sh -p datasets/mmtb-core/<task> \
  -m openrouter/google/gemini-3.1-pro-preview

# Common model slugs:
#   openrouter/google/gemini-3.1-pro-preview
#   openrouter/google/gemini-2.5-flash
#   openrouter/anthropic/claude-sonnet-4-6
#   openrouter/openai/gpt-4o
```

**Note:** Claude Code does NOT support OpenRouter — it uses the Anthropic SDK directly
and requires `ANTHROPIC_API_KEY`.

For OpenAI-compatible endpoints (vLLM, Together, Fireworks):
```bash
harbor run -a terminus-2 -m openai/model-name \
  --agent-kwarg api_base=http://localhost:8000/v1
```

---

## Core Concepts

- **Task**: instruction + container environment + test script
- **Dataset**: collection of tasks
- **Agent**: program that completes tasks (BaseAgent or BaseInstalledAgent)
- **Trial**: one agent attempt at one task
- **Job**: collection of trials

---

## Task Format

### Directory layout

```
task-id/
├── task.toml           # Config and metadata
├── instruction.md      # Agent-facing instructions (markdown)
├── environment/
│   └── Dockerfile      # Container setup
├── solution/
│   └── solve.sh        # Reference solution (required)
└── tests/
    └── test.sh         # Verification entry point
```

Create with: `harbor init --task "<org>/<name>"`

### task.toml

```toml
schema_version = "1.1"

[task]
name = "org/task-name"
description = "Human-readable summary"
authors = [{name = "Name", email = "email"}]
keywords = ["tag1", "tag2"]

[metadata]
# Arbitrary — MMTB uses [metadata.mmtb]

[agent]
timeout_sec = 3000.0
user = "agent"

[verifier]
timeout_sec = 600.0

[environment]
build_timeout_sec = 600
cpus = 1
memory_mb = 2048
storage_mb = 10240
gpus = 0
allow_internet = true
env = {}
mcp_servers = []
```

### Special filesystem paths in container

| Path | Purpose |
|---|---|
| `/logs/verifier/` | Reward file and verifier logs |
| `/logs/agent/` | Agent runtime logs |
| `/logs/artifacts/` | Collected artifacts |
| `/solution/` | Copied solution folder |
| `/tests/` | Copied tests folder |

### Reward files

- `/logs/verifier/reward.txt` — single int/float (0 or 1)
- `/logs/verifier/reward.json` — structured JSON (floats/ints only)

Harbor prioritizes `reward.txt` over `reward.json`.

**Always write `reward.txt`** with the scalar success value — Harbor reads it first and uses it for trial pass/fail. `reward.json` is fine alongside it with multiple numeric keys for richer per-criterion metrics (every MMTB verifier writes `success` plus `eligible`, `unsupported`, and per-check booleans). Harbor's default cross-trial `mean` aggregator only handles single-key `reward.json` cleanly; if you intend to roll up across trials with that aggregator, keep `reward.json` to one key — otherwise stick with the multi-key MMTB convention and rely on `reward.txt` as the source of truth.

---

## Agents

### Types

1. **External agents** (BaseAgent) — interface via `exec()` on `BaseEnvironment`
2. **Installed agents** (BaseInstalledAgent) — deployed into container, run headless

### Agent interface

```python
class BaseAgent:
    def name(self) -> str: ...
    def version(self) -> str | None: ...
    def setup(self, environment: BaseEnvironment) -> None: ...
    def run(self, instruction: str, environment: BaseEnvironment, context: AgentContext) -> None: ...
```

**Key**: `run()` accepts `instruction: str` — text only. No multimodal input support in current Harbor.

### Terminus-2 kwargs

| Param | Default | Purpose |
|---|---|---|
| `model_name` | required | LiteLLM model ID |
| `max_turns` | 1000000 | Episode limit |
| `api_base` | — | Custom API endpoint |
| `temperature` | 0.7 | Sampling param |
| `reasoning_effort` | — | low/medium/high |
| `max_thinking_tokens` | — | Extended thinking (min 1024) |
| `parser_name` | "json" | Response parser |
| `enable_summarize` | True | Context compression |

### Built-in agents

terminus-2, claude-code, codex, gemini-cli, openhands, mini-swe-agent, qwen-code, cline, oracle

### Custom agents

```bash
harbor run -d "<dataset>" --agent-import-path my_module:MyAgent
```

---

## Datasets

### Local

```bash
harbor run -p path/to/dataset -a agent -m model
```

Harbor scans the directory for task directories.

### Publishing

```bash
harbor auth login
harbor dataset init "org/dataset" --description "..." --author "Name <email>"
harbor add path/to/task-a path/to/task-b
harbor publish path/to/dataset -t v1.0 --public
```

### dataset.toml

Created by `harbor dataset init`. References tasks by digest:

```toml
[[tasks]]
name = "org/task-name"
digest = "sha256:..."
```

### Running published

```bash
harbor run -d "org/dataset@v1.0" -a agent -m model
```

---

## Cookbook Recipes

| Recipe | Description |
|---|---|
| simple-task | Minimal single-container task |
| multi-container | Docker Compose + REST APIs |
| mcp-tools | Custom tools via FastMCP server |
| multi-reward | Multiple independent verifiers |
| simulated-user | Agents converse with simulated users |
| computer-use-ubuntu | Ubuntu virtual desktop |
| computer-use-windows | Windows desktop (Daytona) |
| dns-blacklisting | Network hostname blacklisting |
| skills | Agents access custom skills |

---

## MMTB Integration Notes

### What Harbor provides (don't reimplement)
- Container lifecycle, agent scaffolds, model backends (LiteLLM)
- Task discovery, trial execution, reward collection
- Artifact collection from `/logs/artifacts/`

### What MMTB adds
- `[metadata.mmtb]` in task.toml for benchmark-specific semantics
- Terminus-MM agent with native multimedia perception tools
- Tasks with real media assets requiring content understanding

### How multimedia perception works
Terminus-MM is a custom external agent (extends Terminus-2/KIRA) that adds
`watch_video`, `listen_audio`, `view_image` tools via native LLM tool calling.
Media is read from the container via `base64` and injected into the LLM conversation.
