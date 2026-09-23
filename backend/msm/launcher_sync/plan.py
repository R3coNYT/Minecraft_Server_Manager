"""Ce que la synchronisation doit faire — calculé sans rien toucher.

Le calcul est **pur** : il reçoit ce qu'annonce le serveur de fichiers, ce qui
est sur le disque et ce que MSM a lui-même installé, et rend une liste
d'opérations. Aucune écriture ici. C'est ce qui permet de l'examiner avant de
l'appliquer — pour bloquer une suppression massive, ou pour la mettre en attente
quand le serveur tourne.

Trois règles, qui reprennent celles du launcher côté joueur :

* **MSM ne supprime que ce qu'il a installé.** Un mod ajouté à la main sur le
  serveur n'est jamais touché, même absent du manifest ;
* **un fichier identique n'est pas retéléchargé**, il est simplement pris en
  charge ;
* **la désactivation décidée dans MSM l'emporte** : un mod désactivé reste
  désactivé, même mis à jour.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from msm.i18n import tr
from msm.launcher_sync.manifest import ManifestEntry

#: En deçà, une suppression n'a rien de suspect, quel que soit le pourcentage.
MASS_DELETION_MIN = 5
#: Au-delà de cette part des fichiers suivis, la suppression est bloquée.
MASS_DELETION_RATIO = 0.5


@dataclass(frozen=True, slots=True)
class LocalFile:
    """Un fichier présent sur le serveur, sous son nom d'origine ou `.disabled`."""

    path: str
    sha256: str
    disabled: bool


@dataclass(frozen=True, slots=True)
class Install:
    """Un fichier à télécharger et à mettre en place."""

    path: str
    sha256: str
    size: int
    #: Écrire sous le nom `.disabled` : l'état décidé dans MSM est préservé.
    disabled: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "sha256": self.sha256,
            "size": self.size,
            "disabled": self.disabled,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> Install:
        return cls(
            path=str(raw["path"]),
            sha256=str(raw["sha256"]),
            size=int(raw["size"]),
            disabled=bool(raw.get("disabled", False)),
        )


@dataclass(frozen=True, slots=True)
class Plan:
    """Opérations à appliquer au serveur."""

    installs: tuple[Install, ...] = ()
    #: Chemins à retirer, sous leurs deux noms possibles.
    removes: tuple[str, ...] = ()
    #: Fichiers déjà corrects, pris en charge sans téléchargement.
    adopts: tuple[tuple[str, str], ...] = ()
    unchanged: int = 0
    notes: tuple[str, ...] = field(default_factory=tuple)

    @property
    def changes(self) -> int:
        return len(self.installs) + len(self.removes)

    @property
    def empty(self) -> bool:
        return not self.installs and not self.removes

    @property
    def download_bytes(self) -> int:
        return sum(item.size for item in self.installs)


def compute_plan(
    server_entries: list[ManifestEntry],
    local: dict[str, LocalFile],
    tracked: dict[str, str],
) -> Plan:
    """Calcule les opérations qui alignent le serveur sur le manifest.

    :param server_entries: fichiers du manifest destinés au serveur — les mods
        client-only en ont déjà été retirés.
    :param local: fichiers présents sur le disque, par chemin de manifest.
    :param tracked: fichiers installés par MSM lors des synchronisations
        précédentes, avec l'empreinte installée.
    """
    installs: list[Install] = []
    adopts: list[tuple[str, str]] = []
    notes: list[str] = []
    unchanged = 0
    wanted = {entry.path for entry in server_entries}

    for entry in server_entries:
        current = local.get(entry.path)

        if current is not None and current.sha256 == entry.sha256:
            if entry.path in tracked:
                unchanged += 1
            else:
                adopts.append((entry.path, entry.sha256))
            continue

        if current is not None and entry.path not in tracked:
            # Une autre version posée à la main : le serveur de fichiers fait
            # autorité sur les dossiers synchronisés, mais on le dit.
            notes.append(tr("{path} replaced by the file server version.", path=entry.path))

        # Présent localement : on garde l'état choisi dans MSM. Absent : on
        # reprend l'état publié, pour qu'un serveur neuf reflète le modpack.
        disabled = current.disabled if current is not None else entry.disabled
        installs.append(
            Install(path=entry.path, sha256=entry.sha256, size=entry.size, disabled=disabled)
        )

    removes = tuple(sorted(path for path in tracked if path not in wanted))

    return Plan(
        installs=tuple(installs),
        removes=removes,
        adopts=tuple(adopts),
        unchanged=unchanged,
        notes=tuple(notes),
    )


def is_mass_deletion(plan: Plan, tracked_count: int) -> bool:
    """La suppression est-elle assez massive pour être suspecte ?

    Un manifest régénéré à moitié, ou pointé vers le mauvais dossier, annonce
    soudain un modpack vide. Sans ce garde-fou, la synchronisation suivante
    viderait le serveur de ses mods.
    """
    removed = len(plan.removes)
    return removed > MASS_DELETION_MIN and removed > MASS_DELETION_RATIO * tracked_count


def plan_to_dict(plan: Plan) -> dict[str, Any]:
    """Forme persistée d'un plan mis en attente."""
    return {
        "installs": [item.to_dict() for item in plan.installs],
        "removes": list(plan.removes),
        "adopts": [list(item) for item in plan.adopts],
    }


def plan_from_dict(raw: dict[str, Any] | None) -> Plan:
    if not raw:
        return Plan()
    return Plan(
        installs=tuple(Install.from_dict(item) for item in raw.get("installs", [])),
        removes=tuple(str(path) for path in raw.get("removes", [])),
        adopts=tuple((str(item[0]), str(item[1])) for item in raw.get("adopts", [])),
    )
