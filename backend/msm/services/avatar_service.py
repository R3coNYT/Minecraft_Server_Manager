"""Avatars des comptes.

Une image envoyée par un inconnu n'est jamais servie telle quelle : elle est
décodée, recadrée en carré de 256 px et **réencodée** en WebP. Métadonnées
(position GPS d'une photo…) et contenu piégé disparaissent au passage ; ne reste
qu'une image que MSM a lui-même produite.
"""

from __future__ import annotations

import asyncio
import io
from datetime import UTC, datetime
from pathlib import Path

from PIL import Image, ImageOps, UnidentifiedImageError

from msm.config import Settings
from msm.db.models.user import User
from msm.exceptions import ValidationError
from msm.i18n import tr

AVATAR_SIZE = 256
MAX_UPLOAD_BYTES = 2 * 1024 * 1024
#: Au-delà, une image minuscule sur le disque exploserait en mémoire au décodage.
MAX_PIXELS = 25_000_000
ACCEPTED_FORMATS = frozenset({"PNG", "JPEG", "WEBP"})


def avatar_path(settings: Settings, user_id: int) -> Path:
    return settings.data_dir / "avatars" / f"{user_id}.webp"


def _invalid(cause: str) -> ValidationError:
    return ValidationError(
        tr("Unusable image."),
        cause=cause,
        remediation=tr("Send a PNG, JPEG or WebP image of at most 2 MB."),
    )


def _reencode(data: bytes) -> bytes:
    try:
        with Image.open(io.BytesIO(data)) as source:
            if source.format not in ACCEPTED_FORMATS:
                raise _invalid(tr("Only PNG, JPEG and WebP images are accepted."))
            if source.width * source.height > MAX_PIXELS:
                raise _invalid(tr("The image has too many pixels."))
            source.load()
            image = ImageOps.exif_transpose(source).convert("RGBA")
    except (UnidentifiedImageError, Image.DecompressionBombError, OSError, ValueError) as exc:
        raise _invalid(tr("The file is not a readable image.")) from exc

    square = ImageOps.fit(image, (AVATAR_SIZE, AVATAR_SIZE), Image.Resampling.LANCZOS)
    output = io.BytesIO()
    square.save(output, format="WEBP", quality=85, method=4)
    return output.getvalue()


async def save_avatar(settings: Settings, user: User, data: bytes) -> User:
    if len(data) > MAX_UPLOAD_BYTES:
        raise _invalid(tr("The image weighs more than 2 MB."))
    if not data:
        raise _invalid(tr("The file is empty."))

    encoded = await asyncio.to_thread(_reencode, data)
    target = avatar_path(settings, user.id)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(".tmp")
    temporary.write_bytes(encoded)
    temporary.replace(target)
    user.avatar_updated_at = datetime.now(UTC)
    return user


def delete_avatar(settings: Settings, user: User) -> User:
    avatar_path(settings, user.id).unlink(missing_ok=True)
    user.avatar_updated_at = None
    return user
