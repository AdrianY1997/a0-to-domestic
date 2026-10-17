---
id: gotcha-vector-output-via-show-pdf-page-drops-source-annots-links
type: gotcha
scope: project
title: Vector output via show_pdf_page drops source annots, links and JS; keep it that way
triggers: ["pdf","javascript","annotations","anotaciones","seguridad","security","render"]
anchors: [{"path":"backend/src/a0td/render/vector.py","symbol":"show_pdf_page"}]
asserted: 2026-10-16
invalidated: null
superseded_by: null
confidence: medium
pin: false
source: audit
---
Cites SPEC 9.7/SAL-01 and 5.10: PyMuPDF `show_pdf_page` (used by render/vector.py#place_vector) copies only page content and resources, never annotations, links, OpenAction or document JavaScript from the canonical PDF. Verified experimentally with a hostile PDF (URI link, text annot, catalog OpenAction JS, Names/JavaScript): output had none. If vector placement is ever replaced with a page-copy approach (insert_pdf, graft), re-check this and add a regression test.
