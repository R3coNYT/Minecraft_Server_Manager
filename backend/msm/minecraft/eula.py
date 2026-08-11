"""Lecture et acceptation du CLUF Minecraft (``eula.txt``).

Au premier lancement, un serveur Minecraft s'arrête immédiatement et écrit un
``eula.txt`` contenant ``eula=false``. MSM peut basculer cette valeur à ``true``
si l'administrateur a activé l'option — c'est lui qui accepte le contrat, MSM ne
fait qu'appliquer sa décision.

Deux règles strictes :

* **une seule ligne est modifiée**, celle de la clé ``eula``. Le commentaire
  d'en-tête, l'horodatage, les fins de ligne et l'encodage d'origine sont
  conservés à l'octet près — d'où un traitement en octets plutôt qu'en texte.
* **l'écriture est atomique** (fichier temporaire puis remplacement) : une coupure
  en cours d'écriture ne peut pas laisser un ``eula.txt`` tronqué.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from msm.logging_conf import get_logger
from msm.utils.files import atomic_write_bytes

logger = get_logger(__name__)

EULA_FILENAME = "eula.txt"

#: Ligne `eula=<valeur>`, tolérante aux espaces. Les commentaires (`#`) sont exclus.
_EULA_LINE_RE = re.compile(rb"^(?P<prefix>\s*eula\s*=\s*)(?P<value>[^\r\n]*)(?P<eol>\r?\n?)$", re.I)


@dataclass(frozen=True, slots=True)
class EulaStatus:
    """État du fichier ``eula.txt`` d'un serveur."""

    path: Path
    exists: bool
    accepted: bool

    @property
    def needs_acceptance(self) -> bool:
        """Le fichier existe et refuse encore le contrat."""
        return self.exists and not self.accepted


def eula_path(directory: Path) -> Path:
    return directory / EULA_FILENAME


def read_status(directory: Path) -> EulaStatus:
    """Lit l'état du CLUF. Un fichier absent n'est pas une erreur.

    Un serveur qui n'a jamais démarré n'a pas encore de ``eula.txt`` : il le
    créera lui-même au premier lancement.
    """
    path = eula_path(directory)
    if not path.is_file():
        return EulaStatus(path=path, exists=False, accepted=False)

    try:
        content = path.read_bytes()
    except OSError as exc:
        logger.warning("eula_read_failed", path=str(path), error=str(exc))
        return EulaStatus(path=path, exists=True, accepted=False)

    for raw_line in content.splitlines():
        if raw_line.lstrip().startswith(b"#"):
            continue
        match = _EULA_LINE_RE.match(raw_line + b"\n")
        if match is not None:
            return EulaStatus(
                path=path,
                exists=True,
                accepted=match["value"].strip().lower() == b"true",
            )

    return EulaStatus(path=path, exists=True, accepted=False)


def accept(directory: Path) -> bool:
    """Passe ``eula`` à ``true``. Renvoie ``True`` si le fichier a été modifié.

    Renvoie ``False`` si le fichier n'existe pas ou si le contrat était déjà
    accepté — dans les deux cas il n'y a rien à faire, ce n'est pas une erreur.
    """
    status = read_status(directory)
    if not status.exists or status.accepted:
        return False

    path = status.path
    original = path.read_bytes()
    lines = original.splitlines(keepends=True)

    modified = False
    for index, raw_line in enumerate(lines):
        if raw_line.lstrip().startswith(b"#"):
            continue
        match = _EULA_LINE_RE.match(raw_line)
        if match is None:
            continue
        lines[index] = match["prefix"] + b"true" + match["eol"]
        modified = True
        break

    if not modified:  # pragma: no cover - incohérent avec read_status
        return False

    atomic_write_bytes(path, b"".join(lines))
    logger.info("eula_accepted", path=str(path))
    return True
