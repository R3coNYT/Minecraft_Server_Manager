"""Tests du domaine pur : états, permissions, politique de redémarrage."""

from __future__ import annotations

import pytest

from msm.core.permissions import Permission, Role, effective_permissions, has_permission
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
    def test_admin_has_every_permission(self) -> None:
        assert effective_permissions(Role.ADMIN) == frozenset(Permission)

    def test_viewer_is_read_only(self) -> None:
        viewer = effective_permissions(Role.VIEWER)
        assert Permission.CONSOLE_READ in viewer
        for forbidden in (
            Permission.CONSOLE_WRITE,
            Permission.SERVER_START,
            Permission.FILE_DELETE,
            Permission.PLAYER_BAN,
        ):
            assert forbidden not in viewer

    def test_moderator_can_moderate_but_not_administrate(self) -> None:
        moderator = effective_permissions(Role.MODERATOR)
        assert Permission.CONSOLE_WRITE in moderator
        assert Permission.PLAYER_KICK in moderator
        for forbidden in (
            Permission.CONSOLE_DANGEROUS,
            Permission.PLAYER_OP,
            Permission.USER_MANAGE,
            Permission.CONFIG_WRITE,
            Permission.SERVER_DELETE,
        ):
            assert forbidden not in moderator

    def test_per_server_grant_extends_the_role(self) -> None:
        assert has_permission(
            Role.MODERATOR,
            Permission.CONFIG_WRITE,
            granted=frozenset({Permission.CONFIG_WRITE}),
        )

    def test_revocation_wins_over_grant(self) -> None:
        """En cas de conflit, le refus l'emporte — règle de sécurité."""
        assert not has_permission(
            Role.ADMIN,
            Permission.SERVER_DELETE,
            granted=frozenset({Permission.SERVER_DELETE}),
            revoked=frozenset({Permission.SERVER_DELETE}),
        )


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
        assert "boucle" in decision.reason

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
