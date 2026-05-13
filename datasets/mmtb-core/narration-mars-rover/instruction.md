# Task: catch the on-screen claims that disagree with the narration

## Objective

You're doing QA on a captioned NASA Mars-rover panorama narrated video
(`./lecture.mp4`). The on-screen captions look fine at a glance, but a
few of them **disagree with what the narrator actually says** — wrong
number, wrong duration, wrong qualifier. Find every such span where the
visible caption disagrees with the audio and list it in
`./mismatches.json`.

The captioning vendor will be billed per region you flag, so don't
over-flag: spans where the caption matches the spoken narration should
not be reported.

## Output format

A JSON object at `./mismatches.json`:

```json
{
  "mismatches": [
    {"start_sec": 0.0, "end_sec": 0.0},
    {"start_sec": 0.0, "end_sec": 0.0}
  ]
}
```

- `start_sec` / `end_sec` are seconds from the beginning of the
  recording, as floats.
- `start_sec` must be strictly less than `end_sec`.
- The list length is between 0 and 20 (sanity cap).

## Notes

- The video is roughly 3:09 (189 seconds) long.
- A "mismatch" means the caption's text disagrees with the spoken
  narration in semantic content (wrong word, wrong number, etc.) — not
  minor transcription noise like missing punctuation.
- Each reported mismatch interval is matched against the gold via
  greedy IoU ≥ 0.5.
- Verifier scores via event-detection F1; pass at F1 ≥ 0.75.
