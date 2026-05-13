# Task: Cut the Birthday Song and Candle-Blow Segments

## Objective

A family videographer is given a single short birthday-party video at
`./assets/party.webm`. They need to deliver two highlight clips:

1. **The birthday song** — from the moment singing starts to the moment
   singing ends.
2. **The candle blow-out** — from the moment singing ends to the moment
   the last candle goes out.

Save:

- `./delivered/song.mp4` — the singing clip.
- `./delivered/blow.mp4` — the candle blow-out clip.
- `./delivered/boundaries.json` — the time ranges you used:

```json
{
  "song": {"start_sec": <float>, "end_sec": <float>},
  "blow": {"start_sec": <float>, "end_sec": <float>}
}
```

## Output format

Three files under `./delivered/`:

| File | Format | Notes |
|---|---|---|
| `song.mp4` | h264 + aac MP4 | duration = `song.end_sec − song.start_sec` |
| `blow.mp4` | h264 + aac MP4 | duration = `blow.end_sec − blow.start_sec` |
| `boundaries.json` | JSON | exact schema above; floats in seconds from the start of `party.webm` |

Each delivered MP4 must be the actual cut from `party.webm` at the time
range you reported in `boundaries.json` (the verifier compares audio
content, so renaming the source or cutting from a different window will
score 0).
