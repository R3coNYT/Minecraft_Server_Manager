"""Hébergement des serveurs des comptes : dossiers, quotas, ports.

Ouvrir MSM au public, c'est laisser des inconnus consommer la machine. Trois
garde-fous, réglés par l'admin :

* **dossiers** — un compte crée ses serveurs dans son propre dossier,
  ``{racine des comptes}/{identifiant du compte}/{serveur}``, et ne choisit pas
  où ; l'identifiant ne change jamais, un changement de pseudo ne déplace rien ;
* **quotas** — nombre de serveurs, mémoire par serveur et mémoire totale des
  serveurs en ligne, espace disque ; une valeur par défaut, surchargeable par
  compte ; les admins de MSM n'en ont pas ;
* **ports** — chaque serveur reçoit un port libre d'une plage réservée, et ce
  port est **réimposé à chaque démarrage** dans `server.properties` : un compte ne
  peut ni prendre le port d'un autre, ni celui d'un service de la machine.
"""

from __future__ import annotations

import asyncio
import os
import re
import time
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from msm.config import Settings
from msm.core.permissions import Permission, Role
from msm.db.models.audit import AuditAction
from msm.db.models.misc import AppSetting, Backup
from msm.db.models.server import Server, ServerSettings
from msm.db.models.user import User
from msm.db.repositories import AuditRepository, ServerRepository
from msm.db.session import session_scope
from msm.exceptions import ConflictError, PermissionDenied, ValidationError
from msm.i18n import tr
from msm.logging_conf import get_logger
from msm.runtime.server_runtime import PreStartHook
from msm.runtime.supervisor import Supervisor
from msm.security.rbac import AccessContext

logger = get_logger(__name__)

HOSTING_KEY = "hosting"
PORT_FLOOR = 1024
#: Mesurer l'espace d'un modpack parcourt des milliers de fichiers : on ne le
#: refait pas à chaque envoi.
DISK_CACHE_S = 300.0


@dataclass(slots=True)
class Quota:
    """Limites d'un compte. ``None`` : pas de limite."""

    max_servers: int | None = 2
    max_memory_per_server_mb: int | None = 4096
    #: Somme de la mémoire maximale des serveurs **en ligne** du compte.
    max_memory_total_mb: int | None = 6144
    max_disk_mb: int | None = 20480

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None, base: Quota | None = None) -> Quota:
        quota = Quota(**asdict(base)) if base is not None else Quota()
        for item in fields(cls):
            if data and item.name in data:
                value = data[item.name]
                setattr(quota, item.name, None if value is None else int(value))
        return quota


@dataclass(slots=True)
class HostingSettings:
    #: Dossier sous lequel chaque compte reçoit le sien ; vide : première racine autorisée.
    users_root: str | None = None
    port_min: int = 25565
    port_max: int = 25664
    quota: Quota = field(default_factory=Quota)

    def to_dict(self) -> dict[str, Any]:
        return {
            "users_root": self.users_root,
            "port_min": self.port_min,
            "port_max": self.port_max,
            "quota": asdict(self.quota),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> HostingSettings:
        data = data or {}
        return cls(
            users_root=data.get("users_root") or None,
            port_min=int(data.get("port_min", 25565)),
            port_max=int(data.get("port_max", 25664)),
            quota=Quota.from_dict(data.get("quota")),
        )


@dataclass(frozen=True, slots=True)
class Usage:
    servers: int
    memory_online_mb: int
    disk_mb: float


#: Espace occupé par compte : (horodatage, mégaoctets).
_disk_cache: dict[int, tuple[float, float]] = {}


def reset_disk_cache() -> None:
    _disk_cache.clear()


def _folder_size(path: Path) -> int:
    total = 0
    for root, _dirs, names in os.walk(path, onerror=lambda _error: None):
        for name in names:
            try:
                total += (Path(root) / name).lstat().st_size
            except OSError:
                continue
    return total


class HostingService:
    def __init__(self, session: AsyncSession, settings: Settings) -> None:
        self._session = session
        self._settings = settings
        self._audit = AuditRepository(session)

    # ------------------------------------------------------------------ #
    #  Réglages
    # ------------------------------------------------------------------ #
    async def load(self) -> HostingSettings:
        row = await self._session.get(AppSetting, HOSTING_KEY)
        return HostingSettings.from_dict(row.value if row is not None else None)

    async def save(
        self, hosting: HostingSettings, *, context: AccessContext, ip_address: str | None = None
    ) -> HostingSettings:
        context.require(Permission.SETTINGS_MANAGE, action=tr("change the settings"))
        if not PORT_FLOOR <= hosting.port_min <= hosting.port_max <= 65535:
            raise ValidationError(
                tr("Invalid port range."),
                cause=tr(
                    "The range must lie between {floor} and 65535, its start before its end.",
                    floor=PORT_FLOOR,
                ),
                remediation=tr("Enter a range such as 25565 to 25664."),
            )
        if hosting.users_root:
            hosting.users_root = str(self._check_root(hosting.users_root))
        row = await self._session.get(AppSetting, HOSTING_KEY)
        if row is None:
            self._session.add(AppSetting(key=HOSTING_KEY, value=hosting.to_dict()))
        else:
            row.value = hosting.to_dict()
        self._audit.record(
            action=AuditAction.SETTINGS_UPDATED,
            summary=tr("Hosting settings updated."),
            actor_id=context.user_id,
            actor_username=context.username,
            actor_role=context.role.value,
            ip_address=ip_address,
            target_type="settings",
            payload=hosting.to_dict(),
        )
        return hosting

    def _check_root(self, raw: str) -> Path:
        from msm.services.server_service import check_within_roots  # dépendance circulaire

        root = Path(raw).expanduser()
        if not root.is_absolute():
            raise ValidationError(
                tr("Invalid folder."),
                cause=tr("{path} is not an absolute path.", path=raw),
                remediation=tr("Enter a full path, such as /data/minecraft/users."),
            )
        # Même contrôle que pour un dossier de serveur : dans les racines autorisées.
        return check_within_roots(self._settings, root.resolve())

    def users_root(self, hosting: HostingSettings) -> Path:
        if hosting.users_root:
            return Path(hosting.users_root)
        roots = self._settings.server_roots
        return Path(roots[0]).expanduser() if roots else self._settings.data_dir / "servers"

    # ------------------------------------------------------------------ #
    #  Dossiers
    # ------------------------------------------------------------------ #
    async def home_of(self, user: User) -> Path:
        return self.users_root(await self.load()) / user.storage_id

    async def directory_for(self, user: User, name: str, *, taken: set[str]) -> Path:
        """Dossier d'un nouveau serveur du compte, jamais celui d'un serveur existant."""
        from msm.services.server_service import slugify

        home = await self.home_of(user)
        base = slugify(name) or "server"
        candidate = home / base
        suffix = 2
        while candidate.exists() or str(candidate) in taken or await self._managed(candidate):
            candidate = home / f"{base}-{suffix}"
            suffix += 1
        return candidate

    async def _managed(self, directory: Path) -> bool:
        statement = select(Server.id).where(Server.directory == str(directory))
        return (await self._session.execute(statement)).first() is not None

    # ------------------------------------------------------------------ #
    #  Ports
    # ------------------------------------------------------------------ #
    async def allocate_port(self, *, reserved: set[int] | None = None) -> int:
        """Le premier port libre de la plage : ni pris par un serveur, ni réservé."""
        hosting = await self.load()
        used = set((await self._session.execute(select(ServerSettings.port))).scalars())
        used |= reserved or set()
        for port in range(hosting.port_min, hosting.port_max + 1):
            if port not in used:
                return port
        raise ConflictError(
            tr("No free port."),
            cause=tr(
                "Every port from {start} to {end} is already taken.",
                start=hosting.port_min,
                end=hosting.port_max,
            ),
            remediation=tr("An administrator must widen the port range."),
        )

    # ------------------------------------------------------------------ #
    #  Quotas
    # ------------------------------------------------------------------ #
    async def quota_for(self, user: User) -> Quota | None:
        """Limites du compte ; ``None`` pour un admin de MSM, qui n'en a pas."""
        if user.role is Role.ADMIN:
            return None
        hosting = await self.load()
        return Quota.from_dict(user.quota, base=hosting.quota)

    async def usage_of(self, user: User, supervisor: Supervisor | None = None) -> Usage:
        owned = list(
            (
                await self._session.execute(
                    select(Server)
                    .options(selectinload(Server.settings))
                    .where(Server.owner_id == user.id)
                )
            )
            .scalars()
            .all()
        )
        online = 0
        if supervisor is not None:
            for server in owned:
                runtime = supervisor.find(server.id)
                if runtime is not None and runtime.state.is_running:
                    online += _memory_of(server)
        return Usage(
            servers=len(owned), memory_online_mb=online, disk_mb=await self.disk_mb(user, owned)
        )

    async def check_creation(self, user: User, *, memory_max_mb: int, pending: int = 0) -> None:
        quota = await self.quota_for(user)
        if quota is None:
            return
        if quota.max_servers is not None:
            statement = select(func.count(Server.id)).where(Server.owner_id == user.id)
            owned = int((await self._session.execute(statement)).scalar() or 0)
            if owned + pending >= quota.max_servers:
                raise _quota_error(
                    tr("Server limit reached."),
                    tr("Your account can have {count} server(s).", count=quota.max_servers),
                    tr("Delete a server, or ask an administrator for more."),
                )
        self._check_memory(quota, memory_max_mb)

    async def check_memory_setting(self, owner: User, memory_max_mb: int | None) -> None:
        quota = await self.quota_for(owner)
        if quota is not None and memory_max_mb is not None:
            self._check_memory(quota, memory_max_mb)

    @staticmethod
    def _check_memory(quota: Quota, memory_max_mb: int) -> None:
        limit = quota.max_memory_per_server_mb
        if limit is not None and memory_max_mb > limit:
            raise _quota_error(
                tr("Too much memory."),
                tr("A server of this account can use {limit} MB at most.", limit=limit),
                tr("Lower the maximum memory, or ask an administrator for more."),
            )

    async def check_start(self, server: Server, supervisor: Supervisor) -> None:
        """La mémoire des serveurs en ligne du propriétaire, celui-ci compris, tient-elle ?"""
        owner = server.owner
        quota = await self.quota_for(owner) if owner is not None else None
        if quota is None or quota.max_memory_total_mb is None:
            return
        usage = await self.usage_of(owner, supervisor)
        needed = usage.memory_online_mb + _memory_of(server)
        if needed > quota.max_memory_total_mb:
            raise _quota_error(
                tr("Not enough memory left."),
                tr(
                    "Its owner's servers online would use {needed} MB, above the {limit} MB "
                    "allowed.",
                    needed=needed,
                    limit=quota.max_memory_total_mb,
                ),
                tr("Stop another server of this account first."),
            )

    async def check_disk(self, owner: User | None) -> None:
        quota = await self.quota_for(owner) if owner is not None else None
        if quota is None or quota.max_disk_mb is None:
            return
        used = await self.disk_mb(owner)  # type: ignore[arg-type]
        if used >= quota.max_disk_mb:
            raise _quota_error(
                tr("Disk space used up."),
                tr(
                    "The servers of this account use {used} MB out of {limit} MB.",
                    used=round(used),
                    limit=quota.max_disk_mb,
                ),
                tr("Delete backups or files, or ask an administrator for more space."),
            )

    async def disk_mb(self, user: User, owned: list[Server] | None = None) -> float:
        cached = _disk_cache.get(user.id)
        if cached is not None and time.monotonic() - cached[0] < DISK_CACHE_S:
            return cached[1]
        if owned is None:
            statement = select(Server).where(Server.owner_id == user.id)
            owned = list((await self._session.execute(statement)).scalars().all())
        folders = [Path(server.directory) for server in owned]
        size = sum([await asyncio.to_thread(_folder_size, folder) for folder in folders])
        if owned:
            statement = select(func.coalesce(func.sum(Backup.size_bytes), 0)).where(
                Backup.server_id.in_([server.id for server in owned])
            )
            size += int((await self._session.execute(statement)).scalar() or 0)
        megabytes = size / (1024 * 1024)
        _disk_cache[user.id] = (time.monotonic(), megabytes)
        return megabytes


def _memory_of(server: Server) -> int:
    settings = server.settings
    return int(settings.memory_max_mb or 0) if settings is not None else 0


def _quota_error(message: str, cause: str, remediation: str) -> PermissionDenied:
    return PermissionDenied(message, cause=cause, remediation=remediation, code="QUOTA_EXCEEDED")


# --------------------------------------------------------------------------- #
#  server.properties
# --------------------------------------------------------------------------- #
#: Ce qu'un serveur de compte ne choisit pas : son port, et ni RCON ni query, qui
#: ouvriraient d'autres ports hors de la plage.
_ENFORCED_KEYS = ("server-port", "enable-rcon", "enable-query")


def enforce_properties(directory: Path, port: int) -> list[str]:
    """Réimpose port, RCON et query dans `server.properties`. Renvoie les clés corrigées."""
    wanted = {"server-port": str(port), "enable-rcon": "false", "enable-query": "false"}
    path = directory / "server.properties"
    lines = path.read_text(encoding="utf-8").splitlines() if path.is_file() else []
    found: set[str] = set()
    changed: list[str] = []
    for index, line in enumerate(lines):
        match = re.match(r"^\s*([A-Za-z0-9.\-]+)\s*=\s*(.*)$", line)
        if not match or match.group(1) not in wanted:
            continue
        key, value = match.group(1), match.group(2).strip()
        found.add(key)
        if value != wanted[key]:
            lines[index] = f"{key}={wanted[key]}"
            changed.append(key)
    for key in _ENFORCED_KEYS:
        if key not in found:
            lines.append(f"{key}={wanted[key]}")
            changed.append(key)
    if changed:
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return changed


def make_network_hook() -> PreStartHook:
    """Avant chaque démarrage d'un serveur de compte : port de la plage, ni RCON ni query."""

    async def hook(server_id: int) -> list[str]:
        async with session_scope() as session:
            server = await ServerRepository(session).get(server_id)
            if server is None or server.owner is None or server.owner.role is Role.ADMIN:
                return []
            port = server.settings.port if server.settings is not None else None
            directory = Path(server.directory)
        if port is None:
            return []
        changed = await asyncio.to_thread(enforce_properties, directory, port)
        if not changed:
            return []
        logger.info("server_properties_enforced", server_id=server_id, keys=changed)
        return [
            tr(
                "server.properties corrected before start ({keys}): the port is {port}, "
                "RCON and query stay off.",
                keys=", ".join(changed),
                port=port,
            )
        ]

    return hook
