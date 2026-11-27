# virtuoso-cli-integration

**Experimental.** One Cadence Virtuoso session driven by two open-source AI
bridges — [virtuoso-cli](https://github.com/deanyou/virtuoso-cli) (vcli) and
[virtuoso-bridge-lite](https://github.com/Arcadia-1/virtuoso-bridge-lite) —
without them corrupting each other's control of the session.

Status: **the currently published revision fails CI** (the build is broken:
two source files under `src/target/` were left out of this upload, and the
comment-budget check is over its baseline). The test counts quoted below came
from a working copy, not from this published tree. A corrected, self-contained
revision is in progress. **Not yet run against a licensed Virtuoso session**;
treat it as experimental until the checklist in `ACCEPTANCE.md` passes on a
scratch library.

## Why this exists

Both projects descend from the same "RAMIC Bridge" SKILL daemon and define
the same SKILL globals (`RBStart`, `RBPort`, `RBIpc`, ~25 more). Loading both
unmodified into one CIW makes the second load clobber the first, and each
bridge's request queue knows nothing about the other — two AI tools could
edit the same cellView at the same moment.

## What this repo changes

1. **Namespaced fork of vcli** (this tree): every `RB*` SKILL symbol becomes
   `VC*`, its env vars become `VC_DAEMON_PATH` / `VC_SSH_PORT`, so both
   daemons can live in one CIW with zero shared symbols. 227 renames, all
   mechanical.
2. **Cross-bridge mutation lock** (`src/vai_lock.rs` +
   `docs/integration/bridge-lite-vai-lock.patch` for bridge-lite's Python
   daemons): one host-level `flock` file (`$VAI_LOCK_FILE`, default
   `/tmp/virtuoso-ai-bridge.lock`) that each bridge's daemon holds for the
   whole of every evaluation, and that vcli's xdotool GUI path also takes.
   flock because the kernel releases it on daemon death, it survives
   modal-dialog event-loop reentrancy, and it covers GUI input that never
   enters the SKILL interpreter. Opened O_RDWR so it also works on NFS.
   Fail-open with a stderr warning if the path is unusable.

## Setup notes that matter

- Apply `docs/integration/bridge-lite-vai-lock.patch` to your
  virtuoso-bridge-lite clone (`git apply`, base commit 2cc3d066, 2026-11-26).
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

## Test status (this tree, 2026-11-27)

- `cargo test --workspace`: **1428 lib + 207 integration tests pass, 0 fail**
  (includes 4 new `vai_lock` tests, incl. a blocking-serialization test).
- bridge-lite daemon lock patch: py3 daemon compiles; helper block exercised
  against real `flock` — cross-process exclusivity verified (a second opener
  blocks until release). The first draft of that helper locked nothing at
  all; the test caught it, the fix is what's shipped here.
- Base commits: vcli `b3af05ff` (v1.3.6, 2026-11-25), bridge-lite `2cc3d066`
  (2026-11-26, includes the Nov 26 upstream transport fixes).
- **Not covered:** any live Virtuoso behavior. SKILL load-time, dual-daemon
  coexistence and the lock under real CIW load are unverified until
  `ACCEPTANCE.md` is run on a real (scratch) session.

## Licenses

MIT (both upstreams). See `LICENSE` (vcli) and `NOTICE` for attribution.
virtuoso-bridge-lite's license ships with its own repo; the patch here only
contains our additions.
