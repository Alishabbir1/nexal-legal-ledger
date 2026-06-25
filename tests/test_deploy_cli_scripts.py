"""Verify VPS CLI scripts bootstrap imports from the repository root."""
import os
import subprocess
import sys


def _repo_root() -> str:
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_link_portal_firm_help_from_repo_root():
    root = _repo_root()
    result = subprocess.run(
        [sys.executable, "scripts/link_portal_firm.py", "--help"],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "portal firms.id" in result.stdout.lower()


def test_provision_firm_help_from_repo_root():
    root = _repo_root()
    result = subprocess.run(
        [sys.executable, "scripts/provision_firm.py", "--help"],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_repair_portal_tenant_provisions_orphan():
    import json
    import tempfile
    import uuid

    root = _repo_root()
    with tempfile.TemporaryDirectory() as tmp:
        portal_firm_id = "cli-repair-" + uuid.uuid4().hex[:8]
        env = {**os.environ, "NEXAL_DATA_DIR": os.path.join(tmp, "nexal-cli-repair")}
        result = subprocess.run(
            [
                sys.executable,
                "scripts/repair_portal_tenant.py",
                "--portal-firm-id",
                portal_firm_id,
                "--name",
                "CLI Repair Firm",
                "--owner-email",
                "cli-repair@example.com",
                "--portal-user-id",
                str(uuid.uuid4()),
            ],
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
            env=env,
        )
        assert result.returncode == 0, result.stderr + result.stdout
        payload = json.loads(result.stdout)
        assert payload["repaired"] is True
        assert payload["after"]["database_valid"] is True
