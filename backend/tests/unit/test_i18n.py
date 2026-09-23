"""Garde-fous de la traduction : aucun texte utilisateur écrit en dur, catalogue complet.

Ces tests lisent le code source plutôt que de l'exécuter : un texte oublié dans
une branche rarement parcourue — une erreur de restauration, un message de
plantage — ne serait jamais attrapé par un test fonctionnel.
"""

from __future__ import annotations

import ast
import string
from collections.abc import Iterator
from pathlib import Path

import pytest

from msm.i18n import current_language, format_datetime, set_language, tr
from msm.i18n.fr import MESSAGES

PACKAGE = Path(__file__).resolve().parents[2] / "msm"

#: Textes qui ne passent volontairement pas par la traduction :
#: la CLI s'adresse au terminal de l'administrateur, et la configuration est
#: validée avant que la langue choisie puisse être lue en base.
EXEMPT_FILES = {"cli.py", "config.py", "security/crypto.py"}

#: Exceptions justifiées, par (fichier, appel) :
#: l'écho d'une commande dans la console (« > say bonjour (admin) ») ne contient
#: aucune prose, et la raison de fermeture d'un WebSocket n'est jamais affichée.
ALLOWED = {("runtime/log_pipeline.py", "emit_system"), ("ws/endpoint.py", "reason=")}

#: Paramètres nommés porteurs d'un texte destiné à l'utilisateur.
USER_KWARGS = {"cause", "remediation", "summary", "reason"}


def _sources() -> Iterator[tuple[str, ast.Module]]:
    for path in sorted(PACKAGE.rglob("*.py")):
        relative = path.relative_to(PACKAGE).as_posix()
        if relative in EXEMPT_FILES or relative.startswith("i18n/"):
            continue
        yield relative, ast.parse(path.read_text(encoding="utf-8"))


def _is_raw_text(node: ast.AST) -> bool:
    """Un littéral textuel ou une f-string, donc jamais traduit."""
    if isinstance(node, ast.JoinedStr):
        return True
    return isinstance(node, ast.Constant) and isinstance(node.value, str) and bool(node.value)


def _error_classes() -> set[str]:
    import msm.exceptions as exceptions

    names = {
        name
        for name, value in vars(exceptions).items()
        if isinstance(value, type) and issubclass(value, exceptions.MsmError)
    }
    # Sous-classes définies ailleurs dans le paquet.
    return names | {
        "BackupNotSafe",
        "BackupCancelled",
        "DownloadUnavailable",
        "FileServerUnavailable",
        "_Skipped",
    }


def _placeholders(template: str) -> set[str]:
    return {field for _, field, _, _ in string.Formatter().parse(template) if field}


class TestNoHardcodedText:
    def test_errors_are_translated(self) -> None:
        errors = _error_classes()
        offenders: list[str] = []
        for relative, tree in _sources():
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
                if name not in errors:
                    continue
                if node.args and _is_raw_text(node.args[0]):
                    offenders.append(f"{relative}:{node.lineno} message")
                for keyword in node.keywords:
                    if keyword.arg in ("cause", "remediation") and _is_raw_text(keyword.value):
                        offenders.append(f"{relative}:{node.lineno} {keyword.arg}")
        assert not offenders, "Textes d'erreur non traduits :\n" + "\n".join(offenders)

    def test_console_audit_and_permission_texts_are_translated(self) -> None:
        offenders: list[str] = []
        for relative, tree in _sources():
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                name = getattr(node.func, "attr", None) or getattr(node.func, "id", None)
                if name == "emit_system" and node.args and _is_raw_text(node.args[0]):
                    offenders.append(f"{relative}:{node.lineno} emit_system")
                if name == "_record" and len(node.args) > 1 and _is_raw_text(node.args[1]):
                    offenders.append(f"{relative}:{node.lineno} _record")
                for keyword in node.keywords:
                    if (
                        keyword.arg == "action"
                        and name == "require"
                        and _is_raw_text(keyword.value)
                    ):
                        offenders.append(f"{relative}:{node.lineno} require(action=)")
                    # Les appels de journalisation (`logger.info(..., reason=…)`)
                    # portent des clés techniques, et les titres OpenAPI des routes
                    # (`router.get(summary=…)`) documentent l'API : ni l'un ni
                    # l'autre n'est affiché dans l'interface.
                    owner = getattr(getattr(node.func, "value", None), "id", None)
                    if (
                        keyword.arg in USER_KWARGS
                        and isinstance(node.func, ast.Attribute)
                        and owner not in ("logger", "router")
                        and _is_raw_text(keyword.value)
                    ):
                        offenders.append(f"{relative}:{node.lineno} {keyword.arg}=")
        offenders = [
            item for item in offenders if (item.split(":")[0], item.split(" ")[-1]) not in ALLOWED
        ]
        assert not offenders, "Textes utilisateur non traduits :\n" + "\n".join(offenders)


class TestCatalog:
    @staticmethod
    def _literal_templates() -> set[str]:
        found: set[str] = set()
        for _, tree in _sources():
            for node in ast.walk(tree):
                if (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name)
                    and node.func.id == "tr"
                    and node.args
                    and isinstance(node.args[0], ast.Constant)
                    and isinstance(node.args[0].value, str)
                ):
                    found.add(node.args[0].value)
        return found

    def test_every_template_has_a_french_translation(self) -> None:
        missing = sorted(self._literal_templates() - MESSAGES.keys())
        assert not missing, "Traductions françaises manquantes :\n" + "\n".join(missing)

    @pytest.mark.parametrize("template", sorted(MESSAGES))
    def test_translation_keeps_the_same_placeholders(self, template: str) -> None:
        assert _placeholders(MESSAGES[template]) == _placeholders(template)


class TestTranslation:
    def teardown_method(self) -> None:
        set_language("en")

    def test_english_is_the_default(self) -> None:
        assert current_language() == "en"
        assert tr("Server not found.") == "Server not found."

    def test_french_is_applied_with_parameters(self) -> None:
        set_language("fr")
        assert tr("Server “{name}” is not running.", name="survie") == (
            "Le serveur « survie » n'est pas en cours d'exécution."
        )

    def test_missing_translation_falls_back_to_english(self) -> None:
        set_language("fr")
        assert tr("A text nobody translated.") == "A text nobody translated."

    def test_unknown_language_is_refused(self) -> None:
        with pytest.raises(ValueError):
            set_language("de")

    def test_dates_follow_the_language(self) -> None:
        from datetime import datetime

        moment = datetime(2026, 9, 23, 14, 5)
        assert format_datetime(moment) == "2026-09-23 14:05"
        set_language("fr")
        assert format_datetime(moment) == "23/09/2026 14:05"
