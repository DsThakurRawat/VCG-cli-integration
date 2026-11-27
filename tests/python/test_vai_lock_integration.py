import fcntl
import importlib.util
import os
import subprocess
import sys
import time

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))

DAEMON_PATHS = [
    "python/src/virtuoso_bridge/virtuoso/basic/resources/ramic_bridge_daemon_3.py",
    "python/src/virtuoso_bridge/virtuoso/basic/resources/ramic_bridge_daemon_27.py",
    "resources/daemons/ramic_bridge_daemon_3.py",
    "resources/daemons/ramic_bridge_daemon_27.py",
]


def load_daemon_module(rel, lock_path):
    path = os.path.join(REPO_ROOT, rel)
    spec = importlib.util.spec_from_file_location("daemon_under_test", path)
    mod = importlib.util.module_from_spec(spec)
    old_argv = sys.argv
    old_env = os.environ.get("VAI_LOCK_FILE")
    old_stdin = sys.stdin
    sys.argv = ["daemon", "127.0.0.1", "0"]
    os.environ["VAI_LOCK_FILE"] = lock_path
    devnull = open(os.devnull, "rb")
    sys.stdin = devnull
    try:
        spec.loader.exec_module(mod)
    finally:
        sys.argv = old_argv
        sys.stdin = old_stdin
        devnull.close()
        if old_env is None:
            os.environ.pop("VAI_LOCK_FILE", None)
        else:
            os.environ["VAI_LOCK_FILE"] = old_env
    return mod


def test_all_daemon_copies_carry_the_lock():
    for rel in DAEMON_PATHS:
        with open(os.path.join(REPO_ROOT, rel), encoding="utf-8") as fh:
            text = fh.read()
        assert "_vai_lock_acquire" in text, rel
        assert "fcntl.flock" in text or "_flock_fn" in text, rel
        assert "VAI_LOCK_FILE" in text, rel


def test_cross_process_exclusion_and_release(tmp_path):
    lock_path = str(tmp_path / "vai.lock")
    mod = load_daemon_module(DAEMON_PATHS[0], lock_path)
    child_src = (
        "import importlib.util,sys,os,time;"
        "lock,path=sys.argv[1],sys.argv[2];"
        "os.environ['VAI_LOCK_FILE']=lock;"
        "sys.argv=['d','127.0.0.1','0'];"
        "spec=importlib.util.spec_from_file_location('d',path);"
        "m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);"
        "fd=m._vai_lock_acquire();"
        "print('held',flush=True);"
        "time.sleep(30)"
    )
    child = subprocess.Popen(
        [sys.executable, "-c", child_src, lock_path,
         os.path.join(REPO_ROOT, DAEMON_PATHS[0])],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    try:
        assert child.stdout is not None
        assert child.stdout.readline().strip() == "held"
        fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            raise AssertionError("acquired the lock while the child holds it")
        except BlockingIOError:
            pass
        finally:
            os.close(fd)
        child.kill()
        child.wait(timeout=10)
        fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        finally:
            os.close(fd)
        mod_fd = mod._vai_lock_acquire()
        assert mod_fd is not None
        mod._vai_lock_release(mod_fd)
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(timeout=10)


def test_unusable_lock_path_returns_none(tmp_path):
    bad_dir = tmp_path / "no-such-dir"
    mod = load_daemon_module(DAEMON_PATHS[0], str(bad_dir / "vai.lock"))
    assert mod._vai_lock_acquire() is None


def test_all_daemon_copies_carry_quarantine_check():
    for rel in DAEMON_PATHS:
        with open(os.path.join(REPO_ROOT, rel), encoding="utf-8") as fh:
            text = fh.read()
        assert "_vai_quarantined" in text, rel
        assert ".quarantine" in text, rel


def test_quarantine_marker_blocks_and_clears(tmp_path):
    lock_path = str(tmp_path / "vai.lock")
    mod = load_daemon_module(DAEMON_PATHS[0], lock_path)
    assert mod._vai_quarantined() is None
    marker = mod._vai_quarantine_path()
    assert marker == lock_path + ".quarantine"
    with open(marker, "w", encoding="utf-8") as fh:
        fh.write("timeout during eval")
    reason = mod._vai_quarantined()
    assert reason == "timeout during eval"
    try:
        mod._execute_recoverable("1+1")
        raise AssertionError("eval ran while quarantined")
    except mod.RequestNotStarted as exc:
        assert "quarantine" in str(exc)
    os.remove(marker)
    assert mod._vai_quarantined() is None


def test_uncertain_outcome_writes_quarantine_marker(tmp_path):
    lock_path = str(tmp_path / "vai.lock")
    mod = load_daemon_module(DAEMON_PATHS[0], lock_path)

    def boom(_skill):
        raise mod.ExecutionUncertain("simulated partial transmission")

    mod._transmit_and_read = boom
    try:
        mod._execute_recoverable("1+1")
        raise AssertionError("expected ExecutionUncertain")
    except mod.ExecutionUncertain:
        pass
    with open(mod._vai_quarantine_path(), encoding="utf-8") as fh:
        assert "uncertain" in fh.read()
    assert mod._vai_quarantined() is not None


def test_recoverable_refuses_when_lock_unavailable(tmp_path):
    bad_dir = tmp_path / "no-such-dir"
    mod = load_daemon_module(DAEMON_PATHS[0], str(bad_dir / "vai.lock"))

    def forbidden(_skill):
        raise AssertionError("transmit ran without the cross-bridge lock")

    mod._transmit_and_read = forbidden
    try:
        mod._execute_recoverable("1+1")
        raise AssertionError("eval ran without the cross-bridge lock")
    except mod.RequestNotStarted as exc:
        assert "lock unavailable" in str(exc)


def test_marker_write_failure_keeps_lock_held(tmp_path):
    lock_path = str(tmp_path / "vai.lock")
    mod = load_daemon_module(DAEMON_PATHS[0], lock_path)

    def boom(_skill):
        raise mod.ExecutionUncertain("simulated partial transmission")

    mod._transmit_and_read = boom
    mod._vai_enter_quarantine = lambda _reason: False
    try:
        mod._execute_recoverable("1+1")
        raise AssertionError("expected ExecutionUncertain")
    except mod.ExecutionUncertain:
        pass
    fd = os.open(lock_path, os.O_RDWR)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        raise AssertionError("lock was released after a failed marker write")
    except BlockingIOError:
        pass
    finally:
        os.close(fd)


def test_unreadable_marker_treated_as_active(tmp_path):
    lock_path = str(tmp_path / "vai.lock")
    marker = lock_path + ".quarantine"
    os.mkdir(marker)
    mod = load_daemon_module(DAEMON_PATHS[0], lock_path)
    reason = mod._vai_quarantined()
    assert reason is not None
    assert "UNREADABLE" in reason
    try:
        mod._execute_recoverable("1+1")
        raise AssertionError("eval ran while the marker was unreadable")
    except mod.RequestNotStarted as exc:
        assert "UNREADABLE" in str(exc)


def test_all_daemon_copies_refuse_and_hold_on_error():
    for rel in DAEMON_PATHS:
        with open(os.path.join(REPO_ROOT, rel), encoding="utf-8") as fh:
            text = fh.read()
        assert "_vai_enter_quarantine" in text, rel
        assert "LockError: cross-bridge lock unavailable" in text, rel
        assert "_vai_keep_lock" in text, rel
