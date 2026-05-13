# Task: Pick the voice-over take matching the director's multi-dimensional brief

## Objective

You are a voice-over director's assistant. Eighteen takes of the same
scripted line are in `./assets/takes/` (`take_01.wav` through
`take_18.wav`). The scripted line is in `./assets/script.txt`, and the
director's scene brief — describing the delivery along several
dimensions — is in `./assets/brief.md`.

Exactly one of the 18 takes matches **all** of the dimensions in the
brief simultaneously. Select it.

## Output format

Write the chosen take's filename to `./selected_take.txt`:

- Exactly one line.
- Just the filename (for example `take_07.wav`) — no path, no extra
  fields.
- Case sensitive, lowercase `take_NN.wav` with `NN` in `01..18`.
