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

## No MCP? Use the CLIs directly

If your agent has a shell but no MCP servers configured, the same work is
available from the two CLIs. Run them from this repo (the human installed
them; check with `scripts/vai-mcp doctor`). Both print JSON when piped; add
`--format json` to `vcli` to be explicit. Discover everything with
`vcli schema --all` and `virtuoso-bridge --help`.

| Task | Command |
| --- | --- |
| Raw SKILL (vcli) | `VCLI_CAPABILITY=admin vcli --format json skill exec 'EXPR' --timeout 60` |
| Raw SKILL (bridge-lite) | `virtuoso-bridge eval 'EXPR'` (multi-line: `virtuoso-bridge eval --stdin <<'EOF'`) |
| Load a `.il` file | `virtuoso-bridge load FILE.il` or `VCLI_CAPABILITY=admin vcli skill load FILE.il` |
| Libraries / cells | `vcli library list`, `vcli library list-cells LIB` |
| Open / inspect a cellview | `vcli cell open --lib L --cell C`, `vcli cell info`, `vcli cell save`, `vcli cell close` |
| Schematic edit | `vcli schematic open --lib L --cell C`, `vcli schematic place --master LIB/CELL --name I1 --x 0 --y 0`, `vcli schematic conn`, `vcli schematic pin`, `vcli schematic label`, `vcli schematic set-param` |
| Schematic readback | `vcli schematic list-instances`, `list-nets`, `list-pins`, `get-params`, then `vcli schematic check` |
| Transactions | `vcli tx begin --id T1 --lib L --cell C` → edits → `vcli tx diff` → `vcli tx commit` or `vcli tx rollback` |
| Maestro | `vcli maestro list-sessions`, `set-var`, `run`, `history-list`, `get-output-value`, `sim-messages` |
| Standalone Spectre | `vcli sim run`, `vcli sim run-async` / `job-status`, `vcli sim check-license` |
| SKILL / doc lookup | `vcli skill find QUERY`, `virtuoso-bridge skill-find`, `skill-info`, `doc-search` |
| Screenshots / windows | `vcli window screenshot --path out.png`, `vcli window list`, `virtuoso-bridge screenshot` |
| Dialogs (read-only) | `virtuoso-bridge inspect-dialogs --json`, `vcli window list-dialogs-x11` |
| Health | `scripts/vai-mcp doctor`, `virtuoso-bridge status` |

Differences from the MCP path. Tell the human you are using it:

- **Nothing here goes to the shared audit log**, and `scripts/vai-audit`
  will not show it. vcli records the SKILL text it sends (not your
  name or the result) in `~/.cache/virtuoso_bridge/logs/commands.log`;
  bridge-lite records nothing comparable. Say what you ran in your replies.
- **Safe mode is not enforced for you.** If the human asked for safe mode,
  do not use `vcli skill exec`/`skill load`, `virtuoso-bridge eval`/`load`
  or `gui`. Stick to the structured commands.
- **Still enforced:** the cross-bridge lock and the timeout quarantine live
  in the daemons, so they apply to every CLI call too. Rule 2 below holds.
- Never use `virtuoso-bridge dismiss-dialog`/`dismiss-window`,
  `vcli window dismiss-dialog`/`dismiss-window-x11` or `virtuoso-bridge gui`
  unless the human explicitly asks (rule 3).

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
