# Task: Pick the dialogue take that matches the director's 4-turn plan

## Objective

You are a radio-drama director's assistant reviewing takes of a
short 4-turn dialogue between two characters, **Speaker A** and
**Speaker B**. The brief specifies three things:

1. **Per-turn emotion** — each turn is directed to carry a specific
   emotion.
2. **Per-turn speaker** — each turn must be delivered by the named
   speaker (A or B). Both actors speak multiple times; the
   assignment matters.
3. **Intra-turn stability** — each turn's emotion must hold from
   start to end, without drifting mid-turn.

Inputs:

- `./assets/brief.md` — per-turn (speaker, emotion) plan.
- `./assets/script.md` — the four dialogue lines with speaker tags.
- `./assets/takes/take_01.wav` … `take_10.wav` — ten candidate
  dialogue takes. Every take covers the same four turns in the same
  order; the candidates differ in emotion assignments, speaker
  assignments, and / or intra-turn stability.

Pick the take that satisfies **all three** requirements. A take
that hits the emotional arc but swaps which actor delivers a turn
does not satisfy. A take that holds every speaker and emotion
correctly but lets one turn drift mid-way through an emotion
transition does not satisfy either.

## Output format

Write the chosen take's filename to `./selected_take.txt`:

- Exactly one line.
- The literal filename, e.g. `take_03.wav` — no path prefix, no
  trailing slash, no other fields.
- Case-sensitive lowercase, `take_NN.wav` with `NN` in `01..10`.
