# text-erase-worker

Erases burned-in text from a video **without generated pixels** and lays a new text layer on top. Runs one job per
GitHub Actions run (`.github/workflows/erase.yml`), started by the caller's automation through the API.

## The math (one frame)
1. **Mask per letter, only inside known text zones** (times + rects): light letters = morphological top-hat
   `I − open(I, k)` > thr and `I` > lmin; dark letters = black-hat > thr; kernel k wider than the stroke; dilate by
   `grow` px for outline / shadow / glow. Mode `full` erases the whole rect (an old opaque box or a translucent plate).
2. **Fill = normalized convolution, push-pull** (Gortler et al., «The Lumigraph», pull-push): weights `w = 1 − M`; a
   pyramid down `num = G * (I·w)`, `den = G * w`; at each level `F = num / den`, back up blending by
   `a = clip(den / 0.35)`. A letter becomes the blur of the background right next to it; no letter colour leaks in.
3. **Edge**: `out = I·(1 − Mf) + F·Mf`, `Mf = clip(G(M, 1.5 px)·1.6)`.
4. **Video**: the per-frame mask is unioned over ±2 frames of the same shot (never across a cut); a zone marked static
   erases, in every frame of its window, each pixel that is a letter in ≥ `static` share of the window's frames.

### Text that is erased another way
- **Outlined captions** (zone mode `cap`, `engine/captions.py`): a top-hat mask needs letters that stand out of their
  surround, so white letters over a white coat or a white app page were missed. An outlined letter does not depend on
  the background: its fill is an island of the fill colour whose rim is the dark outline all the way round. Islands
  as tall as a capital, two or more in a row, are a text line; punctuation joins glyph by glyph (a «?» hook or a «!»
  bar shorter than the capitals too). A white coat, a pearl, a radio button are islands too, but nothing dark rings
  them or they do not line up; the white counters of an app page's own dark words under a caption are ringed, so
  between the capitals only the capitals, their own counters and punctuation are the caption's. The spoken word in another colour is a caption letter too: its fill colour is measured (`cap.colors`).
  The letters' soft shadow is not blurred away: it is measured on the frames whose surround is flat and
  bright (the shadow = the text shape, blurred, moved down, at a strength) and **divided out**, so the picture keeps
  its detail right up to the letters and only fill + outline are filled. Dark pixels just past the outline (the tip
  of an A or V) join the fill only when their dark run ends within 9 px of the outline: an app page's own words and a
  cup's rim reach further and stay. On textured ground (knit, foliage) the filled letters still show as a soft patch:
  the fill uses only the pixels around them, and mirrored detail, a matched copy of the ground nearby and a fill from
  neighbour frames along the optical flow all looked worse on a known patch (2026-11-15). So the patch's edge is
  feathered (`captions.FEATHER`, 18 px): the ground next to the hole fades into a blurred copy, so the patch is a
  little wider but diffuse instead of letter-shaped; past 18 px nothing changes, and dark page words next to the
  letters stay sharp. Outlined caption lines only: a still headline keeps the plain edge next to kept page elements.
  The patch also changes over time without a jump: when a line's text leaves (a shorter next line, a pause) its
  patch stays and fades out over 20 frames (0.67 s), and the patch of a coming line fades in over 20 frames before its
  text shows (`captions.FADE`; `plate.py` reads the letters and hole of every caption frame first, then fills each
  frame with the holes of the frames around it, never across a cut). Filled frame by frame, the patch jumped in size
  whenever the old caption changed length.
- **A text that stays through many shots** (the legal line, `still` in ZONES.json): its letters are found once, on the
  shots where they stand out, and that one mask erases the line in every frame of its time. On a white page such a
  line is only its grey shadow, and a zone measured on that shot alone found dark specks or nothing.
  A **light-lettered block that stays in one place** (a headline, a call to action outside the end card, a second or
  longer, its reads agreeing, no move over 6 px) is erased the same way: a zone per shot took only the letters that
  stood out on that shot, grown a few px, so over a white wall half of the letters stayed and on every ground the
  letters' dark shadow stayed as a row of specks or as a readable grey word. Its mask also takes near-white fill
  on bright ground (a second, low threshold, and the lowest gate of its shots), and what the build keeps inside its
  rect is put back as it was after the fill. The fill reaches 12 px (outline and shadow), so the pass for dark pixels
  past it is off (it took picture lines: a frame bar, a pill's edge); the crop has 60 px of context on every side; the
  R frames next to the block's times are erased too when half of its mask stands out there.
  The block keeps zones per shot instead when: a read names a plate behind it, its reads disagree on any side, a kept
  element shows on some of its shots only, its letters are dark on a shot, or its letters come or go inside its time
  (on some frame the letters beyond the mask's reach are over 15 % of the mask: a line that comes in late, a word
  that changes; `still_mask_ok()`). `cfg.auto.still_refused` says why. The check counts a block as left when 40 % of
  its mask still stands out, when letters beyond the mask's reach (over a quarter of it) stand out of both the source
  and the plate, or when its mask came out empty (every frame of its time).
- **A translucent caption plate** (zone mode `exact`): the whole plate with its letters, on its page's own frames
  only (a `full` zone is also erased on the 2 frames before and after it).

## Checks that stop a job (`worker/run_job.py`)
A job that would leave old words on screen stops with an error instead of delivering a file:
- **caption coverage**, right after «measure zones»: `cfg.auto.captions` holds, per caption block, the share of the
  frames that show caption letters and are under an erase. A block under one half = «captions not covered» (by block
  time, when no ringed letters can be counted: under one fifth, since a caption that comes and goes leaves its block
  half empty). A caption the caller's own stills show with no erase within 0.3 s of it makes its block uncovered
  (`reads_not_erased`: captions that sit in other rows on one shot, a page the measure missed). A source whose spoken
  word sits on a coloured box of any strong colour is named and counts as uncovered (this engine does not erase that
  box). A frame counts as «shows caption letters» only when its bright pieces look like letters: a white garment in
  the caption rows is bright too, but one large piece.
- **a block nobody could place**, right after «measure zones»: a translated block with no read, no covered band and
  no box of its own (`cfg.auto.unplaced`) would keep its old text under the new one.
- **letters left on the plate**, after «check plate»: an outlined caption line found on any plate frame, a single
  outlined glyph left whole inside a source caption line on 2 or more sampled frames (`captions.left_glyphs`: what a
  line search on the plate cannot see), or the letters of a `still` text still standing out on 3 or more sampled
  frames.
- **letters left outside the captions**, after «check plate»: a top-hat zone (a headline, a hook card, a call to
  action) whose old letters stand out of the plate in 15 or more frames (`residue_stop`; spec `zoneResidueFrames`).
  Fewer frames stay a note in the job record, since this check also fires on textured ground: when the stop was added,
  clean plates had at most 8 frames in a zone and plates that kept a half-erased headline had 17 to 370.
A spec may set `allowUncovered` / `allowResidue` for a hand check.
Not caught yet: a caption page of one letter, or one that no still of the caller shows and that is much larger than
the others; a text that slides in (its zone does not follow it, so the old words show for a frame or two); a
box-style caption whose rect starts late after a cut (rect zones are not checked on the plate).

## Composite without an alpha video (difference matting)
The overlay-only render body is rendered twice, over solid black (B) and solid white (W). Per channel
`α = 1 − (W − B)/255` and colour·α = B, so `final = plate·(1 − α) + B`; where nothing is drawn the plate pixel is kept
exactly. Caption pages can be drawn locally with the real font (`composite.py` CAPTIONS.json) so box and words always
come from the same page.

## Files
| | |
|---|---|
| `engine/auto_zones.py` | the caller's text analysis of this build → the cfg, measured on the source (step «measure zones») |
| `engine/build_math.py` | render body + cfg → erase zones (`ov.zones.json`), overlay bodies over black / white, caption pages |
| `engine/plate.py` | the plate (erased video); `--check N` = letters still standing out + blur where no text was |
| `engine/captions.py` | outlined caption letters: find, erase, the shadow fit |
| `engine/layers.py` | what the render body's layers show (kept source layers) |
| `engine/composite.py` | plate + B / W overlay renders (+ local caption pages) → final mp4, audio from B |
| `engine/measure.py`, `segs.py`, `track.py`, `splice.py` | zone measuring, a tracked moving element, re-made frame ranges |
| `worker/run_job.py` | one job end to end (spec contract in its docstring) |
| `engine/endcard.py`, `worker/endcard_job.py` | a second job: a dark end card at the end of a video → a new card (below) |
| `engine/staticfix.py`, `worker/static_job.py` | a third job: a still image read, fixed in regions, checked (below) |
| `engine/audiomix.py`, `engine/stills.py`, `worker/media_job.py` | media jobs off Creatomate's queue: a Creatomate audio body mixed, or a batch of audio / still bodies, with ffmpeg (below) |
| `tests/` | unit tests on drawn frames, no media: `python -m unittest discover -s tests` (workflow `test.yml`, no secrets) |

## Layers in the cfg: name them, don't number them
Two builds of the same video number their layers differently (one more band shifts every track after it), so a cfg
measured on one build breaks on the next if it names layers by track number. Every layer reference in the cfg
(`drop_text_tracks`, `drop_band_tracks`, `dark_text[].tracks`, `caption_text_track`, `caption_pages.track`, keys of
`text_set` / `text_y_keys`) takes a number (that body only), `"transcript"` (the caption layer), `{"name": "…"}` or
`{"text": "starts with …", "time": t, "named": true|false}`; `build_math.py` resolves them against the body it is given
and stops with the reference that matched nothing (or more than one layer where one is needed).
The same build drift can move what the new version keeps of the source (an «orig …» layer of source pixels) onto a
zone the plate erases, which would bring the old letters back: `build_math.py` stops then too («the cfg does not fit
this build»), and the reason reaches the job's private error field. That stop is only for a cfg written for another
build: `run_job.py` passes `_measured` (the cfg keys `auto_zones.py` measured on this build), and a kept layer under
one of those zones (the legal line's one letter mask and outlined-caption bands included) is left out for the zone's
time instead (± 3 frames), since the old letters must go and outside the
zones the plate is the source itself (2026-11-28: an old two-line caption plate ran into a kept promo tape's
rows). Kept layers lie under our caption boxes, as in the Build.

## Automatic zones (`engine/auto_zones.py`)
A spec with `auto` (the caller's per-block text analysis: blocks with role / time / rough box, exact boxes read on
stills, shot changes, what the build covers) gets its cfg measured on the source instead of written by hand:
- **Caption boxes**, frame by frame at 10 fps: a box = a rectangle of pure black (max channel ≤ 3) whose columns stay
  black in most rows of the band and whose rows are at least a quarter black (letters cover less); a column without
  gap bridging, so the box's antialiased edge parts it from a dark coat right next to it, and margin rows ≥ 0.85
  black fix its top and bottom. No box: bare light letters (a top-hat > 60 over their surround, so a flat cream wall
  is not text) that fill ≥ 12 % of their bbox, ≤ 2 lines tall, not cut by the frame edge.
- **Translucent caption plates** (the reads name a dark plate that is not pure black): such a plate darkens the picture
  behind it, so its pixels move with the background (one test video: 49 over a white app screen, 30–40 over a floor,
  under 12 over dark trousers) and no fixed colour finds it. Measured on every frame of the source, letters first:
  bright islands that pixels as dark as the plate enclose (how dark = the ratio plate / background at the plate's
  edges on the read stills, taken over white, + 12), kept while their bounding rectangle holds nothing but plate and
  letters. Each edge is the first line past the letters that is not dark. A page = the frames whose letters stay; its
  edge is the innermost one any of its frames saw. An edge hidden by something as dark as the plate (trousers, a
  coat, a plant beside it) takes the padding of the opposite side, the same on a plate, else the video's median
  padding. One rect per page, kind `P`, from the page's first frame to its last; `build_math.py` erases the whole
  old plate for that time (the build's new plate is translucent too and would show the old letters through) and
  takes the build's own dark plates (`rgba(r,g,b,a)`, any darkness, not only black) as the new boxes. No
  `caption_pages` for such a source. When the pages do not agree on a plate's padding (bare letters over a dark
  part of the picture have next to none), the reads were wrong and the measures above run as without them. Not
  measured: a plate lighter than mid-grey; a one-letter page whose plate is hidden on two sides; a last word in a
  colour much darker than the other letters (the rect ends before it).
- **The new caption box**: height = the measured one-line boxes (p10 / p90, +0.25 %), radius ≤ the old box's corner
  (measured) and ≤ the build's own plate, `old_boxes` «all» only when no bare-letter page was seen.
- **Every other block, per shot**: mode, kernel, threshold and brightness gate from the pixels inside its read box
  (polarity: the extreme further from the background). White letters partly over a light background get a second
  zone for the near-white fill (threshold 12, gate ≈ fill − 17): there they stand out by only ~25. The legal line is
  light when its brightest pixels rise ≥ 14 above the background (white letters with a dark outline), else dark
  (grey letters on a light page); colour reads are not used there, they name the outline colour. A light legal line
  takes gate 150 and grow 4: its outline / drop shadow sits up to 4 px past the fill, and a gate set from the
  background (cream: 241) kept only the core of each letter, so the shadow's bottom row stayed as a dotted line.
- **Caption styles, in this order**: a dark translucent plate (when the reads name one) → black boxes and bare light
  letters → outlined captions (when under a fifth of the caption frames hold a black box and a line is found in at
  least half of them). Outlined captions get one `cap` zone per run of caption blocks, `cfg.cap` (capital height,
  outline reach, shadow model) and the look of the new caption layer through `text_set` (size and row from the
  measured lines, the house caption font, the house outline and shadow when the measured shadow is the house one,
  else the measured ones). One stray caption read far from the others no longer stretches the caption rows.
- **Zone ends**: a block's times come from stills, so its zone runs to the shot change within 0.2 s and to the last
  frame of the video within 0.5 s. While an end-card line comes in (0.15 s before to 0.45 s after its start) over
  smooth ground, its whole rect is filled.
- **Moving text** (a bouncing pill, a sliding end-card line): the read box is followed by template matching at 4 fps
  (± 48 px) and the zone covers the union of its places.
- **An end-card line next to a kept block** (a tagline that comes up under the logo): the logo's own letters slide in
  through the same space, so one zone over the whole path from the scene's first frame cut them. The line's settled
  picture is followed back through the frames at 15 fps (`follow()`, ± 24 px across, ± 140 px up and down); a filled
  rect travels with it from 0.3 s before it first shows until it has settled (over smooth ground; letter zones
  otherwise), and the letter zone over its settled place starts only then. `cfg.auto.paths` records each path. A
  line that cannot be followed gets its settled place from 0.3 s before the block's own start.
- **Kept blocks** (logo, UI, sticker; prices unless the build lifted the tag) are never under a zone. One that lies
  across the block (at least half of its width) cuts the zone back on the side that costs it least. One that only
  meets a side or a corner (a price tag at the end of a headline's last line), and every source layer the build
  itself keeps («orig …»), is cut out of the zone exactly (+ 6 px around a read): the rest of the line is erased.
- **Light letters on bright ground**, in a block that is erased by zones (it moves, or is on the end card): they do not stand out; only their dark
  shadow does, and it stayed as a grey word. The part of a light block that stands on bright ground (the strip right
  beside it is bright, and the part's own 75th-percentile luma is at least 215) gets a second zone, inside the
  block's rect, that takes dark thin shapes. The word is gone; a faint trace of the shadow's edge can stay.
- **A block without a read** takes its own (rough) box from the analysis; with no box either it is counted in
  `cfg.auto.unplaced` and the job stops.
A `cfg` next to `auto` is laid over the measured cfg key by key (a hand override).

## End-card swap (`engine/endcard.py`, workflow `endcard.yml`)
A separate job on the same runner: the old end card of a finished video is replaced with a new card, the rest of the
file kept. ffmpeg + numpy only, no generated pixels, no renders.
- **Find:** the end card is the run of dark frames (mean grey < 0.20 and 90th percentile < 0.30 at 54×96) that reaches
  the end of the file, 1–9.5 s long; the cut is walked back over the cross-fade into the card. No dark tail → `none`;
  a dark tail longer than a card or a whole dark file → `refused` (nothing is written).
- **Already new:** three frames of the tail (from 1.6 s on, where cards differ most) are compared with the new card,
  ±0.6 s around the same offset; a mean abs diff ≤ 5 of 255 everywhere → `current`. Two cards that share a ground and a
  logo differ by 12–24 there, a re-encoded copy of the same card by 0.5–3.
- **Swap:** the source up to the cut + the new card scaled into the source frame for exactly the old card's length
  (its last frame held when the slot is longer); audio copied bit for bit; crf 17 (20, 23 when the file would pass
  95 MiB, the hosting's one-PUT limit).
- **Checks** (any fails → `failed`, nothing hosted): duration within 1.5 frames; the frame before the cut equals the
  source (≤ 6); three frames of the slot equal the new card (≤ 5). A contact sheet goes with the result for a look.
- **Contract:** inputs = record, base, table and the id of one long-text field holding the job JSON
  (`{v: 1, src, card, …}`); the answer is merged into the same JSON (`worker/endcard_job.py` docstring).

## Static image fixes (`engine/staticfix.py`, workflow `static.yml`)
A third job: a still ad image is read by a vision model and, when it breaks the caller's wants, only the regions that
must change are redrawn by an image-edit model and pasted back. Model ids and prompts come with the job (none here).
- **Read:** the caller's prompt answers one JSON object: the call to action (words, box in thousandths, shape, arrow,
  underline, letter colour), price labels (boxes), language. `plan()` turns it into fixes: words ≠ the wanted ones, any
  shape behind them, an arrow or an underline → `cta`; labels when the job says `prices: remove` → `tags`. None → `current`.
- **Edit:** per fix kind one crop round its boxes (+140 px, widened to the nearest ratio the model draws) goes to the
  edit model with the caller's prompt.
- **Paste:** the model's crop is resized, aligned to the original by ECC (affine, on everything outside the padded
  boxes) and taken only inside each padded box (call to action 20 px, label 80 px: its pointer dot sits 60–70 px out),
  feathered inside the box. Every pixel outside the boxes is the original's. The whole box is taken, not a diff mask:
  a white label on a white wall and a thin pointer line stay under any difference threshold. ECC < 0.80 or a mean
  difference > 20 outside the boxes = the model moved the picture → that try fails.
- **Check:** the result is hosted and read again with the same prompt; every fix must hold (`qa()`: the wanted words,
  no shape, no arrow, no underline, no label left, the call to action still there). Two tries per job, the second with
  the caller's `louder` sentence; then `failed`. Output in the source's format (png keeps its alpha, jpeg at q95).
- **Contract:** the same as the end-card job (ids only; the answer merged into the job JSON, `worker/static_job.py`).

## Playable localization (`engine/vidloc.py`, `worker/loc_job.py`, workflow `loc.yml`)
A fourth job, in stages the caller drives (it writes `Options.locPlay` and starts the run; the answer comes back in the
same block, `worker/loc_job.py` docstring):
- **test:** a playable the caller localized (words in its settings JSON) is played in Chromium on 5 screens by the smoke
  test of its mechanic (`playable/smoke/<mechanic>.js`, copied from the caller's repository by its sync script), the
  source first, then the localized file. It passes when every check the source passes also passes on the localized
  file; a check the source already fails is listed, not held against it. Passed → the localized file is delivered.
- **frame:** a looping end-card video (an inline `data:video/mp4` source, an AI interactive's `videoSrc` or a link): the
  median of 24 frames spread over it is hosted for the caller's read (the words stand still, what moves blurs out).
- **build:** `vidloc.py` with the caller's plan (blocks: rect, old lines, new lines, font class, letter side):
  - *letters* per block: black-hat / top-hat over the rect widened by a third of a line (an ascender on a tight box's
    edge), kept where static in half the frames; components on the wider box's edge, centred outside the read's box,
    taller than 1.6 lines or long thin rules are dropped. The measure mask also drops components far from the words'
    own brightness (a rug fleck at luma 140 beside letters at 235) and specks more than a fifth of a line from any
    letter-sized component; the erase mask keeps them (they are filled from their own surroundings);
  - *erase*: push-pull fill of every frame (real pixels only); *residue check*: the old letters' shape must not stand
    out of the filled frame more than the pixels around them do (a photo's texture answers inside and outside alike);
  - *new words*: lines from the row profile (split at its lowest rows near each expected break when the leading is
    tight); a slanted block (a hand-written note) is measured and drawn level, then turned back; family, weight and
    size from each line's coverage (height of the >= 0.5 box → size, drawn width and ink share → family and weight),
    one family + weight per role (three icon labels), the class's lead family where it scores close; a longer
    translation eases down to 92 % of the size to keep the old block's width, then to 72 % within the room (a button
    or a plain panel; over a photo the room is the frame); fine print re-wraps into up to two more lines when it fits
    nowhere else and stays off other blocks; colour = the old letters' median;
  - *page*: the mp4 inline, muted / playsinline / loop, MRAID ready + viewable, a tap → `mraid.open(clickUrl)`; then
    the video smoke test on 5 screens. Stops (`failed`, nothing hosted): no letters in a rect, old letters left in 6+
    frames, words that do not fit, a failed smoke check.

## Audio mix off Creatomate's queue (`engine/audiomix.py`, `worker/media_job.py`, workflow `media.yml`)
Creatomate renders one file at a time for the whole account, so the caller's audio-only renders (the fitted dub's mix of
its localization recipe: stems + one TTS clip per line) waited behind every other flow's videos: 17 of a run's 53 min
on 2026-11-27. This job makes the same mix from the same body in about a minute on this runner, ffmpeg + numpy only.
- **Body:** the JSON the caller would send to Creatomate's `/v2/renders` (audio elements only), so Creatomate stays the
  way back for any body. Keys and what they do: `engine/audiomix.py` docstring; anything else is `refused`.
- **Same as Creatomate, measured** on a real body (a 77 s dub, 3 stereo stems at 55 % + 36 mono lines with trims and
  fades) against Creatomate's own render of it: same length, lag 0 samples, RMS -21.00 vs -21.01 dB, 100 ms envelope
  correlation 1.0000; per line, 31 of 34 within -55 dB of Creatomate's (the codec's noise), 2 one sample apart. The one
  rule the match needed: a **mono source plays at -3 dB in each channel** (Creatomate's lines came out at 0.706 of
  unity while its stereo bed matched to 1.000).
- **Output:** WAV, 48 kHz, stereo, 16 bit; the sum is made in float and scaled down to -0.2 dBFS only when it would clip
  (`gainDb` says how much); the caller normalizes loudness afterwards.
- **Safety:** sources are fetched over https from the caller's providers only (`media_job.HOSTS`), ≤ 200 MiB each; each
  file's container is read from its first bytes and opened with that demuxer named (a playlist dressed as an mp3 is
  refused before ffmpeg sees it); ffmpeg and curl run without the job's tokens.
- **Contract:** inputs = record, base, table, field id and key of the job inside that field's JSON (`{v: 1, kind: "mix",
  body, state: "sent", nonce}`); the answer goes into the same object: `running` → `done` (`out` = hosted WAV) /
  `refused` (`why`) / `failed` (`step`, `error`), with `ran` = the nonce it answers. A job not in state `sent` is left
  alone. Details in the `worker/media_job.py` docstring.

### Stills and batches (`engine/stills.py`, job kind `batch`, 2026-11-27)
The same recipe asks Creatomate for ~25 still images per run (16 source frames for its text read, contact sheets of up to
32 tiles for its colour and pixel checks, 9 frames of the finished video for QA) and one voice sample per speaker: each
one a slot in the same one-file queue. A `batch` job takes up to 40 bodies at once (`items: [{k, body}]`, audio bodies →
WAV, still bodies → their jpg / png), downloads every source once and answers `outs: [{k, out, …}]` in the items' order;
one body the engines refuse makes the whole batch `refused` before anything is downloaded.
- **Same as Creatomate, measured** on the 15 stills of one real run (8 full-frame jpg, 7 png sheets of 1-27 tiles from 3
  videos): mean difference 0.35-1.18 levels per image, 1.72 on the worst 270 x 480 tile. The rules the match needed: the
  frame shown is the **first one at or after** the moment (20.17 s at 30 fps = frame 606), tiles are scaled with the
  **area** filter in **BT.601** colours, empty sheet cells are **opaque black**, png is RGBA. Keys: `engine/stills.py`
  docstring; anything else (images, text, other fits, sizes in %) is `refused`.
- **Speed:** the 15 stills render in ~35 s on 2 cores (each source decoded from its nearest keyframe, 4 frames at a
  time); the answer drops the job's bodies from the field (they are its bulk; the field holds 100k).

## Privacy (this repository is public)
Only code lives here: no media, no job data, no URLs, no texts. Jobs reach the runner as a record id; the worker reads
the job with repository secrets and prints step names, counts and timings only. No artifacts are uploaded.

## Run locally
```bash
pip install -r requirements.txt   # + ffmpeg
python3 worker/run_job.py --spec spec.json --work /tmp/erase --plate-only     # plate + check, no renders
CREATOMATE_API_KEY=… python3 worker/run_job.py --spec spec.json --work /tmp/erase   # full, output in /tmp/erase/out.mp4
python3 engine/endcard.py swap SRC.mp4 CARD.mp4 OUT.mp4 SHEET.jpg              # end-card swap, local files
python3 engine/vidloc.py PLAN.json SRC.mp4 /tmp/loc                              # end-card video localized (+ ffmpeg)
python3 worker/loc_job.py --spec job.json --work /tmp/loc                         # a loc stage, local files (+ node, playwright)
python3 worker/media_job.py --body BODY.json --work /tmp/media --out /tmp/mix.wav  # an audio mix (downloads its sources)
python3 worker/media_job.py --body JOB.json --files MAP.json --work /tmp/media      # a batch {kind: batch, items}, local files
```
