//! Cross-bridge mutation lock — added by the virtuoso-cli-integration project.
//!
//! When virtuoso-cli and virtuoso-bridge-lite drive the SAME Virtuoso
//! session, each side serializes only its own requests; nothing stops the
//! two daemons (or vcli's X11 GUI input, which bypasses the SKILL
//! interpreter entirely) from acting on the session at the same moment.
//!
//! Every mutation-capable path in this crate takes this host-level advisory
//! lock before acting; the patched bridge-lite daemon takes the same lock.
//! flock is used instead of a SKILL global because:
//!   * the kernel releases it if a daemon dies, so a crash cannot deadlock
//!     the session;
//!   * it is held by the daemon process for the whole evaluation, so a
//!     modal dialog pumping the CIW event loop mid-eval (reentrancy) cannot
//!     let a second request slip in;
//!   * it covers xdotool input, which never enters the SKILL interpreter.
//!
//! The lock serializes ALL requests between the two bridges, including
//! reads: arbitrary SKILL text cannot be classified as read vs write
//! reliably, so correctness wins over concurrency. Both bridges already
//! serialize their own requests, so this costs nothing in practice.
//!
//! Path: $VAI_LOCK_FILE, else /tmp/virtuoso-ai-bridge.lock. The path must be
//! identical on the Virtuoso host for both daemons and for the vcli GUI
//! wrapper. The default is host-wide: with several Virtuoso sessions on one
//! host, set VAI_LOCK_FILE to a per-session path in each session's
//! environment to avoid serializing unrelated sessions.
//!
//! Fail-open: if the lock file cannot be opened or flocked, the request
//! proceeds UNLOCKED with a stderr warning. A coordination aid must not
//! brick the daemon on an odd filesystem; the warning is the signal to fix
//! the path.

use std::fs::{File, OpenOptions};
use std::io;
use std::os::unix::io::AsRawFd;

pub const DEFAULT_LOCK_PATH: &str = "/tmp/virtuoso-ai-bridge.lock";

/// Effective lock-file path: $VAI_LOCK_FILE (non-empty) or the host-wide default.
pub fn lock_path() -> String {
    std::env::var("VAI_LOCK_FILE")
        .ok()
        .filter(|s| !s.is_empty())
        .unwrap_or_else(|| DEFAULT_LOCK_PATH.to_string())
}

/// RAII guard for the cross-bridge lock. Dropping releases it.
/// `file: None` means the lock could not be taken (already warned on stderr).
pub struct VaiLock {
    file: Option<File>,
}

/// Take the cross-bridge lock, blocking until held. See module docs.
pub fn acquire() -> VaiLock {
    let path = lock_path();
    match OpenOptions::new().read(true).write(true).create(true).open(&path) {
        Ok(f) => {
            let rc = unsafe { libc::flock(f.as_raw_fd(), libc::LOCK_EX) };
            if rc == 0 {
                VaiLock { file: Some(f) }
            } else {
                eprintln!(
                    "[vai-lock] flock({}) failed: {}; proceeding UNLOCKED",
                    path,
                    io::Error::last_os_error()
                );
                VaiLock { file: None }
            }
        }
        Err(e) => {
            eprintln!("[vai-lock] cannot open {}: {}; proceeding UNLOCKED", path, e);
            VaiLock { file: None }
        }
    }
}

impl Drop for VaiLock {
    fn drop(&mut self) {
        if let Some(f) = self.file.take() {
            // Explicit unlock for clarity; closing the fd would also release it.
            unsafe { libc::flock(f.as_raw_fd(), libc::LOCK_UN) };
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn lock_path_resolution() {
        // One test, sequential: parallel tests mutating the same env var race.
        std::env::remove_var("VAI_LOCK_FILE");
        assert_eq!(lock_path(), DEFAULT_LOCK_PATH);
        std::env::set_var("VAI_LOCK_FILE", "/tmp/vai-test-override.lock");
        assert_eq!(lock_path(), "/tmp/vai-test-override.lock");
        std::env::set_var("VAI_LOCK_FILE", "");
        assert_eq!(lock_path(), DEFAULT_LOCK_PATH);
        std::env::remove_var("VAI_LOCK_FILE");
    }

    #[test]
    fn second_acquire_blocks_until_first_releases() {
        std::env::set_var("VAI_LOCK_FILE", "/tmp/vai-test-serialize.lock");
        let first = acquire();
        let (tx, rx) = std::sync::mpsc::channel();
        let t = std::thread::spawn(move || {
            tx.send("waiting").unwrap();
            let _second = acquire(); // must block until `first` drops
            tx.send("acquired").unwrap()
        });
        assert_eq!(rx.recv().unwrap(), "waiting");
        std::thread::sleep(std::time::Duration::from_millis(200));
        // The second acquire must still be blocked while `first` lives.
        assert!(rx.try_recv().is_err(), "second acquire did not block");
        drop(first);
        assert_eq!(rx.recv().unwrap(), "acquired");
        t.join().unwrap();
        std::env::remove_var("VAI_LOCK_FILE");
        let _ = std::fs::remove_file("/tmp/vai-test-serialize.lock");
    }
}
