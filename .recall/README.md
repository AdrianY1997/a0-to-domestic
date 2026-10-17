# .recall — project memory

One fact per file in `nodes/`. Committed on purpose: this knowledge travels
with the repo.

- `nodes/` — the memories. Edit by hand if you like; run `recall reindex` after.
- `INDEX.md` — generated summary, one line per memory. Reviewable in diffs.
- `archive/` — superseded or evicted memories. Nothing is ever deleted.
- `audits/` — per-task audit reports.
- `journal.jsonl` — append-only log of every write, recall and audit.
- `local/` — gitignored: your personal layer, the touched-file ledger, proposals.
- `index.json`, `usage.json` — gitignored, derived. Rebuild with `recall reindex`.

Titles are written as complete facts ("Use Drizzle, not Prisma"), not topics
("ORM choice"), because the title alone is what gets injected into context.
