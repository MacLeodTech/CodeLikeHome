"""Option handling of host/install.sh, exercised through --dry-run (needs no root and changes nothing)."""

import getpass
import pathlib
import subprocess

SCRIPT = pathlib.Path(__file__).resolve().parent.parent / "host" / "install.sh"


def dry(*args):
    return subprocess.run(
        ["sh", str(SCRIPT), "--user", getpass.getuser(), "--dry-run", *args],
        capture_output=True,
        check=False,
        text=True,
    )


def test_default_profile_is_accepted():
    r = dry()
    assert r.returncode == 0
    assert "/etc/codelikehome/profile" in r.stdout
    assert "WARNING" not in r.stderr


def test_balanced_and_workstation_accepted():
    assert dry("--profile", "balanced").returncode == 0
    assert dry("--profile", "workstation").returncode == 0


def test_docker_group_warns():
    r = dry("--profile", "balanced", "--groups", "docker") if _has_group("docker") else None
    if r is not None:
        assert r.returncode == 0
        assert "docker group" in r.stderr


def test_rejects_bad_values():
    assert dry("--profile", "bogus").returncode == 2
    assert dry("--umask", "9").returncode == 2
    assert dry("--sudo-timeout", "x").returncode == 2
    assert dry("--groups", "no-such-group-xyz").returncode == 1


def _has_group(name):
    return subprocess.run(["getent", "group", name], check=False, capture_output=True).returncode == 0


def test_gate_dry_run_lists_gate_files_and_not_the_single_unit():
    r = dry("--gate", "--profile", "workstation")
    assert r.returncode == 0
    for path in ("code-server@.service", "codelikehome-gate.service", "/etc/pam.d/codelikehome", "gate.users", "run-editor", "gate.py"):
        assert path in r.stdout
    assert "/etc/systemd/system/code-server.service" not in r.stdout
    assert "root service that checks passwords" in r.stderr


def test_gate_users_are_validated():
    assert dry("--gate", "--users", "bad name").returncode == 2
    assert dry("--gate", "--users", "no-such-user-xyz").returncode == 1
    assert dry("--gate", "--users", "root").returncode == 2
