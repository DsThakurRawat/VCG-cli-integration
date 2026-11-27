# 0001: Dual-bridge coexistence on one Virtuoso session

Status: accepted (2026-11-27), experimental until ACCEPTANCE.md passes on a
licensed session.

## Context

virtuoso-cli (vcli, Rust) and virtuoso-bridge-lite (Python) both descend
from the same "RAMIC Bridge" SKILL daemon. Unmodified, they cannot share one
CIW: both define the same SKILL globals (`RBStart`, `RBPort`, `RBIpc`,
about 25 more), so the second load clobbers the first, and each bridge's
request queue knows nothing about the other, so two clients could act on the
same cellView at the same moment.

## Decision

1. Namespace the vcli fork: every `RB*` SKILL symbol becomes `VC*`, and its
   env vars become `VC_DAEMON_PATH` / `VC_SSH_PORT` (227 mechanical renames).
   Both daemons can then live in one CIW with zero shared symbols. The
   bridge-lite side keeps its upstream `RB*` names.
2. Serialize the two bridges with one host-level advisory lock
   (`$VAI_LOCK_FILE`, default `/tmp/virtuoso-ai-bridge.lock`). The Rust
   crate takes it in `src/vai_lock.rs` on every request path; the vendored
   bridge-lite daemons (`python/`, and the fallback daemons in
   `resources/daemons/`) take the same flock for the whole of every
   evaluation; vcli's xdotool GUI path takes it too.
3. Vendor bridge-lite into `python/` (see `python/VENDORED.md`) instead of
   shipping a patch to apply to a separate clone. One repo installs both
   sides.

## Why flock and not a SKILL global

- The kernel releases an flock when a daemon dies; a crash cannot deadlock
  the session. A SKILL global would stay set.
- The daemon process holds the lock for the whole evaluation, so a modal
  dialog pumping the CIW event loop mid-eval (reentrancy) cannot let a
  second request slip in.
- It covers xdotool GUI input, which never enters the SKILL interpreter.
- Opened O_RDWR: LOCK_EX on an O_RDONLY descriptor fails with EBADF on NFS,
  and EDA hosts often mount home/scratch over NFS.
- Fail-open with a stderr warning if the lock file is unusable: a
  coordination aid must not wedge a daemon on an odd filesystem.
- Platform scope: flock exists only on Unix, and both daemons (and the
  Virtuoso host they run on) are always Linux. On non-Unix builds (the
  Windows client is ssh-only and never runs a local SKILL mutation),
  `src/vai_lock.rs` compiles to an explicit no-op that logs a warning and
  never claims to hold a lock. Mutual exclusion on the host is unchanged.

## Consequences

- The lock serializes ALL requests between the two bridges, including reads:
  arbitrary SKILL text cannot be classified as read vs write reliably, so
  correctness wins over concurrency. Both bridges already serialize their
  own requests, so this costs nothing in practice.
- A drag is mousedown + moves + mouseup and must run under ONE lock hold;
  locking each step separately would let the other bridge inject input
  between mousedown and mouseup. If a locked drag sequence stops mid-drag
  with the button still down, the guard's Drop issues a best-effort mouseup
  as a follow-up command so the session is not left with a pressed button.
- With several Virtuoso sessions on one host, set `VAI_LOCK_FILE` to a
  per-session path in each session's environment; the default is host-wide.
