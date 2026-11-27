import json
import os
import subprocess
import sys

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
LAUNCHER = os.path.join(REPO_ROOT, "scripts", "vai-mcp")

INIT = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"protocolVersion": "2025-11-25", "capabilities": {},
                   "clientInfo": {"name": "test-agent", "version": "0"}}}


def run_bridge(tmp_path, mode, requests):
    audit = tmp_path / "audit.jsonl"
    env = dict(os.environ, VAI_PYTHON=sys.executable, VAI_MODE=mode,
               VAI_AUDIT_LOG=str(audit), VAI_LOCK_FILE=str(tmp_path / "x.lock"),
               VAI_CONFIG_DIR=str(tmp_path / "noconf"))
    env.pop("VAI_DENY_TOOLS", None)
    stdin = "".join(json.dumps(r) + "\n" for r in [INIT] + requests)
    out = subprocess.run(["bash", LAUNCHER, "bridge"], input=stdin, capture_output=True,
                         text=True, env=env, timeout=120, check=True).stdout
    replies = {r["id"]: r for r in map(json.loads, out.splitlines()) if "id" in r}
    entries = [json.loads(l) for l in audit.read_text().splitlines()] if audit.exists() else []
    return replies, entries


def call(rid, name, args):
    return {"jsonrpc": "2.0", "id": rid, "method": "tools/call",
            "params": {"name": name, "arguments": args}}


LIST = {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}


def tool_names(reply):
    return {t["name"] for t in reply["result"]["tools"]}


def test_full_mode_exposes_raw_skill_and_audits_calls(tmp_path):
    replies, entries = run_bridge(tmp_path, "full", [LIST, call(3, "list_pdk", {})])
    assert "evaluate" in tool_names(replies[2])
    assert [e["tool"] for e in entries] == ["list_pdk"]
    e = entries[0]
    assert e["server"] == "bridge" and e["client"] == "test-agent" and e["ok"] is True
    assert oct(os.stat(tmp_path / "audit.jsonl").st_mode & 0o777) == "0o600"


def test_safe_mode_hides_and_refuses_raw_skill(tmp_path):
    replies, entries = run_bridge(tmp_path, "safe", [LIST, call(3, "evaluate", {"expr": "1+2"})])
    assert "evaluate" not in tool_names(replies[2])
    assert "load_file" not in tool_names(replies[2])
    assert replies[3]["result"]["isError"] is True
    assert entries[-1]["tool"] == "evaluate" and entries[-1]["ok"] is False
    assert "VAI_DENY_TOOLS" in entries[-1]["error"]


def test_audit_off_writes_nothing(tmp_path):
    env = dict(os.environ, VAI_PYTHON=sys.executable, VAI_AUDIT_LOG="off",
               XDG_STATE_HOME=str(tmp_path), VAI_CONFIG_DIR=str(tmp_path / "noconf"))
    stdin = json.dumps(INIT) + "\n" + json.dumps(call(3, "list_pdk", {})) + "\n"
    subprocess.run(["bash", LAUNCHER, "bridge"], input=stdin, capture_output=True,
                   text=True, env=env, timeout=120, check=True)
    assert not (tmp_path / "virtuoso-ai" / "audit.jsonl").exists()
