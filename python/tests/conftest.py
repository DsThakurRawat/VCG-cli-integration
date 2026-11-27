from __future__ import annotations

import shutil

import pytest


@pytest.fixture(autouse=True)
def _isolated_connection_state(monkeypatch, tmp_path, request):
    """Offline transport tests must never inspect a developer's saved tunnels."""
    if request.module.__name__.endswith("test_runtime_paths"):
        return
    monkeypatch.setenv("VB_STATE_DIR", str(tmp_path / "bridge-state"))
    def legacy_state(profile=None):
        name = "state_%s.json" % profile if profile else "state.json"
        return tmp_path / "legacy-state" / name
    monkeypatch.setattr("virtuoso_bridge.runtime_paths.legacy_cache_state_file", legacy_state)
    monkeypatch.setattr("virtuoso_bridge.transport.tunnel.legacy_cache_state_file", legacy_state)


@pytest.fixture(autouse=True)
def _private_cross_bridge_lock(monkeypatch, tmp_path_factory):
    """virtuoso-cli-integration: daemons started by tests must never take the
    host-wide cross-bridge lock or write its quarantine marker, or running
    this suite on a lab host would quarantine the live Virtuoso session."""
    root = tmp_path_factory.mktemp("vai_lock")
    monkeypatch.setenv("VAI_LOCK_FILE", str(root / "virtuoso-ai-bridge.lock"))


@pytest.fixture(autouse=True)
def _private_remote_daemon_cache(monkeypatch, tmp_path_factory):
    """Keep the remote-daemon token cache out of the real home directory."""
    root = tmp_path_factory.mktemp("remote_daemons")
    monkeypatch.setattr("virtuoso_bridge.daemon_auth._remote_cache_root", lambda: root)


@pytest.fixture(autouse=True)
def _portable_local_tar_commands(request, monkeypatch):
    """Build the portable local tar argv on every client platform.

    On macOS the bridge probes ``tar --version`` and adds bsdtar AppleDouble
    options. That probe would also reach tests' fake ``Popen`` and change the
    argv they assert. Tests of the probe and live tests request
    ``real_tar_detection``.
    """
    if "real_tar_detection" in request.fixturenames:
        return
    monkeypatch.setattr(
        "virtuoso_bridge.transport.transfer._is_macos_bsdtar",
        lambda _tar_command: False,
    )


@pytest.fixture
def real_tar_detection():
    """Use the bridge's real local tar probe, starting from an empty cache."""
    from virtuoso_bridge.transport import transfer

    transfer._tar_reports_bsdtar.cache_clear()
    yield
    transfer._tar_reports_bsdtar.cache_clear()


@pytest.fixture
def macos_bsdtar(real_tar_detection) -> str:
    """Return the local tar when it is macOS bsdtar; skip on other clients."""
    from virtuoso_bridge.transport import transfer

    tar_command = shutil.which("tar")
    if tar_command is None or not transfer._is_macos_bsdtar(tar_command):
        pytest.skip("requires macOS with bsdtar as the local tar")
    return tar_command


# virtuoso-cli-integration: upstream's tools/ and scripts/ were not vendored.
# Tests that need one of them are skipped (with the missing path as the
# reason) only while that file is absent, so restoring a file re-enables them.
_ROOT = __import__("pathlib").Path(__file__).resolve().parents[1]
_UNVENDORED_MODULES = {
    "test_netlist_and_traffic_scripts.py": "scripts/track_traffic.py",
    "test_signoff_preview.py": "tools/signoff.py",
}
_UNVENDORED_NEEDLES = {
    "skill_exec": "tools/skill_exec.py",
    "fix-symlinks": "scripts/fix-symlinks.sh",
    "FIX_SYMLINKS": "scripts/fix-symlinks.sh",
    "fix_symlinks": "scripts/fix-symlinks.sh",
}
collect_ignore = [name for name, dep in _UNVENDORED_MODULES.items() if not (_ROOT / dep).exists()]


def pytest_collection_modifyitems(config, items):
    import inspect
    missing = {needle: dep for needle, dep in _UNVENDORED_NEEDLES.items() if not (_ROOT / dep).exists()}
    if not missing:
        return
    for item in items:
        fn = getattr(item, "function", None)
        try:
            source = inspect.getsource(fn) if fn else ""
        except (OSError, TypeError):
            source = ""
        for needle, dep in missing.items():
            if needle in source or needle in item.name:
                item.add_marker(pytest.mark.skip(reason="needs %s, which is not vendored in this repo" % dep))
                break
