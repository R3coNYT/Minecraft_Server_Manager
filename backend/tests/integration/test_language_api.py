"""Langue de l'interface : réglage global, appliqué aux textes produits par MSM."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from httpx import AsyncClient

from msm.i18n import set_language
from msm.services.settings_service import load_language
from tests.integration.conftest import ApiClient

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _english_afterwards() -> Iterator[None]:
    """La langue est globale au processus : chaque test la rend telle qu'il l'a trouvée."""
    yield
    set_language("en")


async def test_language_is_readable_before_signing_in(client: AsyncClient) -> None:
    """L'écran de connexion doit déjà s'afficher dans la bonne langue."""
    response = await client.get("/api/v1/ui")

    assert response.status_code == 200
    assert response.json() == {"language": "en", "languages": ["en", "fr"]}


async def test_switching_to_french_translates_errors(admin: ApiClient) -> None:
    response = await admin.put("/api/v1/settings/language", json={"language": "fr"})
    assert response.status_code == 200, response.text

    missing = await admin.get("/api/v1/servers/999")

    assert missing.status_code == 404
    assert missing.json()["message"] == "Serveur introuvable."
    assert (await admin.get("/api/v1/ui")).json()["language"] == "fr"


async def test_choice_survives_a_restart(admin: ApiClient) -> None:
    await admin.put("/api/v1/settings/language", json={"language": "fr"})
    set_language("en")  # ce que verrait un processus tout juste relancé

    assert await load_language() == "fr"


async def test_unknown_language_is_refused(admin: ApiClient) -> None:
    response = await admin.put("/api/v1/settings/language", json={"language": "de"})

    assert response.status_code == 422
    assert response.json()["remediation"]


async def test_only_administrators_change_the_language(viewer: ApiClient) -> None:
    response = await viewer.put("/api/v1/settings/language", json={"language": "fr"})

    assert response.status_code == 403
