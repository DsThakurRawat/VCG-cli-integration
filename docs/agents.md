# Connecting any AI agent (and the human next to it)

Both bridges speak MCP over stdio, so any MCP-capable agent can drive
Virtuoso. Every agent config below runs the same launcher,
`scripts/vai-mcp`, so lock, audit, mode and per-tool settings behave
identically whichever agent you use.

```
agent (opencode / Claude Code / Codex / Cursor / Gemini / ...)
  ├── scripts/vai-mcp vcli    → vcli mcp serve        ─┐  same flock
  └── scripts/vai-mcp bridge  → python/mcp_server.py  ─┤  ($VAI_LOCK_FILE)
human: vcli / virtuoso-bridge CLIs, Virtuoso GUI       ─┘  → one Virtuoso session
both servers → one audit log ($VAI_AUDIT_LOG) → scripts/vai-audit -f
```

## 1. Install once

```sh
./install.sh --user                     # or: cargo build --release
uv venv python/.venv && uv pip install --python python/.venv/bin/python ./python
scripts/vai-mcp doctor                  # both sides should say "ok"
```

The launcher finds `vcli` in `target/release`, `target/debug`, then `PATH`
(override with `VCLI_BIN`), and a Python that can import `virtuoso_bridge`
in `python/.venv`, `.venv`, then `python3` (override with `VAI_PYTHON`).
Every vcli candidate must be this fork's build (it carries the namespaced
`VC*` SKILL and `VAI_LOCK_FILE`); upstream vcli would collide with
bridge-lite in the CIW and is rejected, and `doctor` reports it NOT READY.
A `VCLI_BIN` that fails the check is refused, never silently replaced.

## 2. Configure each bridge separately

The two tools read overlapping `VB_*` variables, so never export them
globally. Put each tool's settings in its own file; the launcher loads only
the matching one into that server's process:

```sh
mkdir -p ~/.config/virtuoso-ai
cat > ~/.config/virtuoso-ai/bridge.env <<'EOF'
VB_REMOTE_HOST=eda-host          # or localhost on the Virtuoso machine
VB_WORK_DIR=/scratch/me/agent
EOF
cat > ~/.config/virtuoso-ai/vcli.env <<'EOF'
VB_REMOTE_HOST=eda-host
EOF
```

Also accepted there (or in the agent's `env` block): `VAI_MODE`,
`VAI_LOCK_FILE`, `VAI_AUDIT_LOG`, `VCLI_CAPABILITY`, `VAI_DENY_TOOLS`.
`VAI_CONFIG_DIR` moves the directory.

| Setting | Default | Effect |
| --- | --- | --- |
| `VAI_MODE` | `full` | `full`: raw SKILL on both servers (vcli gets `admin`), so the agent can do anything SKILL can. `safe`: enforced over anything inherited: `admin` is stripped from `VCLI_CAPABILITY` (from the shell or `vcli.env`) and `evaluate,load_file` are always added to `VAI_DENY_TOOLS`. |
| `VAI_LOCK_FILE` | `/tmp/virtuoso-ai-bridge.lock` | Must match the value in the Virtuoso session's environment. Per-session path if several sessions share a host. |
| `VAI_AUDIT_LOG` | `~/.local/state/virtuoso-ai/audit.jsonl` | `off` disables. File is created mode 0600. |
| `RB_REPLY_TAGS` | `1` (bridge) | Reply-tag framing; keeps replies in step after timeouts. |

## 3. Point your agent at it

Project-local configs are already in the repo root for **Claude Code**
(`.mcp.json`) and **opencode** (`opencode.json`); start the agent in this
directory. Both also read `AGENTS.md` (Claude Code via `CLAUDE.md`).

For other agents, or to use it from another project, use the absolute
launcher path (`/path/to/repo/scripts/vai-mcp`):

**Codex CLI** (`~/.codex/config.toml`):

```toml
[mcp_servers.virtuoso-vcli]
command = "/path/to/repo/scripts/vai-mcp"
args = ["vcli"]

[mcp_servers.virtuoso-bridge]
command = "/path/to/repo/scripts/vai-mcp"
args = ["bridge"]
```

**Cursor** (`.cursor/mcp.json`), **Gemini CLI** (`.gemini/settings.json`),
**Claude Desktop** (`claude_desktop_config.json`), Windsurf, Cline and most
others use the `mcpServers` shape:

```json
{
  "mcpServers": {
    "virtuoso-vcli":   { "command": "/path/to/repo/scripts/vai-mcp", "args": ["vcli"] },
    "virtuoso-bridge": { "command": "/path/to/repo/scripts/vai-mcp", "args": ["bridge"] }
  }
}
```

**VS Code / Copilot** (`.vscode/mcp.json`) uses `servers`:

```json
{
  "servers": {
    "virtuoso-vcli":   { "type": "stdio", "command": "/path/to/repo/scripts/vai-mcp", "args": ["vcli"] },
    "virtuoso-bridge": { "type": "stdio", "command": "/path/to/repo/scripts/vai-mcp", "args": ["bridge"] }
  }
}
```

**opencode from another project**: copy `opencode.json` and use absolute
paths in `command`.

**Remote agent host:** run the agent where it can reach the Virtuoso host
over SSH (the bridges tunnel themselves), or wrap the launcher:
`"command": "ssh", "args": ["-T", "eda-host", "/path/to/repo/scripts/vai-mcp", "bridge"]`.

Agents without `AGENTS.md` support: paste `AGENTS.md` into their system
prompt / rules file. Its rules matter more than the tool list.

## 4. The human's side

- Work as usual: Virtuoso GUI, `vcli ...`, `virtuoso-bridge ...`. The CLIs
  take the same lock, so your commands and the agent's queue behind each
  other instead of interleaving.
- Watch the agent live: `scripts/vai-audit -f`. Filters: `--errors`,
  `--tool maestro`, `--server bridge`, `--client opencode`, `--full`
  (untruncated args), `--json` (raw JSONL for scripting).
- Restrict an agent: `VAI_MODE=safe` in its config `env`, or narrow vcli
  further with `VCLI_CAPABILITY=schematic,cell,library`.
- Your own GUI clicks do **not** take the lock. When you are editing a cell
  by hand, tell the agent to stay off it.

## 5. Check without Virtuoso

```sh
scripts/vai-mcp doctor
python -m pytest tests/python/test_agent_layer.py
tests/fake_virtuoso/run-sandboxed        # full end-to-end, needs bwrap
```

`run-sandboxed` starts a stand-in CIW (`tests/fake_virtuoso/`) that hosts
both real daemons and answers with a small built-in SKILL evaluator, then
drives the real CLIs, the real MCP servers through `scripts/vai-mcp`, the
lock, the timeout quarantine and the audit log against it. It runs inside
bubblewrap: read-only root, private `/tmp`, throwaway `HOME`, no network
beyond loopback, so it never touches your real lock, tokens or config. It
proves the plumbing, not Cadence: SKILL semantics, OpenAccess, the GUI and
design data still need `ACCEPTANCE.md` on a licensed session.

Live behaviour is checked by the "Agent layer" steps in `ACCEPTANCE.md`.
