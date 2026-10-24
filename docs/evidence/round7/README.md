# Round 7 evidence: schematic layout engine

Date: 2026-10-24. Client: JLCEDA Pro 4.1.78. CLI: easyeda-agent 1.14.0.
Python: 3.13.15. Baseline repository commit: `fe950d3`.

Source project: `test` (`d71c031469054b24b71c3ace8f045b1d`), schematic
`Schematic1 / P1` (`6a8364999a809cb4`). The source project was read only.

Frozen baseline archive: `before-round7.epro2`, SHA-256
`f5f1b33fdd10555c12ee5647864619fabfb1669ad5734a0fff4daa0541bc45e9`.

## Files

| File | Meaning |
|---|---|
| `before.png` | Official EDA export of the page before any change |
| `before-connectivity.json` | Canonical connectivity IR, 26 components / 14 nets / 65 connections |
| `before-quality-input.json` | Normalised measured page used by the quality checker |
| `candidate.png` / `candidate.svg` | Offline `layout-render` preview of the computed one-page, six-module layout. Simplified symbols; **not** an EDA import and not an Apply receipt |
| `candidate-quality.json` | Independent quality report of the candidate |
| `comparison.json` | Before/after quality delta on the same 26 parts and module definition |
| `model-report.json` | Coverage, pin/net, NC, text and anchor preflight; one blocker: D4 |
| `placement-consistency.json` | Rigid-translation check; one 1-raw anchor/pin mismatch on D4 |
| `anchor-corrections.json` | The attempted D4 anchor correction and its rationale |
| `binding-check.json` | 26/26 parts carry `EasyEDA Agent Component ID` on the isolated copy |
| `compose-error.txt` | Why `compose --preserve-instances` refused the driver module |

## Result in one line

The offline engine produces a one-page, six-module candidate with 0 quality errors
(5 before). It was **not** written into EDA: D4/KT-0805R has a stored anchor 224 and
pins 210, an offset of -14 raw which is not on the 5-raw grid. The planner normalises
the anchor to 225; applying that would land the real pins 1 raw away from the planned
wires, and the corrected anchor is rejected by compose as off-grid. The driver module
therefore needs either a different equivalent symbol or a rigid-zone apply path.

The official `autolayout --engine official --apply --rewire` fallback was also tried on
a disposable copy: it moved the parts, then failed while rebuilding wiring because the
connector timed out, leaving an invalid transaction. That state was recovered with
`doc reload` and is not used as evidence.

## Commands

```bash
easyeda sch save --project <uuid> --doc <page>
easyeda sch list --project <uuid> --page <page> --include-device-identity \
  --include-bbox --include-pins --include-wires --include-page-primitives
easyeda sch connectivity --project <uuid> --page <page>
easyeda sch designator-geometry --project <uuid> --doc <page>
easyeda sch sheet-geometry --from-svg before.svg --json

python design-hardware/scripts/sch_layout_model.py \
  --snapshot before-list.json --connectivity before-conn.json \
  --designators before-designators.json --sheet sheet.json \
  --module-plan design-hardware/assets/layout-modules/temperature-alarm.module-plan.json \
  --out-dir model

python design-hardware/scripts/sch_layout_pipeline.py \
  --easyeda easyeda --source model/layout-source.json \
  --model-report model/model-report.json --before before-list-compose.json \
  --out-dir planned

easyeda sch layout-plan --zones --from model/layout-source.json --out planned/zones-geom.json
easyeda sch layout-sheet-plan --from planned/render-input.json --out planned/pages.json --flow z
easyeda sch layout-render --from planned/page.json --out planned/layout.svg
python design-hardware/scripts/sch_layout_quality.py planned/planned-quality-input.json \
  --compare model/quality-input.json
```

`before-list-compose.json` is a copy of the list snapshot with the extra
`pagePrimitives.pins` class removed; the original snapshot is preserved. The note is in
`before-list-compose.note.json` in the external evidence directory.

## Parallel-session final result (independently verified)

`verify/` contains the read-only check of the parallel session's final archive
`temp-alarm-r7-final.epro2` (387,055 bytes, SHA-256
`E4BD1B9E80E39FBF87D9F38FD34EA5D9C7341DE7B5DFC4A0DE931CED7B41A13E`), imported as
`temp-alarm-r7-verify`:

- `connectivity.json` — 26 parts / 14 nets / 65 connections; pin partition equal to
  the baseline after the `D1_N1→VBUS`, `D1_N3→CC1`, `D1_N4→CC2` rename.
- `gate.json` — pass; `lint.json` — 0 overlap / 0 tight / 0 pin-coincidence / 0 off-grid.
- `score.json` — 60.7 fair; reversed-labels 0, proximity 57.9.
- `quality.json` — 0 error / 6 warn / 3 info (baseline: 5 error / 6 warn / 2 info).
- `clusters.json` — 24 findings, all WARN tight.
- `sch.png` — official export of the final page.

The full narrative and comparison with this session's offline candidate are in
`docs/round7-peer-verification.md`. The parallel session performed the apply; this
session only verified the archive.

## External raw evidence

`C:\Users\jlc\work\temp-alarm-20261024\evidence\round7\final\` contains the full
snapshots, model inputs, layout outputs, apply queue, and the binding journal. The
repository copy keeps only review-sized files.
