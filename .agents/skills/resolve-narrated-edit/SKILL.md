---
name: resolve-narrated-edit
description: Building a narrated short video in the DaVinci Resolve MCP from a voice-over, an interview and B-roll, starting from an understanding of the material (asset descriptions, timed voice-over, storyline) so pictures match what is being said — a school report, event recap, vlog or explainer cut to an exact length (2:00), with background music ducked under speech, photos with zoom/pan, title cards, and portrait and landscape versions. Apply when asked to edit a video around a recorded voice-over or script, fit narration to an exact duration, add music with ducking, make a 9:16 and a 16:9 version, reframe landscape footage for vertical, stabilize phone footage, or apply a second round of fixes to a timeline the user has already edited.
---

# Resolve Narrated Edit
Turns a recorded voice-over (plus interview, B-roll, photos and music) into
**editable Resolve timelines** of an exact length, in portrait and landscape.
It starts by understanding the film (what every asset shows, what every
sentence says, what the story is); the mechanics below serve that.

Measured end-to-end on DaVinci Resolve **21.0.4.5 free, through the in-app
bridge**, 2026-11-08: a 2:00 school report (Dutch voice-over, a two-person
interview, 12 phone clips, 29 photos, one music track), three review rounds.
Rows marked with that build were measured there; re-confirm on another build
(`resolve_control api_truth` carries the canonical entries).

## The deliverable contract

- **Timelines in Resolve are the deliverable**, not an ffmpeg render. Do the
  analysis with whatever is fastest (ffmpeg, Whisper, OpenCV, contact sheets),
  but place real clips on real tracks so the user can trim, move and re-time in
  the Edit page. Pre-rendering every shot into a flat mp4 is quick and was
  rejected: nothing in it can be adjusted.
- Bake only what the API cannot express, each as its own file on its own track:
  the ducked music bed, a stabilized source, a title card with alpha, a blurred
  background. Name tracks for what they hold.
- Render with the user's preset, not hand-set codec values: load Resolve's
  **YouTube** preset with an **mp4** container (`render load_preset` /
  `prepare_render_job from_preset`). It gave smaller files at the same visible
  quality than custom H.264 settings.
- Exact length means exact: verify `GetEndFrame() - GetStartFrame()` and the
  rendered frame count (`render verify_output expected_frames`).

## Understand the film before you cut it

What made the edit work was not the API calls but knowing the material: what
each asset shows, what the voice-over says sentence by sentence, and what story
the piece tells. Build that understanding first, write it down as files the
user can read and correct, and make every editing decision from it.

- **The assignment and the script.** Read the brief (school assignment, client
  brief) and the recording script. They give the structure (sections, intended
  durations), the beat order, and often the intended picture per section
  ("[Picture: the dolls, then the room]"). Keep the script's section ids; they
  become the plan's ids.
- **Every asset, described in words.** Look at every file, not just its name.
  Contact sheets for videos (`video_review.py sheet`/`shots`), the photos
  themselves, OCR (`video_ocr.py`) for signs, labels and wall texts that name
  the work, the maker and the venue. Write one paragraph per asset (who and what
  is in it, where, framing, motion, quality, usable moments with times) to an
  `asset-descriptions.md`, in the user's language if they will review it. Mark
  inferences as such; a WhatsApp filename tells you nothing. Ask the user about
  what you cannot read (who is the designer, which room).
- **The voice-over, timed.** Transcribe every speech source, then keep a timed
  text (`timing.md`: `0:23.90–0:29.70 s2b <sentence>`) next to the plan. It
  shows the user where each line lands and is what you match pictures against.
  Whisper mishears names; correct them from the script, not the other way round.
- **The storyline.** State in a few lines what the film is about and how it
  moves (arrive → what caught my eye → the work → the maker in her words → my
  opinion → reflection). Each section gets its key picture: the work being
  discussed is shown while it is discussed, the maker is on screen when she
  speaks, the narrator opens and closes.
- **Match picture to sentence, not to section.** For each sentence, pick the
  asset whose content says the same thing ("moss and peacock feathers" → the
  close-up of the dress with feathers; "the snail" → the snail). Use the asset
  descriptions to find candidates, then the usable moment within the clip.
  Prefer variety: no clip twice in a row unless the story is about it.
- **Check the cut against the story.** Storyboard the plan
  (`video_review.py storyboard`), read it alongside the timed text, and fix
  mismatches before building anything in Resolve. After a render, look again at
  frames per sentence; "the picture shows the thing being said" is the test.

Keep these files (brief, descriptions, timed text, plan) in the project folder:
they are what the next round of edits, and the user, will work from.

## Workflow

1. **Inventory and probe.** `ffprobe` every file for duration, fps (phone video
   is VFR), `rotation`, and stream types. A file named "audio only" may still
   carry rotated video; a "voice-over" may be a phone video of the narrator.
   Transcribe a few seconds of anything ambiguous before deciding what it is.
2. **Transcribe with word timings.** Whisper `medium` on GPU,
   `word_timestamps=True`, `language` set, an `initial_prompt` listing the
   names and places in the script. `small` misheard the designer's name;
   `medium` got it. Keep the JSON: every later step cuts on word times.
3. **Cut speech into units** at sentence ends, a few hundred ms inside the
   silence (confirm with `silencedetect` or a 50 ms RMS envelope). Units from
   different recordings (narrator, interview) are interleaved per the script.
4. **Fit to the target length with pauses, not speed.** Fixed short pauses
   inside a section (0.7 s), a fixed tiny gap between interview turns (0.2 s),
   a lead-in (2 s) and tail (0.9 s); solve the **between-section** pause so the
   total is exact. Assert it is > 0.8 s; if not, drop a unit (the script
   should name the optional one) rather than squeeze.
5. **Plan the picture against the speech.** One list of `(tl_out, kind, file,
   src_in, note, crop)`, contiguous from 0 to the target. `kind` is `video`,
   `still`, or `sync` (picture follows a speech unit — the narrator on camera,
   the interview two-shot). Put the narrator on camera at the start, the end,
   and briefly between sections; B-roll everywhere else, chosen per sentence.
   Split `sync` segments at speech-unit boundaries but merge pieces shorter
   than ~0.4 s into the neighbour — a 2-frame flash is a bug. Every cut
   inside one talking-head take is a **jump cut**: handle it (next section).
6. **Interview sync.** If picture and sound are separate files, find the offset
   by cross-correlating loudness envelopes (10 ms RMS, band-passed). If the user
   then nudges the audio on the timeline, read the item positions back and use
   **their** offset from then on.
7. **Prepare media.** Conform VFR phone video to CFR at the timeline rate
   (`fps=24`, rotation applied) so source frame n is timeline frame n. Bake
   **stills to short clips** (see traps). Copy numbered photos to un-numbered
   names before import.
8. **Build stems.** Voice: each unit loudness-matched (-16 LUFS per source,
   high-pass, light denoise on the interview), 12 ms fades, placed on a 48 kHz
   canvas. Music: `scripts/duck_music.py` with the speech intervals. Normalise
   voice + music to -16 LUFS with a look-ahead limiter on the voice only, so the
   bed level does not move with the speech level.
9. **Assemble** with positioned `clip_infos` in one `append_to_timeline` per
   track set, `end_frame` exclusive. Then: Pan per item for reframes,
   `fusion_kenburns.py` for stills, stabilization, and a title card on its own
   track.
10. **Verify from Resolve's output**, not from readback: export frames
    (`timeline_markers set_current_timecode` + `project_settings
    export_frame_as_still`) at the title card, a reframed shot and a still
    (start vs end), and a contact sheet of the render (`video_review.py sheet`).

## Jump cuts in talking heads

Cutting pauses or sentences out of one take of a face leaves small jumps: the
head shifts a few pixels, the expression snaps. Small jumps on a face look like
errors (user feedback on the 2026-11-08 edit, where the narrator and interview
shots kept them). For every cut inside the same take, in this order:

1. **Cover it with B-roll.** Keep the audio continuous and lay a B-roll shot
   over the cut on the track above, starting a little before and ending a
   little after it (≥ 1.5 s). Choose B-roll that matches what is being said;
   this is the standard fix and usually the best one.
2. **Switch to another angle.** If a second camera or a different framing of
   the same moment exists (the interview recorded from two phones, a wide and a
   close shot), cut to it at the join.
3. **Make the jump deliberate.** If neither exists, change the framing enough
   that it reads as a new shot: a clearly different zoom (≥ 15–20 %) and/or
   position on the side after the cut (punch-in). Alternate between two framings
   across successive cuts. A 2–5 % change looks like a mistake; a big one
   looks intentional.

Find them before building: list every pair of adjacent `sync` items from the
same source (`timeline_diff.py`/the plan), and check each with an exported
frame either side of the cut. Cuts *between* different shots or sources need
none of this.

## Portrait (9:16) and landscape (16:9) from one plan

Keep one plan; derive both timelines from it.

- **Portrait from landscape sources:** timeline `timelineInputResMismatchBehavior
  = scaleToCrop`, then Pan per item. Choose the centre per shot with
  `video_review.py reframe <clip>` (where motion is), and for a two-person
  interview pick each speaker by eye on exported frames and switch on who
  speaks (split items at turn changes). Pan units on scale-to-crop are not
  pixels (see traps) — calibrate once per timeline.
- **Landscape with portrait sources:** `scaleToFit`, and a **blurred fill** of
  the same shot on the track below (the user liked this better than black bars
  or a crop that loses the subject): bake per source with
  `scale=960:540:force_original_aspect_ratio=increase,crop=960:540,boxblur=20:3,eq=brightness=-0.08`
  (half resolution is plenty under a heavy blur). Same record/source frames as
  the sharp clip so they stay in step. Native 16:9 shots get no background.
- Title cards: size the box to the measured text (`ImageDraw.textbbox`) with
  padding, and break lines for portrait. A fixed-size box overflowed when the
  font changed. ProRes 4444 `.mov` keeps the alpha.

## Applying fixes to a timeline the user has edited

The user will edit between rounds. **Never rebuild a timeline they touched.**

1. `scripts/timeline_diff.py "<timeline>" clip-infos.json` — lists moved,
   trimmed, deleted items and disabled tracks. A disabled title track is a
   decision; leave it off and say so.
2. Bake replacements with the **same frame count**, then
   `MediaPoolItem.ReplaceClip(path)` on the pool clip. Every item using it
   keeps its position, trims, Pan and Fusion comp. Only touch pool clips whose
   file path belongs to this edit — an older timeline may share a clip *name*.
3. After `ReplaceClip` the pool clip is renamed to the new file; match by file
   path from then on.
4. Re-run the diff: the only change should be the media names.

## Verified traps

| Trap | Confirmed on | Symptom | Fix |
|---|---|---|---|
| Stills ignore `startFrame`/`endFrame` in `AppendToTimeline` | 21.0.4.5 free | Every photo lands 120 frames long and overlaps the next clip | Bake to a 24p mp4 (`ffmpeg -loop 1 -framerate 24 -t 8`), import, trim like video |
| Numbered photos import as one image sequence | 21.0.4.5 free | Five `IMG (1..5).jpeg` become one 5-frame `IMG ([1-5]).jpeg` | Copy to un-numbered names first; compare returned clip count to paths sent |
| `Pan` on a scale-to-crop timeline | 21.0.4.5 free | `set_transform pan_px` pushed the subject out of frame; Pan +100 moved 166 px left on an 848x480 source in a 1080x1920 timeline | Calibrate with two exported frames; frame by eye |
| `DynamicZoomEnabled` and Edit-page keyframes | 21.0.4.5 free | `SetProperty` returns False; `KEYFRAME_API_UNAVAILABLE` | Fusion Transform keyed through `comp.Execute` — `scripts/fusion_kenburns.py` |
| Fusion keys over the bridge | 21.0.4.5 free | `add_keyframe` / expressions: "'BridgeProxy' object is not subscriptable" | Lua via `comp.Execute`, no `Lock()`, tool pre-created from Python, keys at `GetLeftOffset()`-based source frames |
| `Stabilize()` runs the default mode | 21.0.4.5 free | Visible warping on phone footage; no option to pick Translation | `scripts/stabilize_ts.py` (translation + scale, per shot, fixed zoom) + `ReplaceClip`; `ResetAllGrades` removes Resolve's pass |
| Ducking with a fixed release | 21.0.4.5 free | Music swells for a split second between sentences | `duck_music.py --hold 1.0`: stay down through pauses < 1 s; eased curve |
| `timeline_frame capture` returns nothing in some clients | 21.0.4.5 free | Empty tool result | `set_current_timecode` + `export_frame_as_still`; it fails during and right after a render |
| `export_frame_as_still` after `set_current` of another timeline | 21.0.4.5 free | Returns False | Open the Edit page and retry, or check the rendered file instead |
| `prepare_render_job` outside temp | 21.0.4.5 free | "target_dir must be under the system temp directory" | `require_temp_target: false` |
| `add_subfolder` fails without a reason | 21.0.4.5 free | "Failed to create subfolder" with a valid name | Import into an existing bin; organise later |
| `AppendToTimeline` `endFrame` | 21.0.4.5 free | Passing an inclusive end left the timeline one frame short (2879) | Exclusive: `end = start + duration`, same as the MCP tool |

## Scripts

All in `scripts/`, source-safe (read sources, write only the named output):

- `video_review.py shake <clip>` — jitter per window; ≥ 2 px @480 is worth stabilizing.
- `video_review.py reframe <clip> --aspect 9:16` — crop centre per shot, with boxes drawn.
- `stabilize_ts.py in out` — translation + scale stabilization, same frame count.
- `duck_music.py music speech.json out.wav --length 120` — baked ducked bed.
- `fusion_kenburns.py plan.json` — keyed Fusion zoom/pan on items (bridge-safe).
- `timeline_diff.py "<timeline>" clip-infos.json` — what the user changed.

## Source-media safety (AGENTS.md)

Conforms, stills-as-clips, blurred fills, stabilized bakes and stems are new
files under a scratch/edit folder, never beside or over the source. Swapping
media under items with `ReplaceClip` changes the project, not the source files.
