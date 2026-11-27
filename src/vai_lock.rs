//! Cross-bridge mutation lock - rationale: docs/decision-records/0001-dual-bridge-coexistence.md

use std::fs::File;
#[cfg(unix)]
use std::fs::OpenOptions;
#[cfg(unix)]
use std::os::unix::io::AsRawFd;

pub const DEFAULT_LOCK_PATH: &str = "/tmp/virtuoso-ai-bridge.lock";

pub fn lock_path() -> String {
    std::env::var("VAI_LOCK_FILE")
        .ok()
        .filter(|s| !s.is_empty())
        .unwrap_or_else(|| DEFAULT_LOCK_PATH.to_string())
}

pub struct VaiLock {
    file: Option<File>,
    supported: bool,
}

impl VaiLock {
    /// True when this handle holds the kernel lock.
    pub fn held(&self) -> bool {
        self.file.is_some()
    }

    /// False on platforms with no flock: callers must not treat
    /// `!held()` there as an acquisition error.
    pub fn coordination_supported(&self) -> bool {
        self.supported
    }
}

fn quarantine_marker_for(lock: &str) -> String {
    format!("{lock}.quarantine")
}

pub fn quarantine_path() -> String {
    quarantine_marker_for(&lock_path())
}

fn quarantined_at(path: &str) -> Option<String> {
    match std::fs::read_to_string(path) {
        Ok(body) => {
            let body = body.trim().to_string();
            let reason = if body.is_empty() {
                "reason not recorded".to_string()
            } else {
                body
            };
            Some(format!(
                "cross-bridge quarantine active: {reason} (remove {path} only after the Virtuoso session is confirmed idle)"
            ))
        }
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => None,
        Err(e) => Some(format!(
            "cross-bridge quarantine marker {path} is unreadable ({e}); treating this Virtuoso session as quarantined until the marker can be read or removed after the session is confirmed idle"
        )),
    }
}

pub fn quarantined() -> Option<String> {
    quarantined_at(&quarantine_path())
}

fn enter_quarantine_at(path: &str, reason: &str) -> bool {
    match std::fs::write(path, reason) {
        Ok(()) => {
            eprintln!("[vai-lock] QUARANTINE entered: {reason}");
            true
        }
        Err(e) => {
            eprintln!("[vai-lock] cannot write quarantine marker {path}: {e}");
            false
        }
    }
}

pub fn enter_quarantine(reason: &str) -> bool {
    enter_quarantine_at(&quarantine_path(), reason)
}

#[cfg(unix)]
pub fn acquire() -> VaiLock {
    let path = lock_path();
    match OpenOptions::new()
        .read(true)
        .write(true)
        .create(true)
        .truncate(false)
        .open(&path)
    {
        Ok(f) => {
            let rc = unsafe { libc::flock(f.as_raw_fd(), libc::LOCK_EX) };
            if rc == 0 {
                VaiLock {
                    file: Some(f),
                    supported: true,
                }
            } else {
                eprintln!(
                    "[vai-lock] flock({}) failed: {}; mutations must be refused",
                    path,
                    std::io::Error::last_os_error()
                );
                VaiLock {
                    file: None,
                    supported: true,
                }
            }
        }
        Err(e) => {
            eprintln!(
                "[vai-lock] cannot open {}: {}; mutations must be refused",
                path, e
            );
            VaiLock {
                file: None,
                supported: true,
            }
        }
    }
}

#[cfg(not(unix))]
pub fn acquire() -> VaiLock {
    eprintln!("[vai-lock] flock is unavailable on this platform; coordination unsupported");
    VaiLock {
        file: None,
        supported: false,
    }
}

impl Drop for VaiLock {
    fn drop(&mut self) {
        if let Some(_f) = self.file.take() {
            #[cfg(unix)]
            unsafe {
                libc::flock(_f.as_raw_fd(), libc::LOCK_UN)
            };
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn lock_path_resolution() {
        std::env::remove_var("VAI_LOCK_FILE");
        assert_eq!(lock_path(), DEFAULT_LOCK_PATH);
        std::env::set_var("VAI_LOCK_FILE", "/tmp/vai-test-override.lock");
        assert_eq!(lock_path(), "/tmp/vai-test-override.lock");
        std::env::set_var("VAI_LOCK_FILE", "");
        assert_eq!(lock_path(), DEFAULT_LOCK_PATH);
        std::env::remove_var("VAI_LOCK_FILE");
    }

    #[test]
    fn quarantine_path_appends_suffix() {
        assert_eq!(
            quarantine_marker_for("/tmp/x.lock"),
            "/tmp/x.lock.quarantine"
        );
    }

    #[test]
    fn quarantine_marker_roundtrip() {
        let p = std::env::temp_dir().join(format!(
            "vai-test-quarantine-{}.lock.quarantine",
            std::process::id()
        ));
        let ps = p.to_string_lossy().to_string();
        let _ = std::fs::remove_file(&ps);
        assert!(quarantined_at(&ps).is_none());
        assert!(enter_quarantine_at(&ps, "timeout during eval"));
        let msg = quarantined_at(&ps).expect("quarantine marker readable");
        assert!(msg.contains("timeout during eval"));
        assert!(msg.contains("confirmed idle"));
        let _ = std::fs::remove_file(&ps);
        assert!(quarantined_at(&ps).is_none());
    }

    #[test]
    fn unreadable_marker_is_treated_as_active() {
        let p = std::env::temp_dir().join(format!(
            "vai-test-unreadable-{}.lock.quarantine",
            std::process::id()
        ));
        let ps = p.to_string_lossy().to_string();
        let _ = std::fs::remove_dir_all(&ps);
        std::fs::create_dir(&ps).unwrap();
        let msg = quarantined_at(&ps).expect("unreadable marker must read as active");
        assert!(msg.contains("unreadable"));
        let _ = std::fs::remove_dir_all(&ps);
        assert!(quarantined_at(&ps).is_none());
    }

    #[cfg(unix)]
    #[test]
    fn held_reports_lock_state() {
        let p = std::env::temp_dir().join(format!("vai-test-held-{}.lock", std::process::id()));
        let ps = p.to_string_lossy().to_string();
        std::env::set_var("VAI_LOCK_FILE", &ps);
        let guard = acquire();
        assert!(guard.coordination_supported());
        assert!(guard.held());
        drop(guard);
        std::env::remove_var("VAI_LOCK_FILE");
        let _ = std::fs::remove_file(&ps);
    }

    #[cfg(unix)]
    #[test]
    fn second_acquire_blocks_until_first_releases() {
        std::env::set_var("VAI_LOCK_FILE", "/tmp/vai-test-serialize.lock");
        let first = acquire();
        let (tx, rx) = std::sync::mpsc::channel();
        let t = std::thread::spawn(move || {
            tx.send("waiting").unwrap();
            let _second = acquire();
            tx.send("acquired").unwrap()
        });
        assert_eq!(rx.recv().unwrap(), "waiting");
        std::thread::sleep(std::time::Duration::from_millis(200));
        assert!(rx.try_recv().is_err(), "second acquire did not block");
        drop(first);
        assert_eq!(rx.recv().unwrap(), "acquired");
        t.join().unwrap();
        std::env::remove_var("VAI_LOCK_FILE");
        let _ = std::fs::remove_file("/tmp/vai-test-serialize.lock");
    }
}
