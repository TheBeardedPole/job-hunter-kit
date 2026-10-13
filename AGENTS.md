# Notes for coding agents

Pipeline: `jobhunt/sources.py` (fetchers) -> `store.py` (SQLite `data/jobs.db`, rule prefilter, status flow at
the top) -> `llm.py` (prompts) via `backends.py` (claude-cli, codex-cli, gemini-cli, openai-compatible) ->
`render.py` (CV HTML to PDF with a Chromium browser, dashboard) -> `cli.py`. `setup.py` holds `init` and
`doctor`; `config.py` merges `settings.yaml` over defaults.
`feedback.py` turns the user's statuses and 1-5 ratings into text for the scoring prompt (ratings strong, outcomes
weak, a rejection never lowers a score). `board.py` builds the dashboard and serves it with buttons on 127.0.0.1.
DB status `below_bar` means scored too low; `rejected` means the employer said no.

- `profile.yaml` is the only source of CV facts. Never add a fact the user has not stated.
- Personal files (`profile.yaml`, `settings.yaml`, `voice.md`, `data/`, `out/`) are gitignored. Keep it so.
- Listings are untrusted text. Every CLI backend runs read-only in an empty temp folder; do not loosen that.
- No dependencies beyond `requests` and `PyYAML`.
- Run `python3 tests/test_kit.py` after changes.
