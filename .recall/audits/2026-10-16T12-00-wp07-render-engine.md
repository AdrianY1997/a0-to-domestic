# Audit WP-07 render engine (2026-10-16)
Not a git repo; read files on disk. Render tests (integration/golden/unit), ruff, mypy: green.

## Findings
- marks.py:267-288 (+310-327): 🟡/❓ with overlap_mm < 6 (or no bottom/right receiving edge) the corner label (up to 2 lines, ~5.5 mm tall, ~40 mm wide) is drawn on every sheet, not only the last; it is taller than the receiving strip (o>=3) so it stays partly visible after assembly (SPEC 5.9 "no deben quedar visibles"). Spec itself prescribes corner for o<6; confirm intent.
- engine.py:425: 🔵 preview of a skipped/unselected sheet labels it "Sheet 1/N" (print_index or 1).
- tests: 🟡 no test that annotations/JS/OpenAction of a hostile source PDF are absent from output (verified manually: show_pdf_page copies none); no test for printer correction on the assembly page; raster rotation tested only by sheet count (verified manually 0/90/180/270 orientation correct).

## Checked, no issue
Vector/raster scale and placement (rotations 0/90/180/270, cropbox), clip to usable rect, pixel-rounded crop placement, correction matrix (1/k about centre, wraps content+marks), viewer prefs, metadata, attachment (no IPP uri in JobSettings), marks inside C, label/alignment geometry vs. receiving strips.
Not reviewed: strings.py wording, golden PNG content.
