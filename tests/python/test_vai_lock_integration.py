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


def test_fail_open_on_unusable_path(tmp_path):
    bad_dir = tmp_path / "no-such-dir"
    mod = load_daemon_module(DAEMON_PATHS[0], str(bad_dir / "vai.lock"))
    assert mod._vai_lock_acquire() is None
