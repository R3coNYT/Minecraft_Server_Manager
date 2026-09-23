"""Contrôle des fichiers téléversés.

Un mod ou un plugin est du **code exécuté par le serveur**. Le panneau ne peut
pas juger de son innocuité, mais il peut garantir qu'il ne devient rien d'autre
que ce que l'administrateur a demandé :

* le nom est reconstruit à partir de zéro — jamais celui fourni par le client ;
* l'extension est vérifiée contre une liste courte, propre à chaque dossier ;
* la taille est bornée avant écriture ;
* le fichier n'est **jamais rendu exécutable**, et MSM ne le lance jamais
  lui-même : seul le serveur Minecraft le chargera, à son prochain démarrage.
"""

from __future__ import annotations

import re
import sys
import unicodedata
from pathlib import Path

from msm.exceptions import UnsafeUploadError
from msm.i18n import tr

#: Caractères conservés dans un nom de fichier. Tout le reste devient `_`.
_SAFE_CHARS_RE = re.compile(r"[^A-Za-z0-9._+-]")
#: Points multiples consécutifs, qui servent à masquer une double extension.
_MULTI_DOT_RE = re.compile(r"\.{2,}")

#: Noms réservés par Windows, quelle que soit l'extension. Créer `CON.jar` sur un
#: partage Windows échoue ou produit un comportement inattendu.
_WINDOWS_RESERVED = frozenset(
    {"con", "prn", "aux", "nul"}
    | {f"com{digit}" for digit in range(1, 10)}
    | {f"lpt{digit}" for digit in range(1, 10)}
)

MAX_FILENAME_LENGTH = 120


def sanitize_filename(raw: str, *, allowed_suffixes: frozenset[str]) -> str:
    """Construit un nom de fichier sûr à partir de celui proposé.

    Le nom d'origine n'est jamais réutilisé tel quel : seuls les caractères
    reconnus sont conservés, le chemin est écarté, et l'extension doit figurer
    dans la liste autorisée.
    """
    if not isinstance(raw, str) or not raw.strip():
        raise UnsafeUploadError(
            tr("Missing file name."),
            cause=tr("The upload gives no file name."),
            remediation=tr("Try again, selecting the file from the file browser."),
        )

    # Seul le dernier segment est retenu : « ../../evil.jar » devient « evil.jar ».
    candidate = raw.replace("\\", "/").split("/")[-1].strip()
    # Les accents sont translittérés plutôt que remplacés par des `_`, pour que
    # « modèle-café.jar » reste lisible.
    candidate = unicodedata.normalize("NFKD", candidate).encode("ascii", "ignore").decode()
    candidate = _SAFE_CHARS_RE.sub("_", candidate)
    candidate = _MULTI_DOT_RE.sub(".", candidate).strip("._ ")

    if not candidate:
        raise UnsafeUploadError(
            tr("Invalid file name."),
            cause=tr("The name has no usable character left after cleaning."),
            remediation=tr("Rename the file using letters, digits, `-` or `_`."),
        )

    if len(candidate) > MAX_FILENAME_LENGTH:
        stem, _, suffix = candidate.rpartition(".")
        keep = MAX_FILENAME_LENGTH - len(suffix) - 1
        candidate = f"{stem[:keep]}.{suffix}" if suffix else candidate[:MAX_FILENAME_LENGTH]

    path = Path(candidate)
    suffix = path.suffix.lower()

    if suffix not in allowed_suffixes:
        expected = ", ".join(sorted(allowed_suffixes))
        raise UnsafeUploadError(
            tr("File type not allowed."),
            cause=tr("“{name}” does not have an extension expected here.", name=path.name),
            remediation=tr("This folder only accepts: {expected}.", expected=expected),
        )

    if path.stem.casefold() in _WINDOWS_RESERVED:
        raise UnsafeUploadError(
            tr("Reserved file name."),
            cause=tr("“{name}” is a name reserved by Windows.", name=path.stem),
            remediation=tr("Rename the file before uploading it."),
        )

    return path.name


def check_size(size: int, *, maximum: int) -> int:
    """Vérifie la taille annoncée d'un téléversement."""
    if size <= 0:
        raise UnsafeUploadError(
            tr("Empty file."),
            cause=tr("The uploaded file contains no data."),
            remediation=tr("Check the source file, then try again."),
        )
    if size > maximum:
        raise UnsafeUploadError(
            tr("File too large."),
            cause=tr(
                "{size} MB for a {limit} MB limit.",
                size=f"{size / 1024 / 1024:.1f}",
                limit=maximum // 1024 // 1024,
            ),
            remediation=tr("Raise MSM_UPLOAD_MAX_SIZE_MB, or copy the file by hand."),
        )
    return size


def strip_executable_bit(path: Path) -> None:
    """Retire les droits d'exécution d'un fichier téléversé.

    MSM ne lance jamais un fichier reçu ; le laisser exécutable n'apporterait
    rien et offrirait une cible en cas de faille ailleurs.
    """
    if sys.platform == "win32":
        return
    try:
        mode = path.stat().st_mode
        path.chmod(mode & ~0o111)
    except OSError:  # pragma: no cover - système de fichiers sans permissions
        pass
