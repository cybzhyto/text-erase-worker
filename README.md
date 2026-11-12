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

## Composite without an alpha video (difference matting)
The overlay-only render body is rendered twice, over solid black (B) and solid white (W). Per channel
`α = 1 − (W − B)/255` and colour·α = B, so `final = plate·(1 − α) + B`; where nothing is drawn the plate pixel is kept
exactly. Caption pages can be drawn locally with the real font (`composite.py` CAPTIONS.json) so box and words always
come from the same page.

## Files
| | |
|---|---|
| `engine/build_math.py` | render body + cfg → erase zones (`ov.zones.json`), overlay bodies over black / white, caption pages |
| `engine/plate.py` | the plate (erased video); `--check N` = letters still standing out + blur where no text was |
| `engine/composite.py` | plate + B / W overlay renders (+ local caption pages) → final mp4, audio from B |
| `engine/measure.py`, `segs.py`, `track.py`, `splice.py` | zone measuring, a tracked moving element, re-made frame ranges |
| `worker/run_job.py` | one job end to end (spec contract in its docstring) |

## Privacy (this repository is public)
Only code lives here: no media, no job data, no URLs, no texts. Jobs reach the runner as a record id; the worker reads
the job with repository secrets and prints step names, counts and timings only. No artifacts are uploaded.

## Run locally
```bash
pip install -r requirements.txt   # + ffmpeg
python3 worker/run_job.py --spec spec.json --work /tmp/erase --plate-only     # plate + check, no renders
CREATOMATE_API_KEY=… python3 worker/run_job.py --spec spec.json --work /tmp/erase   # full, output in /tmp/erase/out.mp4
```
