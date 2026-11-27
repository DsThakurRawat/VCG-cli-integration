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
3. `src/virtuoso_bridge/virtuoso/basic/bridge.py`
   (`_ensure_daemon_capabilities`): in local mode (`VB_REMOTE_HOST=localhost`,
   no SSH runner) the CLI `eval` and the MCP server never reached
   `warm()`/`ensure_ready()`, so the first hello was sent without the local
   token and the daemon refused it ("bridge token required"). The local
   token is now provisioned at first authenticated use. Found and covered by
   `../tests/fake_virtuoso/` (`test_bridge_cli_roundtrip_local_mode`).
4. `tests/conftest.py`: autouse `_private_cross_bridge_lock` gives every
   test its own `VAI_LOCK_FILE`. Without it, daemon tests that time out
   wrote the real `/tmp/virtuoso-ai-bridge.lock.quarantine`, which
   quarantined both bridges on the host (and made later tests in the same
   run fail on the leaked marker).
5. Test-suite hygiene so `tests/` runs green in CI (`../.github/workflows/python.yml`):
   `tests/conftest.py` skips tests that need upstream's unvendored `tools/`
   or `scripts/` (only while the file is absent, with the path as the
   reason); `tests/_fcntl_stub.py` replaces the four ad-hoc `fcntl` stubs and
   keeps the real `flock`, so the fork's cross-bridge lock works when the
   daemons are imported in-process.
6. `src/virtuoso_bridge/transport/tunnel.py`: generated local-setup files are
   made 0600 and missing state/work directories are created 0700 at every
   level (`Path.mkdir(parents=True)` only applies `mode` to the leaf), so
   local mode works under the common Linux umask 0002.
7. This directory is excluded from the repo's comment-budget gate
   (`../scripts/check-comment-budget.py`), the same way `resources/` is:
   vendored code keeps upstream's comment surface.

## Excluded from the vendored copy

Upstream's website/`docs/` tree, `stats/`, `tools/`, `scripts/` (repo
maintenance), `.github/`, and its agent-facing `AGENTS.md` / `CONTEXT.md`.
Get them from the upstream repo if needed.

## Updating

Re-copy from a newer upstream commit, re-apply modifications 1-6 (or port them if
the files changed), run `tests/` plus
`../tests/python/test_vai_lock_integration.py`, then update the base commit
above.
