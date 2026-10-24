# Independent verification of the parallel round-7 EDA result

Date: 2026-10-24. This session (`feat/sch-layout-engine-round7`) did **not** perform
the real relayout described here. A parallel session (`jlc-ea`) performed it on
`temp-alarm-r7-scratch`; this document records an independent read-only check of its
final archive.

## Inputs

- Archive: `C:\Users\jlc\work\temp-alarm-20261024\evidence\round7\temp-alarm-r7-final.epro2`
- Size: 387,055 bytes; SHA-256
  `E4BD1B9E80E39FBF87D9F38FD34EA5D9C7341DE7B5DFC4A0DE931CED7B41A13E`
- Imported as a new project `temp-alarm-r7-verify`
  (`a5689d21f19c4d5ea34d273a8577edce`); schematic page `6a8364999a809cb4`.
- Baseline for comparison: `evidence/round7/final/before-conn.json`, imported from
  `before-round7.epro2` (SHA-256 `f5f1b33f…`).

## Result

| Check | Result |
|---|---|
| Components | 26 / 26, same refs, same `deviceUuid` and footprint |
| Pin→net partition | **Identical**. 65/65 pin keys, 14 nets, net-name changes (`D1_N1→VBUS`, `D1_N3→CC1`, `D1_N4→CC2`) do not change any pin partition |
| NC | 6 J1 data pins (B6/B7/B8/A6/A7/A8), unchanged |
| `sch gate` | **pass** (layout-lint, clusters, check, bridge-check, drc) |
| `layout-lint` | 0 overlap, 0 tight, 0 pin-coincidence, 0 off-grid, 0 out-of-sheet |
| `sch clusters` | 24 pairwise WARN `tight` (gaps 6–19 raw, default 20); the parallel session reports 5 cluster-level warnings. Both zero ERROR; aggregation differs, geometry does not |
| `layout-score` | **60.7 / fair** on the imported copy (no zone claims) — folded 100, reversed-labels **0**, proximity 57.9, stub-tidiness 72, frame-fit 100. The parallel session reports **66.7 / fair** after adding workflow zone claims (proximity 85.7, reversed still 0) |
| Independent `sch_layout_quality.py` | **0 error**, 6 warn, 3 info; before was 5 error, 6 warn, 2 info |
| PCB `pcb drc` | **passed: true, total 0** |
| PCB `layout-lint` | 92 / easy, 26 parts, 71/71 pads measured, 2 crossings, ratsnest 5,632.51 mil |

### Quality comparison

| Metric | Before | Parallel final | This session's offline candidate |
|---|---|---|---|
| Quality errors (independent) | 5 | **0** | **0** |
| Warnings | 6 | 6 | 2 |
| Module overlaps | 5 pairs | **0** | **0** |
| Component side balance (left/right) | 4 / 22 | **16 / 10** | 14 / 12 |
| Wire length | 2310 | **1940** | 1805 |
| Different-net crossings | 0 | 0 | 5 |
| Official `layout-score` | 94 (misleading) | **60.7 / fair** | not run (offline) |
| Applied to EDA | — | **yes** | no |

## Score interpretation: do not execute the reversed-label fixes blindly

`layout-score`'s reversed-label rule measures a netport against a **module/core
direction**, not against the host pin's outward normal. On this page the two disagree:

| Pin | Measured outward rotation | Score's suggested direction |
|---|---|---|
| `R10.1` | 180° (left) | `--direction right` |
| `C1.1` | 180° (left) | `--direction right` |
| `R14.1` | 180° (left) | `--direction right` |

Following those `fix` strings would point the port into the component, or be rejected
by `sch connect`; it is not a real readability defect. The score remains useful as a
diagnostic of proximity/stub length, but its reversed-label fixes must be checked
against `pins[].rotation` before use. The parallel session correctly declined to apply
them and kept the electrically verified page.

## Interpretation

- The parallel result is a real, electrically equivalent improvement: no module
  overlaps, balanced page, gate pass, 65/65 pin partition unchanged, PCB DRC 0.
- The official score (60.7 without zone claims, 66.7 with the parallel session's zone
  claims) is diagnostic, not a gate. Its reversed-label fixes conflict with pin
  normals and were not applied. The remaining readability trade-offs are netport
  style/proximity and the title-block-driven top/bottom whitespace; our checker sees
  no errors, 6 warnings and 3 info.
- This session's compose candidate is more compact (1805 wire length, 2 warnings) but
  **has not been applied**; its driver module is blocked by the D4 off-grid symbol.
- The two approaches are complementary: the parallel session's netport + group-move
  path achieved the real apply; this session's engine produced the independent
  measurement/quality gate and the blocked compose candidate.

## Files

- Snapshots and reports: `evidence/round7/verify/` (external)
- `connectivity.json`, `list.json`, `gate.json`, `lint.json`, `score.json`,
  `clusters.json`, `quality.json`, `sch.svg`, `sch.png`
- PCB: `pcb drc` and `pcb layout-lint` were run read-only on the imported copy.
