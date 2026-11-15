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
  as tall as a capital, two or more in a row, are a text line; punctuation joins glyph by glyph. A white coat, a
  pearl, a radio button, the counters of dark page text are islands too, but nothing dark rings them or they do not
  line up. The letters' soft shadow is not blurred away: it is measured on the frames whose surround is flat and
  bright (the shadow = the text shape, blurred, moved down, at a strength) and **divided out**, so the picture keeps
  its detail right up to the letters and only fill + outline are filled.
- **A text that stays through many shots** (the legal line, `still` in ZONES.json): its letters are found once, on the
  shots where they stand out, and that one mask erases the line in every frame of its time. On a white page such a
  line is only its grey shadow, and a zone measured on that shot alone found dark specks or nothing.
- **A translucent caption plate** (zone mode `exact`): the whole plate with its letters, on its page's own frames
  only (a `full` zone is also erased on the 2 frames before and after it).

## Checks that stop a job (`worker/run_job.py`)
A job that would leave old words on screen stops with an error instead of delivering a file:
- **caption coverage**, right after «measure zones»: `cfg.auto.captions` holds, per caption block, the share of the
  frames that show caption letters and are under an erase. A block under one half = «captions not covered». A source
  whose spoken word sits on a coloured box is named and counts as uncovered (this engine does not erase that box).
- **letters left on the plate**, after «check plate»: an outlined caption line found on any plate frame, or the
  letters of a `still` text still standing out on 3 or more sampled frames.
The top-hat zones' residue (`plate.py --check`) stays a note in the job record: it also fires on textured ground.
A spec may set `allowUncovered` / `allowResidue` for a hand check.

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
this build»), and the reason reaches the job's private error field.

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
- **Kept blocks** (logo, UI, sticker; prices unless the build lifted the tag) clip the zones that would cover them,
  and so do the source layers the build itself keeps («orig …»): a zone is cut back on the side that costs it least.
A `cfg` next to `auto` is laid over the measured cfg key by key (a hand override).

## Privacy (this repository is public)
Only code lives here: no media, no job data, no URLs, no texts. Jobs reach the runner as a record id; the worker reads
the job with repository secrets and prints step names, counts and timings only. No artifacts are uploaded.

## Run locally
```bash
pip install -r requirements.txt   # + ffmpeg
python3 worker/run_job.py --spec spec.json --work /tmp/erase --plate-only     # plate + check, no renders
CREATOMATE_API_KEY=… python3 worker/run_job.py --spec spec.json --work /tmp/erase   # full, output in /tmp/erase/out.mp4
```
