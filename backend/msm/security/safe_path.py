"""Résolution de chemin confinée à un répertoire.

C'est la barrière qui empêche `../../../../etc/passwd` d'aboutir. Elle est
volontairement unique : toute opération sur fichier du panneau passe par
:func:`resolve_within`, et aucune route ne construit de chemin elle-même.

Trois pièges sont traités, dans cet ordre :

1. **la remontée par ``..``** — la comparaison porte sur le chemin *résolu*, pas
   sur la chaîne fournie, donc `mods/../../etc` est neutralisé ;
2. **les liens symboliques** — ``resolve()`` les suit avant comparaison. Un lien
   `mods/evil` pointant vers `/etc` désigne bien `/etc` et se fait refuser, alors
   qu'une simple vérification de préfixe textuel l'aurait laissé passer ;
3. **la casse sous Windows** — le système de fichiers est insensible à la casse,
   donc `MODS/..` et `mods/..` désignent la même chose. La comparaison est
   normalisée pour que le contournement par la casse soit impossible.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path, PurePath

from msm.exceptions import PathTraversalError

#: Segments interdits : `..` bien sûr, mais aussi les flux alternatifs Windows.
_FORBIDDEN_SEGMENTS = {"..", ""}

#: Caractères qui n'ont rien à faire dans un chemin fourni par un client.
_FORBIDDEN_CHARS = frozenset("\x00\n\r")


def _normalize(path: PurePath) -> str:
    """Représentation comparable d'un chemin, insensible à la casse sous Windows."""
    text = str(path)
    return os.path.normcase(text) if sys.platform == "win32" else text


def _is_within(root: Path, candidate: Path) -> bool:
    """``candidate`` est-il ``root`` ou situé dessous ?"""
    root_key = _normalize(root)
    candidate_key = _normalize(candidate)
    if candidate_key == root_key:
        return True
    # Le séparateur final évite qu'un dossier voisin nommé « serveur-bis »
    # passe pour un enfant de « serveur ».
    return candidate_key.startswith(root_key.rstrip(os.sep) + os.sep)


def resolve_within(root: Path, relative: str | None, *, must_exist: bool = False) -> Path:
    """Résout ``relative`` sous ``root`` et garantit qu'on n'en sort pas.

    :param root: racine autorisée — typiquement le dossier d'un serveur.
    :param relative: chemin relatif fourni par le client. ``None`` ou vide
        désigne la racine elle-même.
    :param must_exist: exiger que la cible existe déjà.
    :raises PathTraversalError: si le chemin sort de la racine, ou est malformé.
    """
    try:
        root_resolved = root.expanduser().resolve()
    except OSError as exc:  # pragma: no cover - racine inaccessible
        raise PathTraversalError(
            "Dossier du serveur inaccessible.",
            cause=str(exc),
            remediation="Vérifier le chemin du serveur et les droits d'accès.",
        ) from exc

    value = (relative or "").strip().replace("\\", "/")
    if not value or value == ".":
        return root_resolved

    if _FORBIDDEN_CHARS & set(value):
        raise PathTraversalError(
            "Chemin refusé.",
            cause="Le chemin contient un caractère de contrôle.",
            remediation="Sélectionner le fichier depuis la liste plutôt que de saisir son chemin.",
        )

    candidate = PurePath(value)
    if candidate.is_absolute() or candidate.drive or value.startswith("//"):
        raise PathTraversalError(
            "Chemin refusé.",
            cause=f"« {relative} » est un chemin absolu.",
            remediation="Indiquer un chemin relatif au dossier du serveur.",
        )

    # Le refus explicite de `..` produit un message clair ; la vérification de
    # confinement plus bas resterait de toute façon la garantie réelle.
    for part in candidate.parts:
        if part in _FORBIDDEN_SEGMENTS or part.strip() in _FORBIDDEN_SEGMENTS:
            raise PathTraversalError(
                "Chemin refusé.",
                cause="Le chemin tente de remonter au-dessus du dossier du serveur.",
                remediation="Rester dans l'arborescence du serveur.",
            )

    try:
        resolved = (root_resolved / candidate).resolve()
    except (OSError, RuntimeError) as exc:
        raise PathTraversalError(
            "Chemin illisible.",
            cause=str(exc),
            remediation="Vérifier que le chemin ne contient pas de lien circulaire.",
        ) from exc

    if not _is_within(root_resolved, resolved):
        raise PathTraversalError(
            "Accès refusé.",
            cause=(
                f"« {relative} » désigne un emplacement situé hors du dossier du serveur "
                f"({root_resolved})."
            ),
            remediation="Rester dans l'arborescence du serveur.",
        )

    if must_exist and not resolved.exists():
        raise PathTraversalError(
            "Fichier introuvable.",
            cause=f"{relative} n'existe pas dans le dossier du serveur.",
            remediation="Rafraîchir la liste des fichiers.",
            code="NOT_FOUND",
            status_code=404,
        )

    return resolved


def relative_to_root(root: Path, target: Path) -> str:
    """Chemin de ``target`` relatif à ``root``, en séparateurs POSIX.

    L'API expose toujours des `/`, quel que soit le système hôte : le frontend
    n'a pas à connaître la plateforme du serveur.
    """
    try:
        return target.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:  # pragma: no cover - appelé après resolve_within
        return target.name


def is_within(root: Path, candidate: Path) -> bool:
    """Variante non levante, pour filtrer des listes."""
    try:
        return _is_within(root.resolve(), candidate.resolve())
    except OSError:  # pragma: no cover
        return False
