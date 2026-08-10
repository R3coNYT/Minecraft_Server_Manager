"""Écritures de fichiers durables.

Une écriture directe qui s'interrompt — coupure de courant, disque plein, panneau
tué — laisse un fichier tronqué. Pour un `server.properties` ou un fichier de
configuration de mod, cela suffit à empêcher le serveur de redémarrer.

Toutes les écritures du panneau passent donc par un fichier temporaire du même
dossier, suivi d'un remplacement atomique. Le fichier existant reste intact
jusqu'au dernier instant : au pire, la modification n'a pas eu lieu.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path


def atomic_write_bytes(path: Path, content: bytes) -> None:
    """Écrit ``content`` dans ``path`` sans jamais laisser de fichier partiel."""
    directory = path.parent
    directory.mkdir(parents=True, exist_ok=True)

    # Le fichier temporaire doit être sur le même système de fichiers que la
    # cible, sinon `os.replace` ne peut pas être atomique.
    descriptor, temporary = tempfile.mkstemp(dir=directory, prefix=".msm-", suffix=".tmp")
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        # Atomique sur POSIX comme sous Windows.
        Path(temporary).replace(path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def atomic_write_text(path: Path, content: str, *, encoding: str = "utf-8") -> None:
    """Variante texte. Les fins de ligne ne sont pas converties."""
    atomic_write_bytes(path, content.encode(encoding))


def read_text_guessing_encoding(path: Path) -> tuple[str, str]:
    """Lit un fichier texte et renvoie ``(contenu, encodage retenu)``.

    ``utf-8-sig`` est essayé d'abord : il gère l'UTF-8 avec ou sans marque
    d'ordre d'octets, cette dernière étant fréquente sur les fichiers édités
    sous Windows. En dernier recours, ``latin-1`` ne peut pas échouer et
    préserve les octets d'origine.
    """
    raw = path.read_bytes()
    for encoding in ("utf-8-sig", "utf-8"):
        try:
            return raw.decode(encoding), encoding
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1"), "latin-1"
