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
import re
import sys
from pathlib import Path, PurePath

from msm.exceptions import PathTraversalError
from msm.i18n import tr

#: Segments interdits : `..` bien sûr, mais aussi les flux alternatifs Windows.
_FORBIDDEN_SEGMENTS = {"..", ""}

#: Caractères qui n'ont rien à faire dans un chemin fourni par un client.
_FORBIDDEN_CHARS = frozenset("\x00\n\r")

#: Lettre de lecteur (`C:`) ou chemin racine (`/etc`, `//serveur/partage`).
#:
#: `PurePath` suit la plateforme hôte : sous Linux, `C:/Windows` n'est pas un
#: chemin absolu mais un nom de fichier acceptable, et sous Windows `/etc` n'est
#: pas absolu faute de lecteur. Le confinement resterait assuré dans les deux
#: cas, mais la **réponse** différerait selon la machine — refus ici, fichier
#: créé là. On refuse donc les deux formes partout : une même requête obtient la
#: même réponse, que MSM tourne sous Linux ou sous Windows.
_ROOTED_RE = re.compile(r"^(?:[A-Za-z]:|/)")


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
            tr("Server folder not accessible."),
            cause=str(exc),
            remediation=tr("Check the server path and the access rights."),
        ) from exc

    value = (relative or "").strip().replace("\\", "/")
    if not value or value == ".":
        return root_resolved

    if _FORBIDDEN_CHARS & set(value):
        raise PathTraversalError(
            tr("Path refused."),
            cause=tr("The path contains a control character."),
            remediation=tr("Select the file from the list instead of typing its path."),
        )

    candidate = PurePath(value)
    if candidate.is_absolute() or candidate.drive or _ROOTED_RE.match(value):
        raise PathTraversalError(
            tr("Path refused."),
            cause=tr("“{path}” is an absolute path.", path=relative),
            remediation=tr("Enter a path relative to the server folder."),
        )

    # Le refus explicite de `..` produit un message clair ; la vérification de
    # confinement plus bas resterait de toute façon la garantie réelle.
    for part in candidate.parts:
        if part in _FORBIDDEN_SEGMENTS or part.strip() in _FORBIDDEN_SEGMENTS:
            raise PathTraversalError(
                tr("Path refused."),
                cause=tr("The path tries to climb above the server folder."),
                remediation=tr("Stay inside the server folder tree."),
            )

    try:
        resolved = (root_resolved / candidate).resolve()
    except (OSError, RuntimeError) as exc:
        raise PathTraversalError(
            tr("Unreadable path."),
            cause=str(exc),
            remediation=tr("Check that the path contains no circular link."),
        ) from exc

    if not _is_within(root_resolved, resolved):
        raise PathTraversalError(
            tr("Access denied."),
            cause=tr(
                "“{path}” points to a location outside the server folder ({root}).",
                path=relative,
                root=root_resolved,
            ),
            remediation=tr("Stay inside the server folder tree."),
        )

    if must_exist and not resolved.exists():
        raise PathTraversalError(
            tr("File not found."),
            cause=tr("{path} does not exist in the server folder.", path=relative),
            remediation=tr("Refresh the file list."),
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
