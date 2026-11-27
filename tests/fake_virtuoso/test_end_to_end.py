import fcntl
import json
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

if os.environ.get("VAI_SANDBOX") != "1":
    pytest.skip("run through tests/fake_virtuoso/run-sandboxed", allow_module_level=True)

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
WORK = Path(os.environ["VAI_WORK"])
BIN = Path(os.environ["VAI_BIN"])
PY = os.environ["VAI_PYTHON"]
LOCK = "/tmp/virtuoso-ai-bridge.lock"
MARKER = LOCK + ".quarantine"
INIT = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"protocolVersion": "2025-11-25", "capabilities": {},
                   "clientInfo": {"name": "e2e-agent", "version": "0"}}}


class Virtuoso:
    def __init__(self, name):
        self.state = WORK / name
        self.home = WORK / (name + "-home")
        self.home.mkdir(parents=True, exist_ok=True)
        self.env = dict(os.environ, HOME=str(self.home))
        self.proc = subprocess.Popen(
            [PY, str(HERE / "fake_virtuoso.py"), "--state-dir", str(self.state),
             "--vcli-daemon", str(BIN / "virtuoso-daemon"), "--bridge-python", PY],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=self.env)
        line = self.proc.stdout.readline()
        if not line:
            raise RuntimeError("fake virtuoso failed: " + self.proc.stderr.read().decode())
        self.info = json.loads(line)
        self.env.update(VB_REMOTE_HOST="localhost", VB_REMOTE_PORT=str(self.info["bridge_port"]),
                        VCLI_CAPABILITY="admin")
        subprocess.run(["virtuoso-bridge", "start"], env=self.env, capture_output=True, timeout=60, check=True)

    def vcli(self, code, timeout=None, env=None):
        cmd = [str(BIN / "vcli"), "--format", "json", "skill", "exec", code]
        if timeout:
            cmd += ["--timeout", str(timeout)]
        out = subprocess.run(cmd, env=dict(self.env, **(env or {})), capture_output=True, text=True, timeout=90)
        return json.loads(out.stdout) if out.stdout.strip() else {"status": "error", "message": out.stderr}

    def bridge(self, code, timeout=30):
        out = subprocess.run(["virtuoso-bridge", "eval", "--timeout", str(timeout), code],
                             env=self.env, capture_output=True, text=True, timeout=120)
        return json.loads(out.stdout)

    def requests(self):
        path = self.state / "requests.jsonl"
        return [json.loads(l) for l in path.read_text().splitlines()] if path.exists() else []

    def mcp(self, server, requests, extra_env=None):
        env = dict(self.env, VAI_CONFIG_DIR=str(WORK / "no-conf"), VCLI_BIN=str(BIN / "vcli"),
                   VAI_PYTHON=PY, **(extra_env or {}))
        env.pop("VCLI_CAPABILITY", None)
        stdin = "".join(json.dumps(r) + "\n" for r in [INIT] + requests)
        out = subprocess.run(["bash", str(REPO / "scripts" / "vai-mcp"), server], input=stdin,
                             capture_output=True, text=True, env=env, timeout=120, check=True)
        return {r["id"]: r for r in map(json.loads, out.stdout.splitlines()) if "id" in r}

    def stop(self):
        self.proc.send_signal(signal.SIGTERM)
        try:
            self.proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.proc.kill()


@pytest.fixture(scope="module")
def virtuoso():
    v = Virtuoso("shared")
    yield v
    v.stop()


@pytest.fixture
def fresh(request):
    if os.path.lexists(MARKER):
        os.unlink(MARKER)
    v = Virtuoso(request.node.name.replace("[", "_").replace("]", ""))
    yield v
    v.stop()
    if os.path.lexists(MARKER):
        os.unlink(MARKER)


def call(rid, name, args):
    return {"jsonrpc": "2.0", "id": rid, "method": "tools/call", "params": {"name": name, "arguments": args}}


def lock_is_free():
    fd = os.open(LOCK, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.flock(fd, fcntl.LOCK_UN)
        return True
    except BlockingIOError:
        return False
    finally:
        os.close(fd)


def in_background(fn, *args, **kwargs):
    box = {}
    t = threading.Thread(target=lambda: box.update(result=fn(*args, **kwargs)))
    t.start()
    return t, box


def test_vcli_cli_roundtrip(virtuoso):
    r = virtuoso.vcli("1+2")
    assert r["status"] == "success", r
    assert r["output"] == "3"
    assert any(q["bridge"] == "vcli" and q["skill"] == "1+2" for q in virtuoso.requests())


def test_bridge_cli_roundtrip_local_mode(virtuoso):
    r = virtuoso.bridge("6*7")
    assert r["status"] == "success", r
    assert r["output"] == "42"


def test_skill_errors_reach_both_clients(virtuoso):
    v = virtuoso.vcli("noSuchFunction(1)")
    assert v["status"] == "error" and "noSuchFunction" in json.dumps(v)
    b = virtuoso.bridge("noSuchFunction(1)")
    assert b["status"] == "error" and "noSuchFunction" in json.dumps(b)


@pytest.mark.parametrize("side", ["vcli", "bridge"])
def test_each_daemon_holds_the_lock_for_the_whole_eval(virtuoso, side):
    assert lock_is_free()
    run = virtuoso.vcli if side == "vcli" else virtuoso.bridge
    t, box = in_background(run, "sleep(2) 7")
    time.sleep(0.8)
    assert not lock_is_free(), "%s daemon must hold the cross-bridge lock mid-eval" % side
    t.join()
    assert box["result"]["status"] == "success", box
    assert lock_is_free()


@pytest.mark.parametrize("first", ["vcli", "bridge"])
def test_bridges_serialize_even_though_the_ciw_is_concurrent(virtuoso, first):
    a = virtuoso.vcli if first == "vcli" else virtuoso.bridge
    b = virtuoso.bridge if first == "vcli" else virtuoso.vcli
    tag_a, tag_b = "sleep(2) 1111%d" % len(first), "2222%d+0" % len(first)
    t, box = in_background(a, tag_a)
    time.sleep(0.5)
    started = time.monotonic()
    second = b(tag_b)
    waited = time.monotonic() - started
    t.join()
    assert box["result"]["status"] == "success" and second["status"] == "success"
    assert waited >= 1.0, "second bridge did not wait for the first (%.2fs)" % waited
    log = virtuoso.requests()
    ea = [q for q in log if tag_a in q["skill"]][-1]
    eb = [q for q in log if tag_b in q["skill"]][-1]
    assert eb["start"] >= ea["end"] - 0.05, "evaluations overlapped in the CIW"


def test_mcp_full_mode_success_failure_and_audit(virtuoso):
    audit = WORK / "audit-full.jsonl"
    env = {"VAI_MODE": "full", "VAI_AUDIT_LOG": str(audit)}
    v = virtuoso.mcp("vcli", [call(2, "skill_exec", {"code": "1+2"}),
                              call(3, "skill_exec", {"code": "noSuchFunction()"})], env)
    assert "3" in json.dumps(v[2].get("result")), v[2]
    assert v[3].get("error") or (v[3].get("result") or {}).get("isError") or \
        "error" in json.dumps(v[3].get("result")).lower(), v[3]
    b = virtuoso.mcp("bridge", [call(2, "evaluate", {"expr": "6*7"})], env)
    assert "42" in json.dumps(b[2]["result"]), b[2]
    entries = [json.loads(l) for l in audit.read_text().splitlines()]
    assert [(e["server"], e["tool"]) for e in entries] == [
        ("vcli", "skill_exec"), ("vcli", "skill_exec"), ("bridge", "evaluate")]
    assert all(e["client"] == "e2e-agent" for e in entries)
    assert entries[0]["ok"] is True and entries[2]["ok"] is True
    assert oct(audit.stat().st_mode & 0o777) == "0o600"


def test_mcp_audit_off_writes_nothing(virtuoso):
    before = set(WORK.rglob("*.jsonl"))
    r = virtuoso.mcp("vcli", [call(2, "skill_exec", {"code": "2+2"})], {"VAI_AUDIT_LOG": "off"})
    assert "4" in json.dumps(r[2].get("result"))
    new = {p for p in set(WORK.rglob("*.jsonl")) - before if "audit" in p.name}
    assert not new
    assert not (virtuoso.home / ".local" / "state" / "virtuoso-ai" / "audit.jsonl").exists()


def test_mcp_safe_mode_never_reaches_the_ciw(virtuoso):
    env = {"VAI_MODE": "safe", "VCLI_CAPABILITY": "admin", "VAI_DENY_TOOLS": "load_file",
           "VAI_AUDIT_LOG": str(WORK / "audit-safe.jsonl")}
    before = len(virtuoso.requests())
    v = virtuoso.mcp("vcli", [call(2, "skill_exec", {"code": "safeModeProbe()"})], env)
    b = virtuoso.mcp("bridge", [call(2, "evaluate", {"expr": "safeModeProbe()"})], env)
    assert v[2].get("error") or (v[2].get("result") or {}).get("isError")
    assert (b[2].get("result") or {}).get("isError") or b[2].get("error")
    assert not [q for q in virtuoso.requests()[before:] if "safeModeProbe" in q["skill"]]


def test_kill9_of_vcli_daemon_mid_eval_releases_the_lock(fresh):
    t, box = in_background(fresh.vcli, "sleep(5) 1")
    time.sleep(0.8)
    assert not lock_is_free()
    os.kill(fresh.info["vcli_pid"], signal.SIGKILL)
    t.join(timeout=30)
    deadline = time.monotonic() + 5
    while not lock_is_free() and time.monotonic() < deadline:
        time.sleep(0.1)
    assert lock_is_free(), "kernel must release the flock when the daemon dies"
    r = fresh.bridge("1+1")
    assert r["status"] == "success" and r["output"] == "2", r


def test_timeout_quarantines_both_bridges_until_marker_removed(fresh):
    r = fresh.vcli("sleep(4) 1", timeout=1)
    assert r["status"] == "error", r
    assert os.path.lexists(MARKER), "an uncertain timeout must write the quarantine marker"
    refused = fresh.bridge("1+1")
    assert refused["status"] == "error" and "quarantine" in json.dumps(refused).lower(), refused
    time.sleep(4)
    os.unlink(MARKER)
    ok = fresh.bridge("1+1")
    assert ok["status"] == "success" and ok["output"] == "2", ok


def test_live_acceptance_script_passes_against_the_fake(fresh):
    report = WORK / "acceptance-report.json"
    env = dict(fresh.env, VAI_CONFIG_DIR=str(WORK / "no-conf"), VAI_PYTHON=PY)
    env.pop("VCLI_CAPABILITY", None)
    p = subprocess.run([PY, str(REPO / "scripts" / "vai-acceptance"), "--quarantine", "--sleep", "3",
                        "--report", str(report)], env=env, capture_output=True, text=True, timeout=600)
    print(p.stdout)
    results = json.loads(report.read_text())["results"]
    failures = [r for r in results if r["status"] == "FAIL"]
    assert not failures, p.stdout + p.stderr
    passed = {r["step"] for r in results if r["status"] == "PASS"}
    assert {"3", "4", "5a", "5b", "5c", "5d", "11", "12", "12b", "13", "15", "Q", "Q2"} <= passed, p.stdout
