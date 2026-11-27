# Vendored: virtuoso-bridge-lite

This directory is a vendored copy of
[virtuoso-bridge-lite](https://github.com/Arcadia-1/virtuoso-bridge-lite)
(MIT, copyright Zhishuai Zhang, Xintian Li, Nan Sun, Lu Jie - see `LICENSE`
here and the repo-root `NOTICE`; cite Zhang et al. 2025 per `CITATION.cff`
in academic use).

- Base commit: `2cc3d066b27455258bd0517f4e24e4fc9f710d8d` (2026-11-26,
  includes the upstream Nov 26 transport fixes).
- Layout: `src/` (the `virtuoso_bridge` package), `tests/`, `examples/`,
  `skills/`, `mcp/`, `mcp_server.py`, `pyproject.toml`, `README.md`,
  `LICENSE`, `CITATION.cff`, `assets/`.

## Local modifications (virtuoso-cli-integration)

1. `src/virtuoso_bridge/virtuoso/basic/resources/ramic_bridge_daemon_3.py`
   and `.../ramic_bridge_daemon_27.py`: added the cross-bridge mutation
   lock (`_vai_lock_acquire` / `_vai_lock_release`, flock on
   `$VAI_LOCK_FILE`, default `/tmp/virtuoso-ai-bridge.lock`), taken for the
   whole of every evaluation. Design rationale:
   `../docs/decision-records/0001-dual-bridge-coexistence.md`.
2. `mcp_server.py`: appends every `tools/call` to the JSONL audit log
   shared with vcli's MCP server (`$VAI_AUDIT_LOG`, default
   `~/.local/state/virtuoso-ai/audit.jsonl`, `off` disables), and honours
   `VAI_DENY_TOOLS` (comma list, hidden from `tools/list` and refused on
   call; the `scripts/vai-mcp` safe mode sets it). Tested by
   `../tests/python/test_agent_layer.py`.
3. This directory is excluded from the repo's comment-budget gate
   (`../scripts/check-comment-budget.py`), the same way `resources/` is:
   vendored code keeps upstream's comment surface.

## Excluded from the vendored copy

Upstream's website/`docs/` tree, `stats/`, `tools/`, `scripts/` (repo
maintenance), `.github/`, and its agent-facing `AGENTS.md` / `CONTEXT.md`.
Get them from the upstream repo if needed.

## Updating

Re-copy from a newer upstream commit, re-apply modifications 1 and 2 (or port them if
the files changed), run `tests/` plus
`../tests/python/test_vai_lock_integration.py`, then update the base commit
above.
