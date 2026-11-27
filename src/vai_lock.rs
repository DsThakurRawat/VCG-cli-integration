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
                VaiLock { file: Some(f) }
            } else {
                eprintln!(
                    "[vai-lock] flock({}) failed: {}; proceeding UNLOCKED",
                    path,
                    std::io::Error::last_os_error()
                );
                VaiLock { file: None }
            }
        }
        Err(e) => {
            eprintln!(
                "[vai-lock] cannot open {}: {}; proceeding UNLOCKED",
                path, e
            );
            VaiLock { file: None }
        }
    }
}

#[cfg(not(unix))]
pub fn acquire() -> VaiLock {
    eprintln!("[vai-lock] flock is unavailable on this platform; proceeding UNLOCKED");
    VaiLock { file: None }
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
