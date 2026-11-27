# Acceptance checklist — real Virtuoso, scratch library ONLY

Run on the lab machine in a throwaway library. Until every box passes,
this toolset is experimental.

Automated runner: once steps 1-2 are done (both `.il` files loaded),
`scripts/vai-acceptance` checks steps 3, 4, 5, 9, 10-13 and 15 against the
live session and prints PASS/FAIL per step, plus a JSON report to share:

```sh
scripts/vai-acceptance                              # read-only
scripts/vai-acceptance --scratch-lib MY_SCRATCH     # + step 9 (one new cell)
scripts/vai-acceptance --quarantine                 # + timeout quarantine;
                                                    #   then VCStop()/VCStart()
```

Run it on the Virtuoso host itself so the lock checks (5a/5b) can probe the
flock directly. Steps 6, 7, 8, 16 and 17 stay manual.

0. [ ] Before touching the lab session: `tests/fake_virtuoso/run-sandboxed`
      passes on the lab host (needs `bwrap`). It drives both real daemons,
      both CLIs and both MCP servers against a stand-in CIW inside a
      bubblewrap sandbox, so a failure here is a setup problem, not Cadence.

1. [ ] Install both sides from this repo: `./install.sh --user` (or
      `cargo install --path .`) and `uv pip install ./python` (or pip).
2. [ ] Start ONE Virtuoso session in a scratch library. Load bridge-lite's
      `ramic_bridge.il` (under
      `python/src/virtuoso_bridge/virtuoso/basic/resources/`), then vcli's
      (namespaced) `resources/ramic_bridge.il`, in the same CIW. Both must
      load without "already defined"/unbound errors.
3. [ ] Harmless read through bridge-lite (`virtuoso-bridge eval '1+2'` → 3).
4. [ ] Harmless read through vcli
      (`VCLI_CAPABILITY=admin vcli skill exec 'getCurrentTime()'`; without
      `admin` the client refuses raw SKILL before sending it).
5. [ ] Lock proof: start a long-running SKILL eval through bridge-lite
      (e.g. a sleep loop), then issue `vcli skill exec '1+1'`. It must
      BLOCK until the first finishes, then answer. Repeat in reverse.
6. [ ] GUI lock proof: repeat (5) with a vcli GUI screenshot/click instead
      of a SKILL read; it must also wait.
7. [ ] Kill -9 the vcli daemon mid-eval; bridge-lite must still work after
      (kernel must have released the lock).
8. [ ] `VCStopAll()` / `RBStopAll()` each stop only their own daemon.
      Careful: `VCStopAll()` runs `pgrep -f virtuoso-daemon | kill`, so it
      also kills every other vcli daemon on the host (other sessions, other
      users' if permitted). Run it only when this is the only vcli session.
9. [ ] One write test in the scratch library (place a component via each
      bridge, sequentially) and open the cellView to confirm both landed.

## Agent layer (same scratch session)

10. [ ] `scripts/vai-mcp doctor` reports vcli and bridge-lite `ok`, with
       `VAI_LOCK_FILE` equal to the value in the Virtuoso session's env.
11. [ ] Start an MCP agent in this repo (opencode via `opencode.json`, or
       Claude Code via `.mcp.json`). Both `virtuoso-vcli` and
       `virtuoso-bridge` connect and list tools.
12. [ ] Ask the agent for a harmless read on each server (bridge
       `evaluate` `1+2`, vcli `skill_exec` `getCurrentTime()`); both answer.
13. [ ] `scripts/vai-audit -f` in another terminal shows both calls live,
       with the agent's client name, `ok`, and duration.
14. [ ] Lock under an agent: while the agent runs a long bridge eval, run
       `vcli skill exec '1+1'` by hand. It waits, then answers.
15. [ ] Restart with `VAI_MODE=safe`: `evaluate`, `load_file` and
       `skill_exec` are gone from the tool lists, and a forced call is
       refused and logged as `ERR`.
16. [ ] Agent write in the scratch library: `tx_begin`, place one instance,
       `tx_diff`, `tx_rollback`. The cellview is unchanged afterwards. Then
       repeat with `tx_commit` and confirm in the GUI.
17. [ ] Repeat 11-12 with a second agent (Codex, Cursor or Gemini, configs
       in `docs/agents.md`) to confirm the setup is agent-agnostic.
