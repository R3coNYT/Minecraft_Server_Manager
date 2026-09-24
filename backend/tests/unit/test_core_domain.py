"""Tests du domaine pur : états, permissions, politique de redémarrage."""

from __future__ import annotations

import pytest

from msm.core.permissions import (
    Permission,
    Role,
    ServerRole,
    global_permissions,
    sees_every_server,
    server_permissions,
)
from msm.core.restart_policy import AutoRestartMode, RestartPolicy
from msm.core.states import ALLOWED_TRANSITIONS, ServerState, assert_transition, can_transition
from msm.exceptions import InvalidStateTransition


# --------------------------------------------------------------------------- #
#  Machine à états
# --------------------------------------------------------------------------- #
class TestServerState:
    def test_nominal_lifecycle_is_allowed(self) -> None:
        assert can_transition(ServerState.OFFLINE, ServerState.STARTING)
        assert can_transition(ServerState.STARTING, ServerState.ONLINE)
        assert can_transition(ServerState.ONLINE, ServerState.STOPPING)
        assert can_transition(ServerState.STOPPING, ServerState.OFFLINE)

    def test_crash_path_is_allowed(self) -> None:
        assert can_transition(ServerState.ONLINE, ServerState.CRASHED)
        assert can_transition(ServerState.CRASHED, ServerState.STARTING)

    @pytest.mark.parametrize(
        ("current", "target"),
        [
            (ServerState.OFFLINE, ServerState.ONLINE),
            (ServerState.OFFLINE, ServerState.STOPPING),
            (ServerState.STOPPING, ServerState.ONLINE),
            (ServerState.CRASHED, ServerState.ONLINE),
        ],
    )
    def test_illegal_transitions_are_refused(
        self, current: ServerState, target: ServerState
    ) -> None:
        assert not can_transition(current, target)
        with pytest.raises(InvalidStateTransition):
            assert_transition(current, target, server="test")

    def test_transition_error_explains_what_is_possible(self) -> None:
        with pytest.raises(InvalidStateTransition) as excinfo:
            assert_transition(ServerState.OFFLINE, ServerState.ONLINE, server="survie")
        assert "survie" in excinfo.value.message
        assert excinfo.value.cause and "STARTING" in excinfo.value.cause

    def test_every_state_declares_its_transitions(self) -> None:
        assert set(ALLOWED_TRANSITIONS) == set(ServerState)

    def test_same_state_is_always_allowed(self) -> None:
        for state in ServerState:
            assert can_transition(state, state)

    def test_running_states(self) -> None:
        assert ServerState.ONLINE.is_running
        assert ServerState.STARTING.is_running
        assert not ServerState.OFFLINE.is_running
        assert not ServerState.CRASHED.is_running


# --------------------------------------------------------------------------- #
#  Permissions
# --------------------------------------------------------------------------- #
class TestPermissions:
    """La matrice « qui peut faire quoi » du plan d'ouverture au public."""

    def test_admin_holds_every_global_permission(self) -> None:
        assert global_permissions(Role.ADMIN) == frozenset(Permission)

    def test_moderator_moderates_the_platform(self) -> None:
        moderator = global_permissions(Role.MODERATOR)
        assert {Permission.AUDIT_VIEW, Permission.USER_VIEW, Permission.USER_BAN} <= moderator
        for forbidden in (
            Permission.USER_MANAGE,
            Permission.SETTINGS_MANAGE,
            Permission.SYSTEM_VIEW,
            Permission.SERVER_REGISTER,
        ):
            assert forbidden not in moderator

    def test_user_can_only_create_servers(self) -> None:
        assert global_permissions(Role.USER) == frozenset({Permission.SERVER_CREATE})

    def test_owner_does_everything_but_launch_settings_and_autostart(self) -> None:
        owner = server_permissions(Role.USER, ServerRole.OWNER)
        assert {
            Permission.CONSOLE_WRITE,
            Permission.FILE_UPLOAD,
            Permission.SERVER_DELETE,
            Permission.SERVER_MEMBERS,
        } <= owner
        # Ce qui s'exécute sur la machine reste l'affaire des admins de MSM.
        for forbidden in (
            Permission.SERVER_LAUNCH,
            Permission.SERVER_REGISTER,
            Permission.SERVER_AUTOSTART,
        ):
            assert forbidden not in owner

    def test_server_admin_neither_deletes_nor_shares(self) -> None:
        admin = server_permissions(Role.USER, ServerRole.ADMIN)
        assert {Permission.SERVER_START, Permission.CONSOLE_WRITE, Permission.SERVER_EDIT} <= admin
        assert Permission.SERVER_DELETE not in admin
        assert Permission.SERVER_MEMBERS not in admin

    def test_viewer_member_only_sees_the_overview(self) -> None:
        assert server_permissions(Role.USER, ServerRole.VIEWER) == frozenset(
            {Permission.SERVER_VIEW}
        )

    def test_a_stranger_has_no_right_at_all(self) -> None:
        assert server_permissions(Role.USER, None) == frozenset()

    def test_msm_admin_oversees_without_editing(self) -> None:
        """Voir, arrêter, supprimer, démarrage avec MSM — mais pas modifier."""
        oversight = server_permissions(Role.ADMIN, None)
        assert {
            Permission.SERVER_VIEW,
            Permission.SERVER_STOP,
            Permission.SERVER_KILL,
            Permission.SERVER_DELETE,
            Permission.SERVER_AUTOSTART,
        } <= oversight
        for forbidden in (
            Permission.SERVER_EDIT,
            Permission.SERVER_START,
            Permission.CONSOLE_READ,
            Permission.FILE_READ,
        ):
            assert forbidden not in oversight

    def test_moderator_can_view_and_stop_any_server(self) -> None:
        assert server_permissions(Role.MODERATOR, None) == frozenset(
            {Permission.SERVER_VIEW, Permission.SERVER_STOP, Permission.SERVER_KILL}
        )

    def test_an_msm_admin_who_is_server_admin_gets_both(self) -> None:
        both = server_permissions(Role.ADMIN, ServerRole.ADMIN)
        assert Permission.CONSOLE_WRITE in both
        assert Permission.SERVER_AUTOSTART in both
        assert Permission.SERVER_MEMBERS not in both

    def test_only_staff_sees_every_server(self) -> None:
        assert sees_every_server(Role.ADMIN)
        assert sees_every_server(Role.MODERATOR)
        assert not sees_every_server(Role.USER)


# --------------------------------------------------------------------------- #
#  Politique de redémarrage
# --------------------------------------------------------------------------- #
class TestRestartPolicy:
    def test_never_mode_never_restarts(self) -> None:
        policy = RestartPolicy(mode=AutoRestartMode.NEVER)
        assert not policy.evaluate(stop_requested=False, exit_code=1, consecutive_crashes=1)

    def test_requested_stop_never_restarts(self) -> None:
        policy = RestartPolicy(mode=AutoRestartMode.ALWAYS)
        decision = policy.evaluate(stop_requested=True, exit_code=0, consecutive_crashes=0)
        assert not decision.should_restart
        assert "panel" in decision.reason

    def test_on_crash_ignores_clean_exit(self) -> None:
        policy = RestartPolicy(mode=AutoRestartMode.ON_CRASH)
        assert not policy.evaluate(stop_requested=False, exit_code=0, consecutive_crashes=0)

    def test_on_crash_restarts_after_failure(self) -> None:
        policy = RestartPolicy(mode=AutoRestartMode.ON_CRASH)
        assert policy.evaluate(stop_requested=False, exit_code=1, consecutive_crashes=1)

    def test_always_restarts_even_after_clean_exit(self) -> None:
        policy = RestartPolicy(mode=AutoRestartMode.ALWAYS)
        assert policy.evaluate(stop_requested=False, exit_code=0, consecutive_crashes=0)

    def test_crash_loop_is_stopped_at_the_ceiling(self) -> None:
        policy = RestartPolicy(mode=AutoRestartMode.ALWAYS, max_consecutive_crashes=3)
        decision = policy.evaluate(stop_requested=False, exit_code=1, consecutive_crashes=3)
        assert not decision.should_restart
        assert "loop" in decision.reason

    def test_delay_grows_exponentially_then_plateaus(self) -> None:
        policy = RestartPolicy(delay_s=10, backoff_factor=2.0, max_delay_s=60)
        assert policy.compute_delay(1) == 10
        assert policy.compute_delay(2) == 20
        assert policy.compute_delay(3) == 40
        assert policy.compute_delay(10) == 60

    def test_stability_threshold(self) -> None:
        policy = RestartPolicy(stability_threshold_s=120)
        assert policy.is_stable(121)
        assert not policy.is_stable(119)

    def test_kill_by_signal_counts_as_crash(self) -> None:
        """Un processus tué par signal a un code de sortie absent, pas nul."""
        policy = RestartPolicy(mode=AutoRestartMode.ON_CRASH)
        assert policy.evaluate(stop_requested=False, exit_code=None, consecutive_crashes=1)
