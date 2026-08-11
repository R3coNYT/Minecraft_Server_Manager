"""Tests des méthodes de démarrage.

L'exigence structurelle vérifiée ici : une commande de démarrage est toujours une
**liste d'arguments**, jamais une chaîne interprétée par un shell.
"""

from __future__ import annotations

import os
import stat
import sys
from pathlib import Path

import pytest

from msm.exceptions import LaunchError
from msm.launchers import LaunchContext, registry


@pytest.fixture
def server_dir(tmp_path: Path) -> Path:
    directory = tmp_path / "srv"
    directory.mkdir()
    return directory


def context(directory: Path, **kwargs: object) -> LaunchContext:
    return LaunchContext(name="test", directory=directory, **kwargs)  # type: ignore[arg-type]


class TestRegistry:
    def test_default_launchers_are_registered(self) -> None:
        keys = {launcher.key for launcher in registry.all_launchers()}
        assert {"jar", "shell", "batch", "custom"} <= keys

    def test_unknown_key_lists_the_available_ones(self) -> None:
        with pytest.raises(LaunchError) as excinfo:
            registry.get("inexistant")
        assert excinfo.value.remediation and "jar" in excinfo.value.remediation

    def test_description_reports_platform_availability(self) -> None:
        described = {item["key"]: item for item in registry.describe_all()}
        batch = described["batch"]
        if sys.platform == "win32":
            assert batch["unavailable_reason"] is None
        else:
            assert batch["unavailable_reason"] and "Windows" in batch["unavailable_reason"]


class TestJarLauncher:
    def test_builds_a_complete_command(self, server_dir: Path) -> None:
        (server_dir / "server.jar").write_bytes(b"fake")
        spec = registry.build_spec(
            "jar",
            context(
                server_dir,
                jar_path="server.jar",
                java_path=sys.executable,  # exécutable garanti présent
                memory_min_mb=1024,
                memory_max_mb=4096,
                jvm_args=("-XX:+UseG1GC",),
            ),
        )

        assert spec.argv[0] == sys.executable
        assert "-Xms1024M" in spec.argv
        assert "-Xmx4096M" in spec.argv
        assert "-XX:+UseG1GC" in spec.argv
        assert spec.argv[-3] == "-jar"
        assert spec.argv[-2] == str((server_dir / "server.jar").resolve())
        assert spec.argv[-1] == "nogui"
        assert spec.cwd == server_dir.resolve()

    def test_missing_jar_is_reported_with_a_remedy(self, server_dir: Path) -> None:
        with pytest.raises(LaunchError) as excinfo:
            registry.build_spec(
                "jar", context(server_dir, jar_path="absent.jar", java_path=sys.executable)
            )
        assert excinfo.value.remediation

    def test_no_jar_configured(self, server_dir: Path) -> None:
        with pytest.raises(LaunchError, match="JAR"):
            registry.build_spec("jar", context(server_dir, java_path=sys.executable))

    def test_absolute_jar_path_is_refused(self, server_dir: Path, tmp_path: Path) -> None:
        """Un serveur ne doit pas pouvoir désigner un exécutable hors de son dossier."""
        outside = tmp_path / "ailleurs.jar"
        outside.write_bytes(b"fake")
        with pytest.raises(LaunchError, match="absolu"):
            registry.build_spec(
                "jar", context(server_dir, jar_path=str(outside), java_path=sys.executable)
            )

    def test_path_traversal_in_jar_path_is_refused(self, server_dir: Path) -> None:
        with pytest.raises(LaunchError):
            registry.build_spec(
                "jar",
                context(server_dir, jar_path="../../etc/passwd", java_path=sys.executable),
            )

    def test_incoherent_memory_settings(self, server_dir: Path) -> None:
        (server_dir / "server.jar").write_bytes(b"fake")
        with pytest.raises(LaunchError, match="mémoire"):
            registry.build_spec(
                "jar",
                context(
                    server_dir,
                    jar_path="server.jar",
                    java_path=sys.executable,
                    memory_min_mb=8192,
                    memory_max_mb=2048,
                ),
            )

    def test_explicit_jvm_memory_args_take_precedence(self, server_dir: Path) -> None:
        (server_dir / "server.jar").write_bytes(b"fake")
        spec = registry.build_spec(
            "jar",
            context(
                server_dir,
                jar_path="server.jar",
                java_path=sys.executable,
                memory_max_mb=4096,
                jvm_args=("-Xmx8G",),
            ),
        )
        assert "-Xmx8G" in spec.argv
        assert "-Xmx4096M" not in spec.argv


@pytest.mark.skipif(sys.platform == "win32", reason="permissions POSIX")
class TestShellLauncherPosix:
    def test_non_executable_script_gives_the_chmod_remedy(self, server_dir: Path) -> None:
        script = server_dir / "run.sh"
        script.write_text("#!/bin/bash\necho ok\n")
        script.chmod(0o644)

        with pytest.raises(LaunchError) as excinfo:
            registry.build_spec("shell", context(server_dir, script_path="run.sh"))

        assert "exécutable" in (excinfo.value.cause or "")
        assert excinfo.value.remediation and "chmod +x" in excinfo.value.remediation

    def test_executable_script_is_accepted(self, server_dir: Path) -> None:
        script = server_dir / "run.sh"
        script.write_text("#!/bin/bash\necho ok\n")
        script.chmod(script.stat().st_mode | stat.S_IXUSR)

        spec = registry.build_spec("shell", context(server_dir, script_path="run.sh"))
        assert spec.argv == (str(script.resolve()),)


@pytest.mark.skipif(sys.platform != "win32", reason="spécifique à Windows")
class TestBatchLauncherWindows:
    def test_batch_script_uses_cmd(self, server_dir: Path) -> None:
        script = server_dir / "run.bat"
        script.write_text("@echo off\r\necho ok\r\n")

        spec = registry.build_spec("batch", context(server_dir, script_path="run.bat"))
        assert spec.argv[0].casefold().endswith("cmd.exe")
        assert spec.argv[1] == "/c"
        assert spec.argv[2] == str(script.resolve())


@pytest.mark.skipif(sys.platform == "win32", reason="indisponibilité attendue sous Linux")
def test_batch_launcher_is_unavailable_on_linux(server_dir: Path) -> None:
    (server_dir / "run.bat").write_text("echo ok")
    with pytest.raises(LaunchError) as excinfo:
        registry.build_spec("batch", context(server_dir, script_path="run.bat"))
    assert excinfo.value.remediation


class TestCustomLauncher:
    def test_argv_is_used_as_is(self, server_dir: Path) -> None:
        spec = registry.build_spec(
            "custom", context(server_dir, custom_argv=(sys.executable, "-c", "print('ok')"))
        )
        assert spec.argv == (sys.executable, "-c", "print('ok')")

    def test_empty_argv_is_refused(self, server_dir: Path) -> None:
        with pytest.raises(LaunchError, match="personnalisée"):
            registry.build_spec("custom", context(server_dir))

    def test_unknown_program_is_reported(self, server_dir: Path) -> None:
        with pytest.raises(LaunchError) as excinfo:
            registry.build_spec(
                "custom", context(server_dir, custom_argv=("binaire-inexistant-msm",))
            )
        assert excinfo.value.remediation

    def test_shell_metacharacters_are_never_interpreted(self, server_dir: Path) -> None:
        """`;` et `&&` restent des arguments littéraux : aucun shell n'intervient."""
        spec = registry.build_spec(
            "custom",
            context(server_dir, custom_argv=(sys.executable, "-c", "pass", "; rm -rf /")),
        )
        assert spec.argv[-1] == "; rm -rf /"
        assert not isinstance(spec.argv, str)


def test_missing_directory_is_reported(tmp_path: Path) -> None:
    absent = tmp_path / "nexiste-pas"
    with pytest.raises(LaunchError, match=r"[Dd]ossier"):
        registry.build_spec("custom", context(absent, custom_argv=(sys.executable,)))


def test_environment_is_propagated(server_dir: Path) -> None:
    spec = registry.build_spec(
        "custom",
        LaunchContext(
            name="test",
            directory=server_dir,
            custom_argv=(sys.executable,),
            env={"MSM_TEST": "1"},
        ),
    )
    assert spec.env == {"MSM_TEST": "1"}
    assert "MSM_TEST" not in os.environ  # la fusion se fait au lancement, pas ici
