"""
Tests for harness property: TARGET STATE IS OBSERVABLE AND RECOVERABLE

Why: Two failure modes from the empirical run live here:

  - Failure mode 2 (target-state corruption): the target environment
    can be put into a state the script-based revert cannot undo. The
    snapshot-based revert is the harness mechanism that makes recovery
    authoritative regardless of what was broken.

  - Failure mode 3 (diagnostic blindness): the harness must be able to
    capture WHY an attempt failed, in structured form, before any
    revert action.

These tests exercise both properties against the real VM. They do not
involve any LLM. They are slower than Tier 1/2 because each break/restore
cycle takes a few seconds, but they are still fast compared to Tier 5.

This file is Tier 3 of the test plan in tests/PLAN.md.

Tests in this file BREAK the VM in controlled ways. Each test must clean
up after itself by restoring the baseline snapshot, or the next test
starts in a corrupted state. We use a fixture to enforce baseline
restoration before each test.
"""

from __future__ import annotations

import asyncio
import shlex

import asyncssh
import pytest

from gemma_forge.harness.tools.ssh import (
    SSHConfig,
    _run_snapshot_cmd,
    check_sudo_healthy,
    gather_environment_diagnostics,
    snapshot_exists,
    snapshot_restore_progress,
    snapshot_save_progress,
)


# Fixture: ensure baseline VM state before each test
@pytest.fixture(autouse=True)
async def restore_baseline_before_each_test():
    """Each test starts at baseline. Cleanup runs after each test too."""
    ok, _ = await _run_snapshot_cmd("restore", "baseline", timeout=60)
    assert ok, "Failed to restore baseline before test"
    # Wait for VM to settle after snapshot restore
    await asyncio.sleep(2)
    yield
    # Cleanup: clear any progress snapshot left behind
    await _run_snapshot_cmd("delete", "progress", timeout=30)


@pytest.fixture
def vm_config() -> SSHConfig:
    return SSHConfig(
        host="192.168.122.43",
        user="adm-forge",
        key_path="/data/vm/gemma-forge/keys/adm-forge",
    )


async def _ssh_run_as_root(config: SSHConfig, script: str) -> tuple[str, int]:
    """Helper: run a script via SSH as root (using sudo). Used by tests to
    deliberately break the VM."""
    async with asyncssh.connect(
        config.host,
        username=config.user,
        client_keys=[config.key_path],
        known_hosts=None,
        connect_timeout=10,
    ) as conn:
        result = await conn.run(
            f"sudo bash -c {shlex.quote(script)}",
            check=False,
            timeout=30,
        )
        return (result.stdout or "") + (result.stderr or ""), result.returncode or 0


# =============================================================================
# Property: gather_environment_diagnostics correctly identifies HEALTHY state
# =============================================================================

class TestDiagnosticsOnHealthyTarget:
    async def test_property_baseline_reports_all_systems_healthy(self, vm_config):
        diag = await gather_environment_diagnostics(vm_config)
        assert diag["sudo_ok"] is True, f"Expected sudo_ok on baseline, got {diag.get('sudo_probe')}"
        assert diag["services_ok"] is True, f"Expected services_ok on baseline, got {diag.get('service_status')}"
        assert diag["mission_healthy"] is True, f"Expected mission_healthy on baseline, got {diag.get('mission_healthcheck')}"

    async def test_property_diagnostics_returns_all_expected_sections(self, vm_config):
        diag = await gather_environment_diagnostics(vm_config)
        for section in ["sudo_probe", "service_status", "mission_healthcheck",
                        "recent_auth_failures", "sudoers_state", "pam_state",
                        "fs_state", "network_state", "recent_journal_errors"]:
            assert section in diag, f"Missing section: {section}"

    async def test_property_diagnostic_call_does_not_modify_target(self, vm_config):
        # Run diagnostics twice and verify the target is unchanged between
        diag1 = await gather_environment_diagnostics(vm_config)
        await asyncio.sleep(1)
        diag2 = await gather_environment_diagnostics(vm_config)
        assert diag1["sudo_ok"] == diag2["sudo_ok"]
        assert diag1["services_ok"] == diag2["services_ok"]
        assert diag1["mission_healthy"] == diag2["mission_healthy"]


# =============================================================================
# Property: gather_environment_diagnostics correctly identifies BROKEN states
# This is the critical property — the loop's recovery depends on accurate
# detection of what's broken.
# =============================================================================

class TestDiagnosticsOnBrokenTarget:
    async def test_property_stopped_nginx_reported_as_services_unhealthy(self, vm_config):
        await _ssh_run_as_root(vm_config, "systemctl stop nginx")
        diag = await gather_environment_diagnostics(vm_config)
        assert diag["services_ok"] is False, "Expected services_ok=False after stopping nginx"
        assert "nginx" in diag.get("service_status", "")
        assert "inactive" in diag.get("service_status", "") or "failed" in diag.get("service_status", "")

    async def test_property_stopped_postgres_reported_as_services_unhealthy(self, vm_config):
        await _ssh_run_as_root(vm_config, "systemctl stop postgresql")
        diag = await gather_environment_diagnostics(vm_config)
        assert diag["services_ok"] is False
        assert "postgresql" in diag.get("service_status", "")
        # Either inactive or failed counts as broken
        assert "inactive" in diag.get("service_status", "") or "failed" in diag.get("service_status", "")

    async def test_property_broken_sudo_reported_as_sudo_unhealthy(self, vm_config):
        # Add a passwd-required line to a sudoers.d file to break passwordless sudo
        # for adm-forge specifically. This mirrors what the overnight run did.
        # Remove NOPASSWD from the cloud-init sudoers file. This actually
        # breaks passwordless sudo (the Defaults:targetpw approach doesn't
        # override per-user NOPASSWD).
        await _ssh_run_as_root(vm_config, """
            sed -i 's/NOPASSWD://g' /etc/sudoers.d/90-cloud-init-users
            visudo -c >/dev/null 2>&1 || true
        """)
        diag = await gather_environment_diagnostics(vm_config)
        assert diag["sudo_ok"] is False, (
            f"Expected sudo_ok=False after breaking sudo. "
            f"sudo_probe={diag.get('sudo_probe')!r}"
        )

    async def test_property_failed_mission_healthcheck_reported(self, vm_config):
        # Stop both critical services so the healthcheck script reports unhealthy
        await _ssh_run_as_root(vm_config, "systemctl stop nginx; systemctl stop postgresql")
        diag = await gather_environment_diagnostics(vm_config)
        assert diag["mission_healthy"] is False, (
            f"Expected mission_healthy=False, got mission_healthcheck={diag.get('mission_healthcheck', '')!r}"
        )

    async def test_property_diagnostics_distinguishes_break_types(self, vm_config):
        """Different breaks produce different diagnostic signatures.

        Note on the asymmetry below: when sudo is broken, the diagnostic
        gather currently CANNOT reach the rest of the target's state via the
        in-band path (SSH-via-sudo). The console fallback is implemented but
        has a known bug ("Connection lost" — the virsh console subprocess
        protocol needs work). In the meantime, broken-sudo gathering returns
        sudo_ok=False with high confidence and the other flags default to
        False because we couldn't probe.

        This is documented in the failure-modes doc as a future improvement
        (failure mode 7: out-of-band channel reliability). The harness still
        recovers correctly because the snapshot restore is at the libvirt
        level, and the loop's post_restore probe uses direct SSH (not the
        diagnostic gather). The Reflector receives "sudo_ok=False" as its
        primary signal, which is sufficient.
        """
        # Break A: stop nginx (sudo still works)
        await _ssh_run_as_root(vm_config, "systemctl stop nginx")
        diag_a = await gather_environment_diagnostics(vm_config)

        # Restore baseline (clean break B from break A)
        ok, _ = await _run_snapshot_cmd("restore", "baseline", timeout=60)
        assert ok
        await asyncio.sleep(2)

        # Break B: remove NOPASSWD from sudoers.d
        await _ssh_run_as_root(vm_config, """
            sed -i 's/NOPASSWD://g' /etc/sudoers.d/90-cloud-init-users
            visudo -c >/dev/null 2>&1 || true
        """)
        diag_b = await gather_environment_diagnostics(vm_config)

        # PROPERTY: Each break is distinguishable from a healthy state by at
        # least one flag.
        assert diag_a["services_ok"] is False, "nginx-down should flip services_ok"
        assert diag_a["sudo_ok"] is True, "nginx-down does not break sudo"
        assert diag_b["sudo_ok"] is False, "removing NOPASSWD should flip sudo_ok"

        # PROPERTY: The two break types produce different diagnostic signatures
        # (i.e., the harness can tell them apart).
        signature_a = (diag_a["sudo_ok"], diag_a["services_ok"], diag_a["mission_healthy"])
        signature_b = (diag_b["sudo_ok"], diag_b["services_ok"], diag_b["mission_healthy"])
        assert signature_a != signature_b, (
            f"Two distinct breaks produced identical diagnostic signatures: {signature_a}"
        )


# =============================================================================
# Property: snapshot save/restore lifecycle is authoritative
# =============================================================================

class TestSnapshotLifecycle:
    async def test_property_progress_can_be_saved_and_listed(self, vm_config):
        ok, detail = await snapshot_save_progress()
        assert ok, f"Failed to save progress snapshot: {detail}"
        assert await snapshot_exists("progress")

    async def test_property_progress_can_be_restored(self, vm_config):
        await snapshot_save_progress()
        ok, detail = await snapshot_restore_progress()
        assert ok, f"Failed to restore progress: {detail}"
        assert "progress" in detail

    async def test_property_restore_falls_back_to_baseline_when_no_progress(self, vm_config):
        # Make sure no progress exists
        await _run_snapshot_cmd("delete", "progress", timeout=30)
        assert not await snapshot_exists("progress")
        ok, detail = await snapshot_restore_progress()
        assert ok
        assert "baseline" in detail

    async def test_property_save_progress_replaces_previous_progress(self, vm_config):
        # Save progress at baseline
        await snapshot_save_progress()
        # Make a change
        await _ssh_run_as_root(vm_config, "touch /etc/forge_test_marker_a")
        # Save progress AGAIN — should replace, capturing the marker
        await snapshot_save_progress()
        # Make another change
        await _ssh_run_as_root(vm_config, "touch /etc/forge_test_marker_b")
        # Restore — should bring marker_a back, lose marker_b
        ok, _ = await snapshot_restore_progress()
        assert ok
        await asyncio.sleep(2)
        check_a, _ = await _ssh_run_as_root(vm_config, "test -e /etc/forge_test_marker_a && echo present || echo absent")
        check_b, _ = await _ssh_run_as_root(vm_config, "test -e /etc/forge_test_marker_b && echo present || echo absent")
        assert "present" in check_a, f"Marker A was lost: {check_a}"
        assert "absent" in check_b, f"Marker B persisted incorrectly: {check_b}"


# =============================================================================
# Property: snapshot restore recovers from arbitrary breakage
# This is the central authority claim — the harness can recover from
# anything the executor might do to the target.
# =============================================================================

class TestSnapshotRecoversFromBreakage:
    async def test_property_recovers_from_stopped_services(self, vm_config):
        # Stop the world
        await _ssh_run_as_root(vm_config, "systemctl stop nginx; systemctl stop postgresql")
        # Verify it's broken
        diag_pre = await gather_environment_diagnostics(vm_config)
        assert diag_pre["services_ok"] is False

        # Restore
        ok, _ = await snapshot_restore_progress()
        assert ok
        await asyncio.sleep(3)  # let services come back up

        # Verify it's healed
        diag_post = await gather_environment_diagnostics(vm_config)
        assert diag_post["services_ok"] is True, (
            f"Services still unhealthy after restore: {diag_post.get('service_status')}"
        )

    async def test_property_recovers_from_broken_sudo(self, vm_config):
        # Remove NOPASSWD from the cloud-init sudoers file. This actually
        # breaks passwordless sudo (the Defaults:targetpw approach doesn't
        # override per-user NOPASSWD).
        await _ssh_run_as_root(vm_config, """
            sed -i 's/NOPASSWD://g' /etc/sudoers.d/90-cloud-init-users
            visudo -c >/dev/null 2>&1 || true
        """)
        # Verify broken
        ok_sudo, _ = await check_sudo_healthy(vm_config)
        assert ok_sudo is False

        # Restore
        ok, _ = await snapshot_restore_progress()
        assert ok
        await asyncio.sleep(2)

        # Verify healed
        ok_sudo_post, _ = await check_sudo_healthy(vm_config)
        assert ok_sudo_post is True, "Sudo still broken after snapshot restore"

    async def test_property_recovers_from_corrupted_etc_file(self, vm_config):
        # Corrupt /etc/hosts (a file the loop won't normally touch but is part of the snapshot)
        await _ssh_run_as_root(vm_config, "echo 'CORRUPTED' > /etc/hosts")
        # Verify corruption
        out, _ = await _ssh_run_as_root(vm_config, "cat /etc/hosts")
        assert "CORRUPTED" in out

        # Restore
        ok, _ = await snapshot_restore_progress()
        assert ok
        await asyncio.sleep(2)

        # Verify the original hosts file is back
        out, _ = await _ssh_run_as_root(vm_config, "cat /etc/hosts")
        assert "CORRUPTED" not in out

    async def test_property_diagnostic_returns_to_healthy_after_restore(self, vm_config):
        """Full cycle: heal → break → diag(broken) → restore → diag(healthy)."""
        # Start healthy
        diag_initial = await gather_environment_diagnostics(vm_config)
        assert diag_initial["services_ok"]
        assert diag_initial["sudo_ok"]
        assert diag_initial["mission_healthy"]

        # Break in two ways simultaneously: stop nginx + remove NOPASSWD
        await _ssh_run_as_root(vm_config, """
            systemctl stop nginx
            sed -i 's/NOPASSWD://g' /etc/sudoers.d/90-cloud-init-users
        """)
        diag_broken = await gather_environment_diagnostics(vm_config)
        assert not diag_broken["services_ok"]
        assert not diag_broken["sudo_ok"]

        # Restore
        ok, _ = await snapshot_restore_progress()
        assert ok
        await asyncio.sleep(3)

        # Verify fully healed
        diag_healed = await gather_environment_diagnostics(vm_config)
        assert diag_healed["services_ok"], f"Services not healed: {diag_healed.get('service_status')}"
        assert diag_healed["sudo_ok"], f"Sudo not healed: {diag_healed.get('sudo_probe')}"
        assert diag_healed["mission_healthy"], f"Mission not healed"
