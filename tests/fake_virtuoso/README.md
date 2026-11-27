# Fake Virtuoso (sandboxed end-to-end tests)

`fake_virtuoso.py` plays the CIW's side of both bridges: it starts the real
`virtuoso-daemon` (vcli) and `ramic_bridge_daemon_3.py` (bridge-lite) the
way their `.il` files do, answers their SKILL requests with the small
evaluator in `skill_eval.py`, and returns results in each bridge's real
framing (vcli: callback files; bridge-lite: tagged STX/NAK frames on the
daemon's stdin). It evaluates requests concurrently on purpose, so the
serialization tests can only pass because of the cross-bridge `flock`.

```sh
tests/fake_virtuoso/run-sandboxed      # builds, then runs pytest inside bwrap
VAI_SKIP_BUILD=1 tests/fake_virtuoso/run-sandboxed -k quarantine
```

The sandbox: read-only root, private `/tmp` (so the default lock and
quarantine marker are throwaway), fresh `HOME`, network namespace with only
loopback, own PID/IPC/UTS namespaces, cleared environment. The tests skip
themselves outside it.

Not covered here: real SKILL semantics, OpenAccess/cellview writes, the GUI.
Those are `ACCEPTANCE.md` on a licensed session. The evaluator is a small
subset written for these tests; [OpenSKILL](https://github.com/sohamxda7/openskill)
(GPL-3.0, standalone SKILL interpreter) could replace it for broader core-SKILL
coverage, but it is not vendored or required.
