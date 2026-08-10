"""Lecture et écriture de ``server.properties``.

Le fichier est traité **ligne par ligne**, jamais reconstruit à partir d'un
dictionnaire : les commentaires, l'ordre des clés et les clés inconnues d'une
version future de Minecraft doivent survivre à une modification depuis le
panneau. Seules les lignes effectivement modifiées sont réécrites.

Les métadonnées (type, valeurs possibles, redémarrage nécessaire) ne servent
qu'à l'affichage : une clé absente de ce catalogue reste éditable comme du texte.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from msm.exceptions import ValidationError
from msm.utils.files import atomic_write_text, read_text_guessing_encoding

PROPERTIES_FILE = "server.properties"

PropertyType = Literal["boolean", "integer", "string", "enum"]

_LINE_RE = re.compile(r"^(?P<key>[^#!=:\s][^=:]*?)\s*[=:]\s*(?P<value>.*)$")


@dataclass(frozen=True, slots=True)
class PropertyMeta:
    """Description d'une clé connue, pour construire un champ de formulaire."""

    key: str
    label: str
    type: PropertyType = "string"
    choices: tuple[str, ...] = ()
    minimum: int | None = None
    maximum: int | None = None
    #: Le serveur doit-il redémarrer pour que la valeur prenne effet ?
    requires_restart: bool = True
    help: str = ""


#: Clés mises en avant dans l'interface. Le reste reste éditable en liste brute.
KNOWN_PROPERTIES: tuple[PropertyMeta, ...] = (
    PropertyMeta("motd", "Message d'accueil", "string", help="Affiché dans la liste des serveurs."),
    PropertyMeta("server-port", "Port", "integer", minimum=1, maximum=65535),
    PropertyMeta("max-players", "Joueurs maximum", "integer", minimum=1, maximum=10000),
    PropertyMeta(
        "gamemode",
        "Mode de jeu",
        "enum",
        choices=("survival", "creative", "adventure", "spectator"),
        requires_restart=False,
    ),
    PropertyMeta(
        "difficulty",
        "Difficulté",
        "enum",
        choices=("peaceful", "easy", "normal", "hard"),
        requires_restart=False,
    ),
    PropertyMeta("pvp", "Combat entre joueurs", "boolean", requires_restart=False),
    PropertyMeta("hardcore", "Mode extrême", "boolean"),
    PropertyMeta("white-list", "Liste blanche", "boolean", requires_restart=False),
    PropertyMeta(
        "online-mode",
        "Authentification Mojang",
        "boolean",
        help="Désactiver autorise les comptes non authentifiés : à réserver aux réseaux privés.",
    ),
    PropertyMeta("allow-flight", "Autoriser le vol", "boolean"),
    PropertyMeta("allow-nether", "Autoriser le Nether", "boolean"),
    PropertyMeta("view-distance", "Distance de vue", "integer", minimum=2, maximum=32),
    PropertyMeta("simulation-distance", "Distance de simulation", "integer", minimum=3, maximum=32),
    PropertyMeta("spawn-protection", "Protection du point d'apparition", "integer", minimum=0),
    PropertyMeta("enable-command-block", "Blocs de commande", "boolean"),
    PropertyMeta("level-name", "Nom du monde", "string"),
    PropertyMeta("level-seed", "Graine du monde", "string"),
    PropertyMeta("enforce-whitelist", "Appliquer la liste blanche", "boolean"),
    PropertyMeta("enable-rcon", "Activer RCON", "boolean"),
    PropertyMeta("rcon.port", "Port RCON", "integer", minimum=1, maximum=65535),
)

_META_BY_KEY = {meta.key: meta for meta in KNOWN_PROPERTIES}


@dataclass(slots=True)
class PropertyEntry:
    """Une clé du fichier, avec sa valeur et ses métadonnées si elle est connue."""

    key: str
    value: str
    line: int
    meta: PropertyMeta | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "value": self.value,
            "known": self.meta is not None,
            "label": self.meta.label if self.meta else self.key,
            "type": self.meta.type if self.meta else "string",
            "choices": list(self.meta.choices) if self.meta else [],
            "minimum": self.meta.minimum if self.meta else None,
            "maximum": self.meta.maximum if self.meta else None,
            "requires_restart": self.meta.requires_restart if self.meta else True,
            "help": self.meta.help if self.meta else "",
        }


@dataclass(slots=True)
class PropertiesFile:
    """Contenu analysé de ``server.properties``."""

    path: Path
    exists: bool
    lines: list[str] = field(default_factory=list)
    entries: list[PropertyEntry] = field(default_factory=list)
    encoding: str = "utf-8"

    def get(self, key: str) -> str | None:
        for entry in self.entries:
            if entry.key == key:
                return entry.value
        return None


def properties_path(directory: Path) -> Path:
    return directory / PROPERTIES_FILE


def read(directory: Path) -> PropertiesFile:
    """Analyse ``server.properties``. Un fichier absent n'est pas une erreur."""
    path = properties_path(directory)
    if not path.is_file():
        return PropertiesFile(path=path, exists=False)

    try:
        content, encoding = read_text_guessing_encoding(path)
    except OSError as exc:
        raise ValidationError(
            "Fichier de configuration illisible.",
            cause=str(exc),
            remediation="Vérifier les droits d'accès sur server.properties.",
        ) from exc

    lines = content.splitlines()
    entries: list[PropertyEntry] = []

    for index, line in enumerate(lines):
        stripped = line.strip()
        if not stripped or stripped.startswith(("#", "!")):
            continue
        match = _LINE_RE.match(line)
        if match is None:
            continue
        key = match["key"].strip()
        entries.append(
            PropertyEntry(
                key=key,
                value=match["value"].strip(),
                line=index,
                meta=_META_BY_KEY.get(key),
            )
        )

    return PropertiesFile(path=path, exists=True, lines=lines, entries=entries, encoding=encoding)


def validate_value(key: str, value: str) -> str:
    """Valide une valeur selon les métadonnées de la clé, si elle est connue."""
    meta = _META_BY_KEY.get(key)
    if meta is None:
        # Clé inconnue : on refuse seulement ce qui casserait le format.
        return _reject_line_breaks(key, value)

    clean = _reject_line_breaks(key, value)

    if meta.type == "boolean":
        if clean.lower() not in ("true", "false"):
            raise ValidationError(
                f"Valeur invalide pour « {meta.label} ».",
                cause=f"« {value} » n'est ni `true` ni `false`.",
                remediation="Utiliser la case à cocher pour choisir la valeur.",
            )
        return clean.lower()

    if meta.type == "integer":
        try:
            number = int(clean)
        except ValueError:
            raise ValidationError(
                f"Valeur invalide pour « {meta.label} ».",
                cause=f"« {value} » n'est pas un nombre entier.",
                remediation="Saisir un nombre entier.",
            ) from None
        if meta.minimum is not None and number < meta.minimum:
            raise ValidationError(
                f"Valeur trop basse pour « {meta.label} ».",
                cause=f"{number} est inférieur au minimum ({meta.minimum}).",
                remediation=f"Saisir une valeur d'au moins {meta.minimum}.",
            )
        if meta.maximum is not None and number > meta.maximum:
            raise ValidationError(
                f"Valeur trop élevée pour « {meta.label} ».",
                cause=f"{number} dépasse le maximum ({meta.maximum}).",
                remediation=f"Saisir une valeur d'au plus {meta.maximum}.",
            )
        return str(number)

    if meta.type == "enum" and clean not in meta.choices:
        raise ValidationError(
            f"Valeur invalide pour « {meta.label} ».",
            cause=f"« {value} » ne fait pas partie des valeurs acceptées.",
            remediation=f"Choisir parmi : {', '.join(meta.choices)}.",
        )

    return clean


def _reject_line_breaks(key: str, value: str) -> str:
    if "\n" in value or "\r" in value:
        raise ValidationError(
            f"Valeur invalide pour « {key} ».",
            cause="La valeur contient un saut de ligne, ce que le format n'admet pas.",
            remediation="Saisir la valeur sur une seule ligne.",
        )
    return value.strip()


def apply_changes(directory: Path, changes: dict[str, str]) -> tuple[list[str], bool]:
    """Applique des modifications et réécrit le fichier.

    Renvoie ``(clés modifiées, redémarrage nécessaire)``. Les clés absentes du
    fichier sont ajoutées à la fin ; celles qui n'ont pas changé de valeur sont
    ignorées, pour que le fichier ne soit pas réécrit inutilement.
    """
    if not changes:
        return [], False

    parsed = read(directory)
    if not parsed.exists:
        raise ValidationError(
            "Fichier server.properties introuvable.",
            cause="Le serveur ne l'a pas encore généré.",
            remediation="Démarrer le serveur une première fois pour qu'il crée ses fichiers.",
        )

    lines = list(parsed.lines)
    by_key = {entry.key: entry for entry in parsed.entries}
    applied: list[str] = []
    needs_restart = False

    for key, raw_value in changes.items():
        clean_key = key.strip()
        if not clean_key or "=" in clean_key or clean_key.startswith("#"):
            raise ValidationError(
                "Clé de configuration invalide.",
                cause=f"« {key} » n'est pas un nom de propriété valide.",
                remediation="Utiliser les champs proposés par l'interface.",
            )

        value = validate_value(clean_key, str(raw_value))
        entry = by_key.get(clean_key)

        if entry is not None:
            if entry.value == value:
                continue
            lines[entry.line] = f"{clean_key}={value}"
        else:
            lines.append(f"{clean_key}={value}")

        applied.append(clean_key)
        meta = _META_BY_KEY.get(clean_key)
        needs_restart = needs_restart or (meta.requires_restart if meta else True)

    if not applied:
        return [], False

    # Le fichier conserve sa fin de ligne finale, comme l'écrit Minecraft.
    atomic_write_text(parsed.path, "\n".join(lines) + "\n", encoding="utf-8")
    return applied, needs_restart
