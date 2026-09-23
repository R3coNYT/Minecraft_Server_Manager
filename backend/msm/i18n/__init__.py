"""Langue des textes produits par MSM : erreurs, console, journal d'audit.

L'anglais est la langue source : chaque texte destiné à l'utilisateur est écrit
en anglais dans le code et passe par :func:`tr`, qui le rend dans la langue
choisie. Les traductions françaises vivent dans :mod:`msm.i18n.fr`, indexées
par le texte anglais lui-même — le code reste lisible, et une traduction
manquante se replie sur l'anglais au lieu d'afficher une clé.

La langue est un **réglage global** du panneau, et non une préférence par
navigateur : une bonne partie des textes naît hors de toute requête — lignes
système de la console, synchronisation en tâche de fond, notifications Discord —
et doit pourtant sortir dans la langue de l'interface.

Les textes paramétrés utilisent la syntaxe de :meth:`str.format` : ::

    tr("Server “{name}” is not running.", name=server.name)
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from msm.i18n.fr import MESSAGES as _FRENCH

Language = Literal["en", "fr"]

SUPPORTED_LANGUAGES: tuple[Language, ...] = ("en", "fr")
DEFAULT_LANGUAGE: Language = "en"

_language: Language = DEFAULT_LANGUAGE


def current_language() -> Language:
    return _language


def set_language(language: str) -> Language:
    """Change la langue de tout ce que MSM produit à partir de maintenant."""
    global _language
    if language not in SUPPORTED_LANGUAGES:
        raise ValueError(f"Unsupported language: {language!r}")
    _language = language  # type: ignore[assignment]
    return _language


def tr(template: str, /, **params: object) -> str:
    """Rend ``template`` dans la langue courante, paramètres substitués."""
    text = _FRENCH.get(template, template) if _language == "fr" else template
    return text.format(**params) if params else text


def format_datetime(moment: datetime) -> str:
    """Date et heure dans la forme usuelle de la langue courante."""
    return moment.strftime("%d/%m/%Y %H:%M" if _language == "fr" else "%Y-%m-%d %H:%M")


__all__ = [
    "DEFAULT_LANGUAGE",
    "SUPPORTED_LANGUAGES",
    "Language",
    "current_language",
    "format_datetime",
    "set_language",
    "tr",
]
