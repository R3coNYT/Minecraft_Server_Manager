"""Service des fichiers statiques de l'interface.

En production, MSM sert lui-même le frontend compilé. Ce n'est pas qu'une
commodité d'installation : le panneau et son API partagent alors la **même
origine**, donc le cookie de session fonctionne sans `SameSite=None; Secure`,
sans CORS, et sans reverse proxy obligatoire. Un serveur nginx reste possible
devant, mais n'est plus nécessaire pour que l'ensemble fonctionne.

Le repli vers l'interface est branché sur le **gestionnaire de 404**, et non sur
une route attrape-tout. Une route `/{chemin:path}` masquerait silencieusement
toute route enregistrée après elle ; passer par l'erreur 404 garantit que les
routes réelles gagnent toujours, quel que soit l'ordre d'enregistrement.

Si le dossier compilé est absent — cas du développement, où Vite s'en charge —
rien n'est monté et l'API fonctionne seule.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from msm.config import PROJECT_ROOT
from msm.logging_conf import get_logger

logger = get_logger(__name__)

#: Emplacements possibles du frontend compilé, par ordre de préférence.
CANDIDATE_DIRECTORIES = (
    PROJECT_ROOT / "frontend" / "dist",
    Path("/opt/msm/frontend/dist"),
)

#: Préfixes qui doivent rester des erreurs JSON, jamais du HTML : un appel d'API
#: mal formé doit recevoir une erreur exploitable, pas la page d'accueil.
API_PREFIXES = ("/api", "/ws")


def find_frontend() -> Path | None:
    """Premier dossier de frontend compilé trouvé, ou ``None``."""
    for candidate in CANDIDATE_DIRECTORIES:
        if (candidate / "index.html").is_file():
            return candidate
    return None


def mount_frontend(app: FastAPI, directory: Path | None = None) -> bool:
    """Monte l'interface compilée. Renvoie ``False`` si elle est absente.

    Un dossier fourni explicitement est vérifié comme les autres : sans cela,
    une erreur de chemin dans la configuration produirait un panneau qui répond
    à toutes les requêtes par un fichier introuvable, sans le moindre indice.
    """
    root = directory or find_frontend()
    if root is None or not (root / "index.html").is_file():
        logger.info(
            "frontend_not_mounted",
            hint="index.html introuvable",
            directory=str(root) if root else None,
        )
        app.state.frontend_root = None
        return False

    assets = root / "assets"
    if assets.is_dir():
        app.mount("/assets", StaticFiles(directory=assets), name="assets")

    app.state.frontend_root = root
    logger.info("frontend_mounted", directory=str(root))
    return True


def spa_response(app: FastAPI, path: str) -> FileResponse | None:
    """Réponse d'interface pour un chemin non traité par l'API.

    Renvoie ``None`` quand la requête doit rester une erreur : soit l'interface
    n'est pas installée, soit le chemin vise l'API.
    """
    root: Path | None = getattr(app.state, "frontend_root", None)
    if root is None:
        return None

    normalized = "/" + path.lstrip("/")
    if normalized.startswith(API_PREFIXES):
        return None

    # Un fichier réellement présent est servi tel quel (favicon, manifeste…) ;
    # tout le reste retombe sur l'interface, dont le routage est côté navigateur.
    candidate = (root / normalized.lstrip("/")).resolve()
    if normalized != "/" and candidate.is_file() and root.resolve() in candidate.parents:
        return FileResponse(candidate)

    return FileResponse(root / "index.html")
