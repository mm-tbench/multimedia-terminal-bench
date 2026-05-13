# Edit Brief — *Houston, We Have a Podcast* ep. 414

**Client:** NASA Johnson Space Center / NASA Office of STEM Engagement
**Deliverable:** revised episode master with retro-fitted mid-roll spot
**Turnaround:** 48 hours

## Sponsor spot

`./ad_bumper.wav` is the pre-produced mid-roll, already voiced and mixed
to deliver at the client's target level. Place it as-is — do not trim or
re-pitch it.

## Placement

Insert the spot at the conversation's single major pivot from the
guest's personal context — her background, her prior research, her
methodology — to the broad framing question that motivates the rest of
the episode. This pivot occurs once and only once in the interview: it
is the first moment where the host stops asking the guest about
herself and starts asking about the subject matter the episode is
really about.

Place the spot in the short silence immediately before the host opens
that framing question, so the question lands cleanly coming out of the
sponsor break.

## Transitions

Apply a short crossfade at each side of the insertion so the sponsor
spot does not sit on a hard cut. Typical crossfade duration is
**100–500 ms** per side; use engineering judgment within that range.

## Loudness & mastering

The revised episode must meet the client's broadcast delivery spec:

- Integrated loudness: **−16 LUFS**, ±1 LU.
- True peak: **≤ −1 dBTP**.

Apply loudness normalization to the full finished episode (not just the
insert). Use any appropriate tool (ffmpeg `loudnorm`, Adobe Audition
Match Loudness, etc.).

## Delivery

- Final episode: `./episode.mp3`, encoded at **≥ 128 kbps** MP3.
- Edit log: `./edit_log.json` per the schema in `instruction.md`.

The original episode is not to be shortened or re-ordered elsewhere —
only the mid-roll insertion and the mastering pass are in scope for this
engagement.
