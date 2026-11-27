# Acceptance checklist — real Virtuoso, scratch library ONLY

Run on the lab machine in a throwaway library. Until every box passes,
this toolset is experimental.

1. [ ] Install both sides from this repo: `./install.sh --user` (or
      `cargo install --path .`) and `uv pip install ./python` (or pip).
2. [ ] Start ONE Virtuoso session in a scratch library. Load bridge-lite's
      `ramic_bridge.il` (under
      `python/src/virtuoso_bridge/virtuoso/basic/resources/`), then vcli's
      (namespaced) `resources/ramic_bridge.il`, in the same CIW. Both must
      load without "already defined"/unbound errors.
3. [ ] Harmless read through bridge-lite (`virtuoso-bridge eval '1+2'` → 3).
4. [ ] Harmless read through vcli (`vcli skill exec 'getCurrentTime()'`).
5. [ ] Lock proof: start a long-running SKILL eval through bridge-lite
      (e.g. a sleep loop), then issue `vcli skill exec '1+1'`. It must
      BLOCK until the first finishes, then answer. Repeat in reverse.
6. [ ] GUI lock proof: repeat (5) with a vcli GUI screenshot/click instead
      of a SKILL read; it must also wait.
7. [ ] Kill -9 the vcli daemon mid-eval; bridge-lite must still work after
      (kernel must have released the lock).
8. [ ] `VCStopAll()` / `RBStopAll()` each stop only their own daemon.
9. [ ] One write test in the scratch library (place a component via each
      bridge, sequentially) and open the cellView to confirm both landed.
