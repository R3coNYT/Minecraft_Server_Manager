"""Opérations sur le disque du serveur : inventaire, préparation, application.

Tout ce qui écrit passe par :func:`resolve_within` — les chemins viennent d'un
autre serveur — et par un remplacement atomique : un mod est soit l'ancien, soit
le nouveau, jamais un fichier à moitié copié qu'un démarrage chargerait.

Les fonctions sont **bloquantes** : elles hachent et déplacent des fichiers de
plusieurs mégaoctets, et s'exécutent dans un thread.
"""

from __future__ import annotations

import hashlib
import shutil
from pathlib import Path

from msm.launcher_sync.plan import LocalFile, Plan
from msm.logging_conf import get_logger
from msm.security.safe_path import resolve_within

logger = get_logger(__name__)

DISABLED_SUFFIX = ".disabled"
_CHUNK = 1024 * 1024


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(_CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def scan_local(
    server_dir: Path,
    prefixes: tuple[str, ...],
    known: dict[str, tuple[int, int, str]],
) -> dict[str, LocalFile]:
    """Inventorie les fichiers sous les dossiers synchronisés.

    :param known: empreintes déjà calculées, par chemin : ``(taille, mtime_ns,
        sha256)``. Un fichier dont taille et date n'ont pas bougé n'est pas
        re-haché — sans cela, chaque synchronisation relirait des centaines de
        mégaoctets de mods pour constater que rien n'a changé.
    """
    found: dict[str, LocalFile] = {}
    for prefix in prefixes:
        root = resolve_within(server_dir, prefix.rstrip("/"))
        if not root.is_dir():
            continue
        for file in root.rglob("*"):
            if not file.is_file() or file.is_symlink():
                continue
            relative = file.relative_to(server_dir).as_posix()
            disabled = relative.endswith(DISABLED_SUFFIX)
            path = relative[: -len(DISABLED_SUFFIX)] if disabled else relative

            stat = file.stat()
            cached = known.get(relative)
            if cached is not None and cached[0] == stat.st_size and cached[1] == stat.st_mtime_ns:
                digest = cached[2]
            else:
                digest = sha256_of(file)

            # Les deux variantes coexistent rarement ; l'active l'emporte alors.
            if path in found and not found[path].disabled:
                continue
            found[path] = LocalFile(path=path, sha256=digest, disabled=disabled)
    return found


def disabled_paths(server_dir: Path, prefixes: tuple[str, ...]) -> list[str]:
    """Chemins de manifest des fichiers désactivés sur le serveur."""
    result: list[str] = []
    for prefix in prefixes:
        root = resolve_within(server_dir, prefix.rstrip("/"))
        if not root.is_dir():
            continue
        for file in root.rglob(f"*{DISABLED_SUFFIX}"):
            if file.is_file() and not file.is_symlink():
                relative = file.relative_to(server_dir).as_posix()
                result.append(relative[: -len(DISABLED_SUFFIX)])
    return sorted(result)


def staged_path(staging_dir: Path, sha256: str) -> Path:
    """Emplacement d'un fichier préparé, nommé par son empreinte.

    Nommer par l'empreinte évite toute collision et rend la préparation
    reprenable : un fichier déjà téléchargé et vérifié n'est pas redemandé.
    """
    return staging_dir / sha256


def apply_plan(server_dir: Path, staging_dir: Path, plan: Plan) -> list[str]:
    """Applique un plan au disque. Renvoie les messages à afficher.

    Les retraits passent **après** les installations : si une installation
    échoue en cours de route, le serveur garde ses anciens mods plutôt que de se
    retrouver avec moins qu'avant.
    """
    messages: list[str] = []

    for item in plan.installs:
        source = staged_path(staging_dir, item.sha256)
        name = item.path + (DISABLED_SUFFIX if item.disabled else "")
        target = resolve_within(server_dir, name)
        other = resolve_within(
            server_dir, item.path if item.disabled else item.path + DISABLED_SUFFIX
        )

        target.parent.mkdir(parents=True, exist_ok=True)
        # Copie vers un temporaire du même dossier, puis remplacement atomique :
        # le remplacement n'est atomique qu'au sein d'un même système de fichiers.
        temporary = target.with_name(target.name + ".msm-tmp")
        shutil.copyfile(source, temporary)
        temporary.replace(target)
        if other.exists():
            other.unlink()

    for path in plan.removes:
        for name in (path, path + DISABLED_SUFFIX):
            target = resolve_within(server_dir, name)
            if target.is_file():
                target.unlink()

    if plan.installs:
        messages.append(f"{len(plan.installs)} fichier(s) installé(s) ou mis à jour.")
    if plan.removes:
        messages.append(f"{len(plan.removes)} fichier(s) retiré(s).")
    return messages


def clear_staging(staging_dir: Path) -> None:
    shutil.rmtree(staging_dir, ignore_errors=True)
