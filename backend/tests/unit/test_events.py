"""Tests des actions d'événement et du moteur d'exécution."""

from __future__ import annotations

import asyncio
import json

import pytest

from msm.core.danger import DangerLevel
from msm.events import registry
from msm.events.actions import ExecutionContext
from msm.events.engine import MAX_STEPS, EventRunner, RunStatus, max_danger, parse_steps
from msm.exceptions import UnsafeCommandError, ValidationError


class Recorder:
    """Console factice : mémorise les commandes au lieu de les envoyer."""

    def __init__(self) -> None:
        self.commands: list[str] = []
        self.slept: list[float] = []

    async def send(self, command: str) -> str:
        self.commands.append(command)
        return command

    async def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)


@pytest.fixture
def recorder() -> Recorder:
    return Recorder()


@pytest.fixture
def context(recorder: Recorder) -> ExecutionContext:
    return ExecutionContext(
        server_name="survie", actor="flavien", send=recorder.send, sleep=recorder.sleep
    )


async def run_action(key: str, params: dict, context: ExecutionContext):
    action = registry.get(key)
    return await action.execute(context, action.validate(params))


# --------------------------------------------------------------------------- #
#  Catalogue
# --------------------------------------------------------------------------- #
class TestRegistry:
    def test_builtin_actions_are_registered(self) -> None:
        keys = {action.key for action in registry.all_actions()}

        assert {"say", "title", "actionbar", "give", "teleport", "kill", "delay", "command"} <= keys

    def test_unknown_action_lists_the_available_ones(self) -> None:
        with pytest.raises(ValidationError) as excinfo:
            registry.get("inexistante")

        assert excinfo.value.remediation and "say" in excinfo.value.remediation

    def test_catalogue_describes_its_fields(self) -> None:
        """Le frontend construit ses formulaires à partir de cette description."""
        catalogue = {item["key"]: item for item in registry.describe_all()}

        give = catalogue["give"]
        fields = {field["name"]: field for field in give["fields"]}
        assert fields["count"]["type"] == "number"
        assert fields["count"]["maximum"] == 6400
        assert catalogue["kill"]["danger"] == "DESTRUCTIVE"


# --------------------------------------------------------------------------- #
#  Actions
# --------------------------------------------------------------------------- #
class TestActions:
    @pytest.mark.asyncio
    async def test_say(self, context: ExecutionContext, recorder: Recorder) -> None:
        result = await run_action("say", {"message": "Bonjour à tous !"}, context)

        assert recorder.commands == ["say Bonjour à tous !"]
        assert "Bonjour" in result.summary

    @pytest.mark.asyncio
    async def test_title_sets_timings_before_the_title(
        self, context: ExecutionContext, recorder: Recorder
    ) -> None:
        """Appliquées après, les durées ne prendraient effet qu'au titre suivant."""
        await run_action("title", {"title": "ÉVÉNEMENT", "subtitle": "Ça commence !"}, context)

        assert recorder.commands[0].startswith("title @a times")
        assert recorder.commands[1].startswith("title @a subtitle")
        assert recorder.commands[2].startswith("title @a title")

    @pytest.mark.asyncio
    async def test_title_encodes_text_as_json(
        self, context: ExecutionContext, recorder: Recorder
    ) -> None:
        """Un guillemet dans le message casserait une commande concaténée."""
        await run_action("title", {"title": 'Le "grand" tournoi'}, context)

        title_command = recorder.commands[-1]
        payload = title_command.split("title @a title ", 1)[1]
        assert json.loads(payload) == {"text": 'Le "grand" tournoi'}

    @pytest.mark.asyncio
    async def test_actionbar(self, context: ExecutionContext, recorder: Recorder) -> None:
        await run_action("actionbar", {"message": "Plus que 5 minutes"}, context)

        assert recorder.commands[0].startswith("title @a actionbar ")

    @pytest.mark.asyncio
    async def test_give_to_everyone(self, context: ExecutionContext, recorder: Recorder) -> None:
        await run_action("give", {"item": "diamond", "count": 5}, context)

        assert recorder.commands == ["give @a diamond 5"]

    @pytest.mark.asyncio
    async def test_teleport_to_a_player(
        self, context: ExecutionContext, recorder: Recorder
    ) -> None:
        await run_action("teleport", {"target": "@a", "destination": "Flavien"}, context)

        assert recorder.commands == ["tp @a Flavien"]

    @pytest.mark.asyncio
    async def test_teleport_to_coordinates(
        self, context: ExecutionContext, recorder: Recorder
    ) -> None:
        await run_action("teleport", {"target": "@a", "destination": "100 64 -200"}, context)

        assert recorder.commands == ["tp @a 100 64 -200"]

    @pytest.mark.asyncio
    async def test_delay_waits_without_sending_anything(
        self, context: ExecutionContext, recorder: Recorder
    ) -> None:
        await run_action("delay", {"seconds": 30}, context)

        assert recorder.slept == [30.0]
        assert recorder.commands == []

    @pytest.mark.asyncio
    async def test_custom_command(self, context: ExecutionContext, recorder: Recorder) -> None:
        await run_action("command", {"command": "weather clear"}, context)

        assert recorder.commands == ["weather clear"]


class TestActionValidation:
    @pytest.mark.parametrize(
        ("key", "params"),
        [
            ("say", {"message": ""}),
            ("say", {"message": "   "}),
            ("title", {"title": ""}),
            ("give", {"item": "diamond ; stop", "count": 1}),
            ("give", {"item": "diamond", "count": 0}),
            ("give", {"item": "diamond", "count": 99999}),
            ("kill", {"target": "nom invalide"}),
            ("teleport", {"target": "@a", "destination": ""}),
            ("delay", {"seconds": -5}),
            ("delay", {"seconds": "beaucoup"}),
        ],
    )
    def test_invalid_parameters_are_refused(self, key: str, params: dict) -> None:
        with pytest.raises(ValidationError):
            registry.get(key).validate(params)

    def test_injection_through_a_message_is_refused(self) -> None:
        """Un saut de ligne permettrait d'exécuter une seconde commande."""
        with pytest.raises(UnsafeCommandError):
            registry.get("command").validate({"command": "say bonjour\nop attaquant"})

    def test_errors_explain_the_correction(self) -> None:
        with pytest.raises(ValidationError) as excinfo:
            registry.get("give").validate({"item": "diamond", "count": 99999})

        assert excinfo.value.cause
        assert excinfo.value.remediation


class TestDangerClassification:
    def test_ordinary_actions_are_safe(self) -> None:
        assert registry.danger_of("say", {"message": "bonjour"}) is DangerLevel.SAFE
        assert registry.danger_of("give", {"item": "diamond", "count": 1}) is DangerLevel.SAFE

    def test_kill_is_destructive(self) -> None:
        assert registry.danger_of("kill", {"target": "@a"}) is DangerLevel.DESTRUCTIVE

    def test_custom_command_is_judged_on_its_content(self) -> None:
        """Classer toute commande libre comme dangereuse viderait la permission de son sens."""
        assert registry.danger_of("command", {"command": "weather clear"}) is DangerLevel.SAFE
        assert registry.danger_of("command", {"command": "stop"}) is DangerLevel.DESTRUCTIVE
        assert registry.danger_of("command", {"command": "op Flavien"}) is DangerLevel.SENSITIVE


# --------------------------------------------------------------------------- #
#  Séquences
# --------------------------------------------------------------------------- #
class TestParseSteps:
    def test_valid_sequence(self) -> None:
        steps = parse_steps(
            [
                {"action": "say", "params": {"message": "Le tournoi commence"}},
                {"action": "delay", "params": {"seconds": 10}},
                {"action": "give", "params": {"item": "diamond_sword", "count": 1}},
            ]
        )

        assert [step.action for step in steps] == ["say", "delay", "give"]

    def test_empty_sequence_is_refused(self) -> None:
        with pytest.raises(ValidationError, match="vide"):
            parse_steps([])

    def test_oversized_sequence_is_refused(self) -> None:
        steps = [{"action": "say", "params": {"message": "x"}}] * (MAX_STEPS + 1)

        with pytest.raises(ValidationError, match="trop long"):
            parse_steps(steps)

    def test_error_points_at_the_faulty_step(self) -> None:
        """Dans une séquence de dix, savoir laquelle corriger est essentiel."""
        with pytest.raises(ValidationError) as excinfo:
            parse_steps(
                [
                    {"action": "say", "params": {"message": "ok"}},
                    {"action": "give", "params": {"item": "diamond", "count": 0}},
                ]
            )

        assert "Étape 2" in excinfo.value.message

    def test_max_danger_of_a_sequence(self) -> None:
        steps = parse_steps(
            [
                {"action": "say", "params": {"message": "Attention"}},
                {"action": "kill", "params": {"target": "@a"}},
            ]
        )

        assert max_danger(steps) is DangerLevel.DESTRUCTIVE


@pytest.mark.asyncio
class TestEventRunner:
    async def test_runs_every_step_in_order(
        self, context: ExecutionContext, recorder: Recorder
    ) -> None:
        steps = parse_steps(
            [
                {"action": "say", "params": {"message": "Le tournoi commence"}},
                {"action": "delay", "params": {"seconds": 10}},
                {"action": "give", "params": {"item": "diamond_sword", "count": 1}},
            ]
        )
        progress: list = []

        result = await EventRunner(steps, context, on_progress=progress.append).run()

        assert result.status is RunStatus.COMPLETED
        assert result.current_step == 3
        assert recorder.commands == ["say Le tournoi commence", "give @a diamond_sword 1"]
        assert recorder.slept == [10.0]

    async def test_progress_is_reported_at_each_step(self, context: ExecutionContext) -> None:
        steps = parse_steps(
            [
                {"action": "say", "params": {"message": "un"}},
                {"action": "say", "params": {"message": "deux"}},
            ]
        )
        progress: list = []

        await EventRunner(steps, context, on_progress=progress.append).run()

        # Démarrage, puis une notification par étape, puis la fin.
        assert [item.current_step for item in progress] == [0, 1, 2, 2]
        assert progress[-1].status is RunStatus.COMPLETED

    async def test_failure_stops_the_sequence(self, recorder: Recorder) -> None:
        """Poursuivre après un échec produirait un demi-événement."""

        async def failing_send(command: str) -> str:
            if "deux" in command:
                raise UnsafeCommandError("Commande refusée.", cause="test")
            recorder.commands.append(command)
            return command

        context = ExecutionContext(
            server_name="survie", actor="flavien", send=failing_send, sleep=recorder.sleep
        )
        steps = parse_steps(
            [
                {"action": "say", "params": {"message": "un"}},
                {"action": "say", "params": {"message": "deux"}},
                {"action": "say", "params": {"message": "trois"}},
            ]
        )

        result = await EventRunner(steps, context).run()

        assert result.status is RunStatus.FAILED
        assert result.current_step == 2
        assert result.error
        # La troisième étape n'a pas été tentée.
        assert recorder.commands == ["say un"]

    async def test_cancellation_while_reporting_progress(self, recorder: Recorder) -> None:
        """Rendre compte écrit en base : l'annulation peut tomber pile là.

        Sans rattrapage autour de la boucle entière, l'exécution restait
        éternellement « en cours » dans l'historique.
        """
        progress: list = []

        async def slow_report(item) -> None:
            progress.append(item)
            # Assez long pour que l'annulation tombe pendant cette écriture.
            await asyncio.sleep(0.2)

        context = ExecutionContext(
            server_name="survie", actor="flavien", send=recorder.send, sleep=recorder.sleep
        )
        steps = parse_steps(
            [
                {"action": "say", "params": {"message": "un"}},
                {"action": "say", "params": {"message": "deux"}},
            ]
        )
        task = asyncio.create_task(EventRunner(steps, context, on_progress=slow_report).run())

        await asyncio.sleep(0.1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        assert progress[-1].status is RunStatus.CANCELLED

    async def test_cancellation_during_a_delay(self, recorder: Recorder) -> None:
        """Un événement de trente minutes doit pouvoir être interrompu."""
        context = ExecutionContext(
            server_name="survie", actor="flavien", send=recorder.send, sleep=asyncio.sleep
        )
        steps = parse_steps(
            [
                {"action": "say", "params": {"message": "début"}},
                {"action": "delay", "params": {"seconds": 3600}},
                {"action": "say", "params": {"message": "jamais atteint"}},
            ]
        )
        progress: list = []
        task = asyncio.create_task(EventRunner(steps, context, on_progress=progress.append).run())

        await asyncio.sleep(0.1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        assert recorder.commands == ["say début"]
        assert progress[-1].status is RunStatus.CANCELLED
