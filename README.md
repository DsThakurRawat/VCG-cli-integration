# virtuoso-cli-integration

**Experimental.** One Cadence Virtuoso session driven by two open-source AI
bridges — [virtuoso-cli](https://github.com/deanyou/virtuoso-cli) (vcli, Rust)
and [virtuoso-bridge-lite](https://github.com/Arcadia-1/virtuoso-bridge-lite)
(Python) — combined into one self-contained repo, without them corrupting
each other's control of the session. Built for both a human at the CLI and
an AI agent over MCP.

Status: automated checks (Rust builds/tests on Linux/macOS/Windows, comment
budget, security audit, Python lock tests) run in GitHub Actions on every
push - see the Actions tab for this revision's actual result. **Not yet run
against a licensed Virtuoso session.** Treat it as experimental until the
checklist in `ACCEPTANCE.md` passes on a scratch library.

## Why this exists

Both projects descend from the same "RAMIC Bridge" SKILL daemon and define
the same SKILL globals (`RBStart`, `RBPort`, `RBIpc`, ~25 more). Loading both
unmodified into one CIW makes the second load clobber the first, and each
bridge's request queue knows nothing about the other — two AI tools could
edit the same cellView at the same moment.

## What is in this repo

1. **Namespaced fork of vcli** (crate root): every `RB*` SKILL symbol becomes
   `VC*`, its env vars become `VC_DAEMON_PATH` / `VC_SSH_PORT`, so both
   daemons can live in one CIW with zero shared symbols. 227 renames, all
   mechanical. Ships the `vcli` / `vtui` CLIs, the Rust `virtuoso-daemon`,
   and vcli's MCP server.
2. **Vendored virtuoso-bridge-lite** (`python/`): the full `virtuoso_bridge`
   package (schematic/netlist/simulation wrappers), its CLI, and its
   `mcp_server.py` MCP adapter, with the cross-bridge lock already applied.
   No separate clone, no manual patching. Provenance and local changes:
   `python/VENDORED.md`.
3. **Cross-bridge mutation lock**: one host-level `flock` file
   (`$VAI_LOCK_FILE`, default `/tmp/virtuoso-ai-bridge.lock`) that each
   bridge's daemon holds for the whole of every evaluation, and that vcli's
   xdotool GUI path also takes. Rust side: `src/vai_lock.rs`. Python side:
   already patched into `python/` and the fallback daemons in
   `resources/daemons/`. Design rationale:
   `docs/decision-records/0001-dual-bridge-coexistence.md`.

## Install (Linux, on or reachable to the Virtuoso host)

```sh
# Rust side (needs cargo; builds vcli, vtui, virtuoso-daemon)
./install.sh --user          # or: cargo install --path .

# Python side (needs Python >= 3.9; uv or pip)
uv pip install ./python      # or: pip install ./python
```

Human use: `vcli ...` and `virtuoso-bridge ...` CLIs as documented by each
tool.

## Use with any AI agent

Both bridges are MCP servers, started through one launcher:
`scripts/vai-mcp vcli` and `scripts/vai-mcp bridge`. Run
`scripts/vai-mcp doctor` to check the install,
`tests/fake_virtuoso/run-sandboxed` to test everything end to end without
Cadence, and `scripts/vai-acceptance` against a live session.

- **Claude Code** (`.mcp.json`) and **opencode** (`opencode.json`): ready in
  the repo root, so start the agent here.
- **Codex, Cursor, Gemini CLI, VS Code, Claude Desktop, ...**: copy-paste
  configs in `docs/agents.md`.
- **Agent rules**: `AGENTS.md` (Claude Code reads it via `CLAUDE.md`).
- **Mode**: `VAI_MODE=full` (default) lets the agent run raw SKILL, so it
  can do anything Cadence can. `VAI_MODE=safe` removes raw SKILL from both
  servers.
- **Human oversight**: every agent tool call from either server goes to one
  shared audit log. Watch it live with `scripts/vai-audit -f`.

## Setup notes that matter

- **Do not export `VB_*` variables globally.** Both CLIs read overlapping
  client-side vars (`VB_PROFILE`, `VB_REMOTE_HOST`, ...). Keep each tool's
  config in its own file/shell.
- Set `RB_REPLY_TAGS=1` for bridge-lite (reply-tag framing prevents
  out-of-step replies after timeouts).
- `VAI_LOCK_FILE` defaults to a host-wide path. Running several Virtuoso
  sessions on one host? Set a per-session path in each session's environment.
- **PDK hygiene:** driving a cloud agent against a licensed PDK exposes cell
  names/CDF data to the model provider. Keep PDK material out of this repo
  (see `.gitignore`) and consider a name-alias map before anything crosses
  SSH (see lixunqi12/virtuoso-agent for one pattern).

## Automated checks (GitHub Actions, on this tree)

- `.github/workflows/ci.yml`: fmt/clippy, `cargo test`, `cargo build`
  (+daemon), cargo audit, Windows native-ssh build, comment budget.
- `.github/workflows/integration.yml`: cross-platform integration matrix.
- `.github/workflows/fake-virtuoso.yml`: sandboxed end-to-end run of both
  real daemons, CLIs and MCP servers against a stand-in CIW
  (`tests/fake_virtuoso/`, bubblewrap).
- `.github/workflows/python.yml`: installs the vendored `python/` package
  and runs `tests/python/` (functional cross-process flock tests over the
  shipped daemon code) plus `compileall` over the vendored package.
- `tests/python/test_vai_lock_integration.py` exists because the first
  draft of the Python lock helper locked nothing at all; the test caught
  it, the fix is what ships.
- Base commits: vcli `b3af05ff` (v1.3.6, 2026-11-25), bridge-lite `2cc3d066`
  (2026-11-26, includes the Nov 26 upstream transport fixes).
- **Not covered anywhere:** live Virtuoso behavior. SKILL load-time,
  dual-daemon coexistence and the lock under real CIW load are unverified
  until `ACCEPTANCE.md` is run on a real (scratch) session.

## Licenses

MIT (both upstreams). See `LICENSE` (vcli), `python/LICENSE` (bridge-lite)
and `NOTICE` for attribution.
