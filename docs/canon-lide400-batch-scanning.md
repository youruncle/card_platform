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

## Layout recovery v2

`layout-recovery-v2` treats a scan as one layout rather than a bag of contours.
Its evidence routes are multi/global thresholding, adaptive thresholding,
gradient contours and Hough segments, horizontal/vertical projection
periodicity, and connected-component splitting. Whole-bed components are scale
evidence and are rejected as cards. Hypotheses are ranked globally by region
boundary energy, rectangular/card aspect plausibility, coverage, row/column
consistency, overlap/conflict penalties, missing slots, clipping and rotation.

Once a front revision is confirmed, its count is a hard constraint for the
back. Identity, horizontal/vertical mirrors and their 180-degree combination
are scored against back-side edge evidence; an independent back result can
refine boundaries but cannot silently change eight cards into three. Low edge
energy, count changes, overlaps, whole-sheet candidates and severe clipping
set `requires_review` and retain their reasons in the sheet hypothesis.

The sheet editor supports add/delete, four-corner dragging, undo and confirm.
Confirmation creates a new derived sheet/crop revision and a
`flatbed_layout_override`; it never mutates the source image. Replay all local
immutable originals with:

```sh
PYTHONPATH=apps/api/src .venv/bin/python scripts/replay_flatbed_layouts.py --json
```
