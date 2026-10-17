---
id: gotcha-papers-oriented-always-sorts-width-height-so-w-h-with
type: gotcha
scope: project
title: papers.oriented() always sorts width/height, so (w,h) with PORTRAIT is swapped when w>h; do not assume input order
triggers: ["oriented","orientation","orientacion","roll","rollo","banner","portrait"]
anchors: [{"path":"backend/src/a0td/domain/papers.py","symbol":"oriented"}]
asserted: 2026-10-17
invalidated: null
superseded_by: null
confidence: medium
pin: false
source: audit
---
oriented(w, h, PORTRAIT) returns (min, max). Any caller that builds a frame from an explicit sheet size (e.g. roll width x shortened length in planner._shrink_roll) gets the dimensions swapped when the length is below the width. Roll sheets are portrait by feed direction, not by shape: bypass oriented() for rolls or pass orientation AUTO.
