use serde_json::Value;
use std::path::PathBuf;
use std::sync::OnceLock;

pub const SERVER: &str = "vcli";
const ARGS_LIMIT: usize = 4000;
const ERROR_LIMIT: usize = 500;

static CLIENT: OnceLock<String> = OnceLock::new();

pub fn set_client(initialize_params: &Value) {
    if let Some(name) = initialize_params
        .pointer("/clientInfo/name")
        .and_then(|v| v.as_str())
    {
        let _ = CLIENT.set(name.to_string());
    }
}

fn client() -> &'static str {
    CLIENT.get().map(String::as_str).unwrap_or("unknown")
}

pub fn log_path() -> Option<PathBuf> {
    match std::env::var("VAI_AUDIT_LOG") {
        Ok(v) if matches!(v.trim(), "0" | "off" | "false" | "none") => None,
        Ok(v) if !v.trim().is_empty() => Some(PathBuf::from(v.trim())),
        _ => default_path(),
    }
}

fn default_path() -> Option<PathBuf> {
    let base = std::env::var_os("XDG_STATE_HOME")
        .filter(|v| !v.is_empty())
        .map(PathBuf::from)
        .or_else(|| dirs::home_dir().map(|h| h.join(".local").join("state")))?;
    Some(base.join("virtuoso-ai").join("audit.jsonl"))
}

fn clip(s: &str, limit: usize) -> String {
    if s.len() <= limit {
        return s.to_string();
    }
    let mut end = limit;
    while !s.is_char_boundary(end) {
        end -= 1;
    }
    format!("{}...[+{} bytes]", &s[..end], s.len() - end)
}

pub fn entry(tool: &str, args: &Value, error: Option<&str>, ms: u128) -> Value {
    let args_text = args.to_string();
    let args_value = if args_text.len() <= ARGS_LIMIT {
        args.clone()
    } else {
        Value::String(clip(&args_text, ARGS_LIMIT))
    };
    serde_json::json!({
        "ts": chrono::Local::now().to_rfc3339_opts(chrono::SecondsFormat::Millis, false),
        "server": SERVER,
        "client": client(),
        "user": std::env::var("USER").unwrap_or_default(),
        "pid": std::process::id(),
        "tool": tool,
        "args": args_value,
        "ok": error.is_none(),
        "error": error.map(|e| clip(e, ERROR_LIMIT)),
        "ms": ms,
    })
}

pub fn record(tool: &str, args: &Value, error: Option<&str>, ms: u128) {
    let Some(path) = log_path() else {
        return;
    };
    write_line(&path, &entry(tool, args, error, ms));
}

fn write_line(path: &std::path::Path, value: &Value) {
    crate::runtime_paths::append_private_line(path, &value.to_string());
}

pub fn result_error(result: &Value) -> Option<String> {
    if result.get("isError").and_then(|v| v.as_bool()) != Some(true) {
        return None;
    }
    let text = result
        .pointer("/content/0/text")
        .and_then(|v| v.as_str())
        .unwrap_or("tool reported isError");
    Some(text.to_string())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn clip_respects_char_boundaries() {
        let s = "é".repeat(10);
        let out = clip(&s, 5);
        assert!(out.starts_with("éé"));
        assert!(out.contains("[+16 bytes]"));
        assert_eq!(clip("short", 10), "short");
    }

    #[test]
    fn entry_truncates_large_args_and_marks_errors() {
        let big = serde_json::json!({ "code": "x".repeat(ARGS_LIMIT * 2) });
        let e = entry("skill_exec", &big, Some("boom"), 7);
        assert_eq!(e["server"], "vcli");
        assert_eq!(e["tool"], "skill_exec");
        assert_eq!(e["ok"], false);
        assert_eq!(e["error"], "boom");
        assert!(e["args"].as_str().unwrap().len() < ARGS_LIMIT + 40);
        let ok = entry("util_ping", &serde_json::json!({}), None, 1);
        assert_eq!(ok["ok"], true);
        assert!(ok["error"].is_null());
    }

    #[test]
    fn result_error_reads_is_error_payloads() {
        let err =
            serde_json::json!({"isError": true, "content": [{"type": "text", "text": "nope"}]});
        assert_eq!(result_error(&err).as_deref(), Some("nope"));
        assert_eq!(result_error(&serde_json::json!({"content": []})), None);
    }

    #[test]
    #[serial_test::serial]
    fn log_path_honours_env_and_off_switch() {
        let old = std::env::var_os("VAI_AUDIT_LOG");
        std::env::set_var("VAI_AUDIT_LOG", "off");
        assert!(log_path().is_none());
        std::env::set_var("VAI_AUDIT_LOG", "/tmp/x/audit.jsonl");
        assert_eq!(log_path(), Some(PathBuf::from("/tmp/x/audit.jsonl")));
        match old {
            Some(v) => std::env::set_var("VAI_AUDIT_LOG", v),
            None => std::env::remove_var("VAI_AUDIT_LOG"),
        }
    }

    #[cfg(unix)]
    #[test]
    fn write_line_tightens_loose_permissions_and_refuses_symlinks() {
        use std::os::unix::fs::PermissionsExt;
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("audit.jsonl");
        std::fs::write(&path, "").unwrap();
        std::fs::set_permissions(&path, std::fs::Permissions::from_mode(0o644)).unwrap();
        write_line(&path, &entry("a", &serde_json::json!({}), None, 1));
        let mode = std::fs::metadata(&path).unwrap().permissions().mode() & 0o777;
        assert_eq!(mode, 0o600);
        assert_eq!(std::fs::read_to_string(&path).unwrap().lines().count(), 1);

        let target = dir.path().join("elsewhere");
        let link = dir.path().join("link.jsonl");
        std::os::unix::fs::symlink(&target, &link).unwrap();
        write_line(&link, &entry("b", &serde_json::json!({}), None, 1));
        assert!(!target.exists(), "audit must not follow a symlink");

        let fresh = dir.path().join("new").join("deeper").join("audit.jsonl");
        write_line(&fresh, &entry("c", &serde_json::json!({}), None, 1));
        let dmode = std::fs::metadata(fresh.parent().unwrap())
            .unwrap()
            .permissions()
            .mode()
            & 0o777;
        assert_eq!(dmode, 0o700);
    }

    #[test]
    fn write_line_appends_jsonl() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("nested").join("audit.jsonl");
        write_line(&path, &entry("a", &serde_json::json!({}), None, 1));
        write_line(&path, &entry("b", &serde_json::json!({}), Some("e"), 2));
        let text = std::fs::read_to_string(&path).unwrap();
        let lines: Vec<Value> = text
            .lines()
            .map(|l| serde_json::from_str(l).unwrap())
            .collect();
        assert_eq!(lines.len(), 2);
        assert_eq!(lines[0]["tool"], "a");
        assert_eq!(lines[1]["ok"], false);
    }
}
