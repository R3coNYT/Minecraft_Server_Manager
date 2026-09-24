"""Langue des textes produits par MSM : erreurs, console, journal d'audit.

L'anglais est la langue source : chaque texte destiné à l'utilisateur est écrit
en anglais dans le code et passe par :func:`tr`, qui le rend dans la langue
choisie. Les traductions françaises vivent dans :mod:`msm.i18n.fr`, indexées
par le texte anglais lui-même — le code reste lisible, et une traduction
manquante se replie sur l'anglais au lieu d'afficher une clé.

La langue **globale** du panneau vaut pour tout ce qui naît hors d'une requête —
lignes système de la console, synchronisation en tâche de fond, notifications
Discord. Chaque compte peut choisir sa propre langue : elle s'applique aux
réponses de ses requêtes (erreurs, messages), le temps de la requête, via
:func:`use_language`.

Les textes paramétrés utilisent la syntaxe de :meth:`str.format` : ::

    tr("Server “{name}” is not running.", name=server.name)
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime
from typing import Literal

from msm.i18n.fr import MESSAGES as _FRENCH

Language = Literal["en", "fr"]

SUPPORTED_LANGUAGES: tuple[Language, ...] = ("en", "fr")
DEFAULT_LANGUAGE: Language = "en"

_language: Language = DEFAULT_LANGUAGE
#: Langue du compte à l'origine de la requête en cours ; prime sur la globale.
_request_language: ContextVar[Language | None] = ContextVar("msm_language", default=None)


def current_language() -> Language:
    """Langue globale du panneau (celle des textes produits hors requête)."""
    return _language


def _effective() -> Language:
    return _request_language.get() or _language


def set_request_language(language: str | None) -> None:
    """Langue du compte courant pour le reste de la requête (``None`` : la globale)."""
    _request_language.set(language if language in SUPPORTED_LANGUAGES else None)  # type: ignore[arg-type]


@contextmanager
def use_language(language: str | None) -> Iterator[None]:
    """Rend les textes dans `language` le temps d'un bloc."""
    token = _request_language.set(
        language if language in SUPPORTED_LANGUAGES else None  # type: ignore[arg-type]
    )
    try:
        yield
    finally:
        _request_language.reset(token)


def set_language(language: str) -> Language:
    """Change la langue de tout ce que MSM produit à partir de maintenant."""
    global _language
    if language not in SUPPORTED_LANGUAGES:
        raise ValueError(f"Unsupported language: {language!r}")
    _language = language  # type: ignore[assignment]
    return _language


def tr(template: str, /, **params: object) -> str:
    """Rend ``template`` dans la langue courante, paramètres substitués."""
    text = _FRENCH.get(template, template) if _effective() == "fr" else template
    return text.format(**params) if params else text


def format_datetime(moment: datetime) -> str:
    """Date et heure dans la forme usuelle de la langue courante."""
    return moment.strftime("%d/%m/%Y %H:%M" if _effective() == "fr" else "%Y-%m-%d %H:%M")


__all__ = [
    "DEFAULT_LANGUAGE",
    "SUPPORTED_LANGUAGES",
    "Language",
    "current_language",
    "format_datetime",
    "set_language",
    "set_request_language",
    "tr",
    "use_language",
]
