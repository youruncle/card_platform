# Canon CanoScan LiDE 400 batch scanning

The Canon path is independent from the NETUM camera/NSSDK path. It uses a small
ImageCaptureCore Swift process so that every command owns exactly one exclusive
scanner session and closes it before exiting.

## Local bridge contract

```sh
./scripts/canon_lide400_bridge.swift list --timeout 20
./scripts/canon_lide400_bridge.swift capabilities --timeout 20
./scripts/canon_lide400_bridge.swift preview --dpi 300 --timeout 90 --output /absolute/new-file.png
./scripts/canon_lide400_bridge.swift scan --dpi 600 --timeout 240 --output /absolute/new-file.png
```

Only 300 and 600 DPI are accepted for scans. Output paths must not already
exist. Standard output is newline-delimited JSON with events such as `device`,
`capabilities`, `scan_configured`, `scan_started`, `scan_complete`, and `error`.
Exit status 75 represents a busy/open failure, 124 a timeout, 130 cancellation,
and 69 a disconnected or missing device. Sending SIGINT cancels an active scan,
waits for the close-session callback, and exits 130.

## HTTP workflow

All endpoints require the existing account session and filter every query by
`owner_id`.

1. `GET /v1/flatbed/device`
2. `POST /v1/flatbed/sessions` with `{ "dpi": 300 }`
3. `POST /v1/flatbed/sessions/{id}/capture/front?preview=false`
4. Keep the cards in place, flip them, then capture `back`.
5. `POST /v1/flatbed/sessions/{id}/pair`
6. If review is required, `PUT /v1/flatbed/sessions/{id}/pairs` with the
   confirmed one-to-one region mapping.
7. `POST /v1/flatbed/sessions/{id}/submit` to create the existing scanner
   recognition jobs. Repeating this request returns the same job IDs.

Existing whole-sheet files can be imported using the corresponding
`/sheets/front` and `/sheets/back` multipart endpoints. This is a fallback for
recovery and testing, not a browser-camera emulation.

## Pairing and recovery

The matcher scores identity, horizontal mirror, vertical mirror, and 180-degree
layout hypotheses. It combines geometry, outline dimensions, local Vision OCR,
card/year/serial hints, color histograms, perceptual hashes, and any available
recognition-consistency signal. A Hungarian assignment selects the global
one-to-one optimum. Every candidate score and conflict is persisted.

Scores at least 0.82 with no conflict are high confidence. Scores from 0.62 are
medium confidence; the rest are low. Quantity mismatches and every non-high
result require review. Original sheets and detection geometry are protected by
database immutability triggers; derived crops can be rebuilt. Reloading a
session returns latest front/back regions, candidate history, selected pairs,
and manual overrides.

## Layout recovery v14

The detector keeps count and geometry as separate questions. Threshold,
gradient, component, mixed-holder and physical-grid routes propose geometry;
each proposed cell must also contain distributed current-image evidence. An
expected count is never permission to divide an empty bed into synthetic card
regions. Empty slots remain missing and visible to the editor.

The normal back-side workflow is explicitly **flip each card in place**. Front
regions define stable identity slots and bounded search windows, but their
polygons are not copied as back-side answers. SAM 2 searches each slot in the
current back image, retries a failed slot once with a neighbour-bounded wider
window, and accepts only current-image geometry that passes object-presence,
size, aspect, slot-containment and peer-overlap checks. A failed or empty slot
is recorded in `slot_refinement.missing_slot_indices`; the implementation keeps
`manufactured_count=0`. Count-based whole-bed projection is disabled in this
mode because aligned white information bands inside card backs are not reliable
holder seams.

Every final crop then passes an independent validator which does not read the
detector or SAM confidence. It records presence, isolation, boundary and
usability scores plus explainable blank/fragment, multi-card, neighbour-overlap
and source-edge violations in `signals.crop_quality`. Regions remain visible
and editable; the overview uses green, amber and red geometry to expose quality
instead of silently hiding low-quality results.

The sheet editor supports add/delete, four-corner dragging, undo and confirm.
Confirmation creates a new derived sheet/crop revision and a
`flatbed_layout_override`; it never mutates the source image. Replay all local
immutable originals with:

```sh
PYTHONPATH=apps/api/src .venv/bin/python scripts/replay_flatbed_layouts.py --json
```

The replay is SHA-256 deduplicated and runs front-style standalone detection.
Expected counts are scoring truth only: they and detector-generated priors are
never fed into the detector. Use `--enforce` to apply the manifest's regression
floors. This intentionally replaces the old oracle-assisted 72/72 result.

## Optional SAM 2 boundary refinement

The production detector remains responsible for standalone candidates. On a
back scan, an isolated SAM 2.1 Tiny worker instead localizes one current-image
object per front slot. It never performs unprompted card discovery and cannot
manufacture a failed slot. Without a usable front slot workflow, the same worker
can still tighten a classical coarse box with geometry and overlap fallback.

Install the isolated Apple Silicon runtime and checkpoint:

```bash
./scripts/setup_flatbed_ml.sh
```

Then opt in with `FLATBED_ML_ENABLED=true`. The default is disabled so API
deployments without the 149 MB checkpoint or PyTorch sidecar remain functional.
Accepted sheets use a detector version ending in `+sam2.1-tiny`; audit metadata
is stored in `layout_hypothesis.ml_refinement`, and refined regions include
`sam2_box_prompt` in `signals.detection_evidence`. Each accepted mask receives a
1.2% per-side source-image safety margin (`sam2_safety_margin`) before cropping;
the margin is clamped at scan-bed edges and still passes the global overlap gate.
The worker retains the mask's minimum-area rotated rectangle
(`sam2_oriented_mask`) and the cropper perspective-rectifies those four corners;
one conflicting mask falls back independently instead of disabling refinement
for every other card on the sheet. When SAM instead fills an almost horizontal
white scanner slot that is materially larger than its prompt, four independent
Canny thresholds must agree on the same 1.5–12 degree card edge before the
worker substitutes an oriented quad (`edge_consensus_oriented`). This hybrid
route fixes tilted sleeves without letting a single noisy edge override a good
mask. General-purpose vision-language grounding may be used as a semantic
review signal, but it is not the pixel-geometry authority for crop corners.
