# Agent guide: driving Cadence Virtuoso through this repo

Read by opencode, Codex, Claude Code (via `CLAUDE.md`), Cursor and any other
agent that loads `AGENTS.md`. A human works in the same Virtuoso session
alongside you: the GUI, the CIW and the design data are **shared**.

## Your two MCP servers

Both are started by `scripts/vai-mcp` (see `docs/agents.md` for per-agent
config). They drive the **same** Virtuoso session and take the same host
lock (`$VAI_LOCK_FILE`), so their calls never interleave.

| Server | Strengths | Raw SKILL tool |
| --- | --- | --- |
| `virtuoso-bridge` (Python, bridge-lite) | `new_schematic`/`build_schematic`/`add_part`/`add_wire`/`add_pin`, `check_schematic`, standalone Spectre (`simulate`, `simulate_many`, `read_waveform`), PDK inspection (`list_pdk`/`search_pdk`/`describe_pdk`), Cadence doc search (`search_docs`), `lookup_skill`, `screenshot`, Maestro `run_test`/`test_results` | `evaluate`, `load_file` |
| `virtuoso-vcli` (Rust, vcli) | cellview open/save/close/info, schematic place/wire/list, **transactions** (`tx_begin`/`tx_diff`/`tx_rollback`/`tx_commit`), Maestro session vars/run/export/history, `maestro_snapshot`, window list/screenshot, file upload/download | `skill_exec`, `skill_load`, `skill_eval` |

Anything Cadence can do through SKILL is reachable through the raw SKILL
tools: use a structured tool when one fits, and raw SKILL as the supported
escape hatch otherwise. If the raw SKILL tools are missing, the human started
the servers in `VAI_MODE=safe`; do not try to work around it.

Skills (detailed how-tos) are MCP resources: `skill://virtuoso/SKILL.md`,
`skill://spectre/SKILL.md`, `skill://netlist/SKILL.md`,
`skill://optimizer/SKILL.md`, `skill://search/SKILL.md` on the bridge
server, plus vcli's prompts `maestro-run-and-check`,
`spectre-failure-triage` and `cell-design-review`. On disk they are under
`python/skills/`. Read the branch you need; do not load the whole catalog.

## Rules (learned the hard way by both upstream projects)

1. **Scratch library first.** Until `ACCEPTANCE.md` passes on a real
   session, only write to a throwaway library the human named.
2. **Never retry after a timeout or uncertain result.** SKILL is not
   replayed automatically for a reason: the first call may have landed.
   After a timeout the daemons write a quarantine marker
   (`$VAI_LOCK_FILE.quarantine`) and **both** bridges refuse every SKILL
   eval and GUI action until a human removes it. When you see
   "cross-bridge quarantine active", stop and tell the human. Never delete
   the marker yourself: only the human can confirm the CIW is idle.
3. **Do not touch dialogs.** A popup in the shared CIW cannot be attributed
   to you or the human. Never press Enter/Cancel or close a form to unblock
   yourself; report it and wait. `virtuoso-bridge inspect-dialogs` is
   read-only and safe.
4. **Do not destroy what you did not create.** `new_schematic` /
   `build_schematic` need `replace=true` to overwrite; only pass it when the
   human asked for a replacement. Creating a cell is not permission to delete
   an existing one. Preserve unrelated instances and the human's edits.
5. **Wrap multi-step vcli edits in a transaction:** `tx_begin` → edits →
   `tx_diff` (show the human) → `tx_commit`, or `tx_rollback` on doubt.
6. **Verify, don't assume.** Check SKILL function signatures with
   `lookup_skill`/`describe_skill`/`search_docs`, and real cell pins / CDF
   params before placing. A miss in examples is not proof a function is
   absent. Read back connectivity and run `check_schematic` before
   netlisting or simulating.
7. **Return values, not prints.** `printf` output goes to the CIW, not to
   you; build a string or list as the SKILL return value instead
   (`python/skills/virtuoso/references/troubleshooting.md`).
8. **A screenshot or a successful call is not electrical signoff.** Use
   Spectre/PSF numbers for claims about circuit behaviour.
9. **Maestro:** reuse the exact existing session on a shared CIW; bind result
   reads to the acknowledged history; never purge or close sessions you did
   not open.
10. **Expensive tools are rate-limited** (simulations, screenshots, startup).
    Batch work into fewer calls: one `evaluate` can run many expressions.
11. **PDK data is licensed.** Do not paste cell names, model files or CDF
    dumps anywhere outside this session.

## What the human sees

Every tool call you make, on either server, is appended to the shared audit
log (`$VAI_AUDIT_LOG`, default `~/.local/state/virtuoso-ai/audit.jsonl`)
with your client name, arguments, result and duration. The human watches it
with `scripts/vai-audit -f`. Work so that log reads like a clear story.

## Working in this repo (code changes)

- Rust: `cargo fmt --check`, `cargo clippy -- -D warnings`, `cargo test`.
- Python: `python -m pytest tests/python` (needs `pip install ./python`).
- `python3 scripts/check-comment-budget.py` must pass: comment counts per
  directory may only go down.
- `python/` is vendored bridge-lite; record every local change in
  `python/VENDORED.md`.
