"""Tests des briques de la création de serveurs : fichiers, installeur, tâche."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from msm.exceptions import ValidationError
from msm.provisioning import files
from msm.provisioning.installer import InstallerFailed, run_installer
from msm.provisioning.jobs import JobRegistry, JobStatus, ProvisioningJob, Step, StepKey, StepStatus


class TestFiles:
    def test_a_missing_or_empty_folder_can_host_a_server(self, tmp_path: Path) -> None:
        assert files.is_empty_or_missing(tmp_path / "absent")
        assert files.is_empty_or_missing(tmp_path)
        (tmp_path / "world").mkdir()
        assert not files.is_empty_or_missing(tmp_path)

    def test_the_port_is_added_then_replaced(self, tmp_path: Path) -> None:
        files.write_port(tmp_path, 25566)
        assert (tmp_path / "server.properties").read_text() == "server-port=25566\n"

        (tmp_path / "server.properties").write_text("motd=Salut\nserver-port=25565\npvp=true\n")
        files.write_port(tmp_path, 25570)

        assert (tmp_path / "server.properties").read_text().splitlines() == [
            "motd=Salut",
            "server-port=25570",
            "pvp=true",
        ]

    def test_the_eula_is_accepted_explicitly(self, tmp_path: Path) -> None:
        files.write_eula(tmp_path)

        assert "eula=true" in (tmp_path / "eula.txt").read_text().splitlines()

    def test_memory_replaces_previous_values(self, tmp_path: Path) -> None:
        (tmp_path / "user_jvm_args.txt").write_text("# NeoForge\n-Xmx4G\n-XX:+UseG1GC\n")

        files.write_jvm_memory(tmp_path, 1024, 6144)

        assert (tmp_path / "user_jvm_args.txt").read_text().splitlines() == [
            "# NeoForge",
            "-XX:+UseG1GC",
            "-Xms1024M",
            "-Xmx6144M",
        ]

    def test_a_folder_created_by_the_job_is_removed(self, tmp_path: Path) -> None:
        folder = tmp_path / "nouveau"
        (folder / "libraries").mkdir(parents=True)
        (folder / "server.jar").write_bytes(b"jar")

        files.clear_directory(folder, remove_itself=True)

        assert not folder.exists()

    def test_a_folder_that_existed_is_emptied_but_kept(self, tmp_path: Path) -> None:
        folder = tmp_path / "existant"
        (folder / "libraries").mkdir(parents=True)
        (folder / "server.jar").write_bytes(b"jar")

        files.clear_directory(folder, remove_itself=False)

        assert folder.is_dir()
        assert list(folder.iterdir()) == []


@pytest.mark.asyncio
class TestInstaller:
    async def test_a_failing_installer_explains_why(self, tmp_path: Path) -> None:
        """Python refuse l'option `-jar` : un installeur qui échoue, sans réseau ni Java."""
        installer = tmp_path / "neoforge-installer.jar"
        installer.write_bytes(b"pas un jar")

        with pytest.raises(InstallerFailed) as excinfo:
            await run_installer(installer, tmp_path, java=sys.executable, timeout=60)

        assert "Exit code" in (excinfo.value.cause or "")
        assert excinfo.value.remediation


class TestJob:
    def _job(self) -> ProvisioningJob:
        return ProvisioningJob(
            name="survie",
            directory="/data/minecraft/survie",
            distribution="paper",
            version="1.21.1",
            build=None,
            created_by=1,
            steps=[Step(StepKey.FOLDER), Step(StepKey.DOWNLOAD), Step(StepKey.REGISTER)],
        )

    def test_a_failure_marks_the_running_step_and_keeps_the_explanation(self) -> None:
        job = self._job()
        job.begin(StepKey.FOLDER)
        job.finish(StepKey.FOLDER)
        job.begin(StepKey.DOWNLOAD)

        job.fail(
            ValidationError("Téléchargement interrompu.", cause="réseau", remediation="réessayer")
        )

        assert job.status is JobStatus.FAILED
        assert job.step(StepKey.DOWNLOAD).status is StepStatus.FAILED
        assert job.step(StepKey.REGISTER).status is StepStatus.PENDING
        assert job.to_dict()["error"] == {
            "message": "Téléchargement interrompu.",
            "cause": "réseau",
            "remediation": "réessayer",
        }

    def test_an_unexpected_error_still_gives_an_action(self) -> None:
        job = self._job()
        job.begin(StepKey.FOLDER)

        job.fail(OSError("disque plein"))

        error = job.to_dict()["error"]
        assert error["cause"] == "disque plein"
        assert error["remediation"]

    def test_steps_are_labelled_for_the_interface(self) -> None:
        steps = self._job().to_dict()["steps"]

        assert [step["key"] for step in steps] == ["folder", "download", "register"]
        assert all(step["label"] for step in steps)

    def test_the_registry_finds_running_jobs(self) -> None:
        registry = JobRegistry()
        job = self._job()
        registry.add(job)

        assert registry.get(job.id) is job
        assert registry.running() == [job]
        job.complete(7)
        assert registry.running() == []
