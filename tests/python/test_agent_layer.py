import json
import pytest
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


def find_vcli():
    for c in (os.environ.get("VCLI_BIN"), os.path.join(REPO_ROOT, "target", "release", "vcli"),
              os.path.join(REPO_ROOT, "target", "debug", "vcli")):
        if c and os.access(c, os.X_OK):
            return c
    return None


def run_launcher(tmp_path, server, requests, extra_env=None, env_files=None, check=True):
    conf = tmp_path / "conf"
    conf.mkdir(exist_ok=True)
    for name, text in (env_files or {}).items():
        (conf / name).write_text(text)
    audit = tmp_path / "audit.jsonl"
    env = dict(os.environ, VAI_PYTHON=sys.executable, VAI_AUDIT_LOG=str(audit),
               VAI_LOCK_FILE=str(tmp_path / "x.lock"), VAI_CONFIG_DIR=str(conf),
               HOME=str(tmp_path / "home"))
    for k in ("VAI_DENY_TOOLS", "VCLI_CAPABILITY", "VAI_MODE"):
        env.pop(k, None)
    vcli = find_vcli()
    if vcli:
        env["VCLI_BIN"] = vcli
    env.update(extra_env or {})
    stdin = "".join(json.dumps(r) + "\n" for r in [INIT] + requests)
    proc = subprocess.run(["bash", LAUNCHER, server], input=stdin, capture_output=True,
                          text=True, env=env, timeout=120, check=check)
    replies = {r["id"]: r for r in map(json.loads, proc.stdout.splitlines()) if "id" in r}
    return proc, replies


def refused(reply):
    return bool(reply.get("error")) or bool((reply.get("result") or {}).get("isError"))


@pytest.mark.parametrize("source", ["shell", "env_file"])
def test_bridge_safe_mode_merges_inherited_denies(tmp_path, source):
    kwargs = ({"extra_env": {"VAI_MODE": "safe", "VAI_DENY_TOOLS": "load_file"}} if source == "shell"
              else {"env_files": {"bridge.env": "VAI_MODE=safe\nVAI_DENY_TOOLS=load_file\n"}})
    _, replies = run_launcher(tmp_path, "bridge", [LIST, call(3, "evaluate", {"expr": "1+2"})], **kwargs)
    names = tool_names(replies[2])
    assert "evaluate" not in names and "load_file" not in names
    assert refused(replies[3])


vcli_required = pytest.mark.skipif(find_vcli() is None, reason="needs a built vcli (cargo build)")


@vcli_required
@pytest.mark.parametrize("source", ["shell", "env_file"])
def test_vcli_safe_mode_strips_inherited_admin(tmp_path, source):
    kwargs = ({"extra_env": {"VAI_MODE": "safe", "VCLI_CAPABILITY": "Admin, schematic"}} if source == "shell"
              else {"env_files": {"vcli.env": "VAI_MODE=safe\nVCLI_CAPABILITY=admin\n"}})
    _, replies = run_launcher(tmp_path, "vcli", [LIST, call(3, "skill_exec", {"code": "1+2"})], **kwargs)
    names = tool_names(replies[2])
    assert not {"skill_exec", "skill_load", "skill_eval"} & names
    assert refused(replies[3])


@vcli_required
def test_vcli_full_mode_exposes_raw_skill(tmp_path):
    _, replies = run_launcher(tmp_path, "vcli", [LIST], extra_env={"VAI_MODE": "full"})
    assert "skill_exec" in tool_names(replies[2])


def test_launcher_rejects_non_fork_vcli(tmp_path):
    impostor = tmp_path / "vcli"
    impostor.write_text("#!/bin/sh\necho RBIpcDataHandler\n")
    impostor.chmod(0o755)
    proc, _ = run_launcher(tmp_path, "vcli", [], extra_env={"VCLI_BIN": str(impostor)}, check=False)
    assert proc.returncode != 0
    assert "not this repo's namespaced vcli" in proc.stderr


def test_bridge_audit_tightens_existing_file_permissions(tmp_path):
    audit = tmp_path / "audit.jsonl"
    audit.write_text("")
    audit.chmod(0o644)
    run_launcher(tmp_path, "bridge", [call(3, "list_pdk", {})])
    assert oct(audit.stat().st_mode & 0o777) == "0o600"
    assert len(audit.read_text().splitlines()) == 1


def test_bridge_cli_falls_back_to_path(tmp_path, monkeypatch):
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "mcp_server_under_test", os.path.join(REPO_ROOT, "python", "mcp_server.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    bindir = tmp_path / "bin"
    bindir.mkdir()
    cli = bindir / "virtuoso-bridge"
    cli.write_text("#!/bin/sh\n")
    cli.chmod(0o755)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "elsewhere" / "python"))
    monkeypatch.setenv("PATH", str(bindir))
    assert mod._bridge_executable() == str(cli)
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    with pytest.raises(FileNotFoundError):
        mod._bridge_executable()
