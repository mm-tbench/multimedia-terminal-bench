# Task: chapter a lecture into navigable sections

## Objective

You're a lecture-platform editor adding chapter markers to a 23-minute
lecture (`./lecture.mp4`) so viewers can jump between major sections.
Identify each chapter's start timestamp and write them to
`./chapters.json`.

## What counts as a chapter start

A chapter starts at any moment where the lecturer **pivots focus to a
substantively new area** — for example:

- The opening of the lecture itself (always counts).
- Moving between distinct theoretical frameworks or doctrines.
- Transitioning from abstract theory to a concrete, sustained case study
  (or vice versa).
- Beginning a structured walkthrough of a set of named sub-domains
  (treat the start of the walkthrough as one chapter — not each item
  within it).

Things that do NOT qualify as chapter starts:

- Brief asides, parenthetical remarks, or short illustrative examples.
- Mid-discussion slide flips that elaborate the same point (e.g.,
  zooming a mind-map node, advancing through bullets on one slide).
- Recurring rhetorical questions or summary recaps within an ongoing
  section.

A chapter spans at minimum tens of seconds of focused discussion; if you
find yourself flagging boundaries every few seconds, you are
over-segmenting.

## Output format

A JSON object at `./chapters.json`:

```json
{
  "chapter_starts_sec": [0.0, 0.0, 0.0, 0.0]
}
```

- `chapter_starts_sec` is a list of floats — the start time in seconds of
  each chapter, in increasing order.
- The first chapter MUST start at `0.0` (the beginning of the lecture).
- Subsequent chapter starts must be strictly increasing.
- The list length is between 1 and 12 (sanity cap).

## Notes

- The lecture is exactly 23:40 (1420 seconds) long.
- The video has both speaker close-ups and slide visuals.
- Both the audio and the visual stream carry chapter-boundary signals;
  the strongest boundaries have both.
- Each reported chapter start is matched against the gold within ±2.0
  seconds, so small detection-noise is tolerated; larger drift fails the
  match.
