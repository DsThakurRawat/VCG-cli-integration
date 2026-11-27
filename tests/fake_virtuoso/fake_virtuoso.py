import argparse
import json
import os
import select
import signal
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from skill_eval import Session, SkillError, errset_record, format_value

STX, NAK, RS = "\x02", "\x15", "\x1e"
REPO = Path(__file__).resolve().parents[2]
BRIDGE_DAEMON = REPO / "python/src/virtuoso_bridge/virtuoso/basic/resources/ramic_bridge_daemon_3.py"


class Ciw:
    def __init__(self, state_dir, serialize):
        self.session = Session(env=os.environ)
        self.serialize = serialize
        self.lock = threading.Lock()
        self.log = open(state_dir / "requests.jsonl", "a", buffering=1)
        self.log_lock = threading.Lock()
        self.busy = 0

    def evaluate(self, bridge, text):
        started = time.time()
        if self.serialize:
            self.lock.acquire()
        self.busy += 1
        try:
            ok, value = True, format_value(self.session.eval_text("(progn " + text + "\n)"))
        except SkillError as exc:
            ok, value = False, format_value(errset_record(exc))
        except Exception as exc:
            ok, value = False, format_value(errset_record("*Error* fake-virtuoso: %s: %s" % (type(exc).__name__, exc)))
        finally:
            self.busy -= 1
            if self.serialize:
                self.lock.release()
        with self.log_lock:
            loaded = self.session.take_loaded()
            if loaded:
                text = text + "\n" + "\n".join(loaded)
            self.log.write(json.dumps({"bridge": bridge, "skill": text, "ok": ok, "value": value,
                                       "start": started, "end": time.time()}) + "\n")
        return ok, value


def drain(stream, path):
    with open(path, "ab", buffering=0) as out:
        for line in iter(stream.readline, b""):
            out.write(line)


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def start_vcli(ciw, daemon_bin, state_dir):
    env = dict(os.environ, VC_SSH_PORT="22", VCLI_CAPABILITY="admin")
    proc = subprocess.Popen([daemon_bin, "127.0.0.1", "0"], stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
    port = None
    deadline = time.time() + 15
    with open(state_dir / "vcli-daemon.log", "ab", buffering=0) as log:
        while port is None:
            if time.time() > deadline or proc.poll() is not None:
                raise SystemExit("vcli daemon did not report PORT")
            line = proc.stderr.readline()
            log.write(line)
            text = line.decode(errors="replace").strip()
            if text.startswith("PORT:"):
                port = int(text[5:])
    threading.Thread(target=drain, args=(proc.stderr, state_dir / "vcli-daemon.log"), daemon=True).start()
    cb = port + 1
    data_file, done_file, started_file = (Path("/tmp/.ramic_cb_%d%s" % (cb, sfx)) for sfx in ("", ".done", ".started"))

    def loop():
        fd = proc.stdout.fileno()
        while True:
            chunks = []
            ready, _, _ = select.select([fd], [], [])
            while ready:
                chunk = os.read(fd, 65536)
                if not chunk:
                    return
                chunks.append(chunk)
                ready, _, _ = select.select([fd], [], [], 0.1)
            text = b"".join(chunks).decode(errors="replace")
            started_file.write_text(time.strftime("%b %d %H:%M:%S %Y"))
            threading.Thread(target=answer, args=(text,), daemon=True).start()

    def answer(text):
        ok, value = ciw.evaluate("vcli", text)
        data_file.write_text((STX if ok else NAK) + value + RS)
        done_file.write_text("")

    threading.Thread(target=loop, daemon=True).start()
    host = socket.gethostname().split(".")[0]
    user = os.environ.get("USER", "user")
    session_id = "%s-%s-%d" % (host, user, port)
    sessions = Path.home() / ".cache/virtuoso_bridge/sessions"
    sessions.mkdir(parents=True, exist_ok=True)
    (sessions / (session_id + ".json")).write_text(json.dumps({
        "id": session_id, "port": port, "pid": 0, "host": host, "user": user,
        "created": time.strftime("%b %d %H:%M:%S %Y")}))
    return proc, port, session_id


def start_bridge(ciw, python, state_dir):
    port = free_port()
    boot_id = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
    env = dict(os.environ, RB_REPLY_TAGS="1", RB_VIRTUOSO_PID=str(os.getpid()),
               RB_VIRTUOSO_BOOT_ID=boot_id, RB_VIRTUOSO_WORK_DIR=os.getcwd())
    proc = subprocess.Popen([python, "-u", str(BRIDGE_DAEMON), "127.0.0.1", str(port)],
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
    threading.Thread(target=drain, args=(proc.stderr, state_dir / "bridge-daemon.log"), daemon=True).start()
    write_lock = threading.Lock()

    def answer(tag, text):
        ok, value = ciw.evaluate("bridge", text)
        with write_lock:
            proc.stdin.write(((STX if ok else NAK) + tag + value + RS).encode())
            proc.stdin.flush()

    def loop():
        for raw in iter(proc.stdout.readline, b""):
            line = raw.decode(errors="replace").rstrip("\r\n")
            if not line:
                continue
            tag = ""
            if line.startswith("vbtag:") and len(line) > 23:
                tag, line = line[6:22], line[23:]
            threading.Thread(target=answer, args=(tag, line), daemon=True).start()

    threading.Thread(target=loop, daemon=True).start()
    deadline = time.time() + 15
    while True:
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
            break
        except OSError:
            if time.time() > deadline or proc.poll() is not None:
                raise SystemExit("bridge daemon did not listen on %d" % port)
            time.sleep(0.05)
    return proc, port


def main():
    ap = argparse.ArgumentParser(description="Stand-in for a Virtuoso CIW that hosts both bridge daemons.")
    ap.add_argument("--state-dir", type=Path, required=True)
    ap.add_argument("--vcli-daemon", required=True)
    ap.add_argument("--bridge-python", default=sys.executable)
    ap.add_argument("--serialize", action="store_true",
                    help="evaluate one request at a time like a real CIW (default: concurrent)")
    a = ap.parse_args()
    a.state_dir.mkdir(parents=True, exist_ok=True)
    ciw = Ciw(a.state_dir, a.serialize)
    vcli, vcli_port, session_id = start_vcli(ciw, a.vcli_daemon, a.state_dir)
    bridge, bridge_port = start_bridge(ciw, a.bridge_python, a.state_dir)
    info = {"pid": os.getpid(), "vcli_port": vcli_port, "vcli_pid": vcli.pid,
            "vcli_session": session_id, "bridge_port": bridge_port, "bridge_pid": bridge.pid}
    (a.state_dir / "ready.json").write_text(json.dumps(info))
    print(json.dumps(info), flush=True)

    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    while not stop.is_set():
        stop.wait(0.5)
    for proc in (vcli, bridge):
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()


if __name__ == "__main__":
    main()
