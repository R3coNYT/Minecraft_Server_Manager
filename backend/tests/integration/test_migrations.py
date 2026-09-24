"""Les migrations sur une base **remplie**, comme en production.

Une migration qui recrée une table (SQLite n'en modifie pas une en place) la
supprime au passage : avec les clés étrangères actives, ce ``DROP TABLE`` effaçait
en cascade tout ce qui en dépendait — sessions, réglages des serveurs, joueurs,
sauvegardes — ou échouait sur une contrainte. Sur une base vide, rien ne se voit :
d'où ce test, qui remplit **chaque table** avant de migrer.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config

from msm.config import get_settings

REPO = Path(__file__).resolve().parents[3]
#: Dernière version avant l'ouverture au public : celle des installations existantes.
BASELINE = "f414e4b69a05"
#: Tables que les migrations suppriment volontairement.
DROPPED = {"server_permissions"}


@pytest.fixture
def migrations(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[Path, Config]]:
    database = tmp_path / "msm.db"
    monkeypatch.setenv("MSM_DATABASE_URL", f"sqlite+aiosqlite:///{database.as_posix()}")
    monkeypatch.setenv("MSM_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("MSM_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("MSM_SECRET_KEY", "k" * 64)
    get_settings.cache_clear()
    # Pas de fichier alembic.ini : sa configuration de journalisation écraserait
    # celle des tests.
    config = Config()
    config.set_main_option("script_location", str(REPO / "migrations"))
    yield database, config
    get_settings.cache_clear()


_OVERRIDES: dict[str, dict[str, object]] = {
    "users": {"username": "flavien", "role": "ADMIN", "is_active": 1},
}


def _value(table: str, column: str, declared: str) -> object:
    kind = declared.upper()
    if "INT" in kind:
        return 1
    if "BOOL" in kind:
        return 0
    if "FLOAT" in kind or "REAL" in kind or "NUMERIC" in kind:
        return 1.0
    if "DATE" in kind or "TIME" in kind:
        return "2026-09-24 10:00:00.000000"
    if "JSON" in kind:
        return "{}"
    return f"{table}-{column}"


def _fill(database: Path) -> dict[str, int]:
    """Une ligne dans chaque table, références comprises. Renvoie les comptes."""
    connection = sqlite3.connect(database)
    connection.execute("PRAGMA foreign_keys=ON")
    tables = [
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%' AND name != 'alembic_version'"
        )
    ]
    pending = sorted(tables, key=lambda name: (name != "users", name != "servers", name))
    # Plusieurs passes : une table n'est remplie qu'une fois ses parents remplis.
    while pending:
        progressed = False
        for table in list(pending):
            columns = connection.execute(f"PRAGMA table_info('{table}')").fetchall()
            row: dict[str, object] = {}
            for _cid, name, declared, notnull, _default, pk in columns:
                if pk and "INT" in declared.upper():
                    continue
                if notnull or pk:
                    row[name] = _value(table, name, declared)
            row.update(_OVERRIDES.get(table, {}))
            names = ", ".join(row)
            marks = ", ".join("?" for _ in row)
            try:
                connection.execute(
                    f"INSERT INTO {table} ({names}) VALUES ({marks})", list(row.values())
                )
            except sqlite3.IntegrityError:
                continue
            pending.remove(table)
            progressed = True
        assert progressed, f"Tables impossibles à remplir : {pending}"
    connection.commit()
    counts = {
        table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] for table in tables
    }
    connection.close()
    return counts


def test_upgrading_a_populated_database_keeps_every_row(
    migrations: tuple[Path, Config],
) -> None:
    database, config = migrations
    command.upgrade(config, BASELINE)
    before = _fill(database)
    assert all(count == 1 for count in before.values()), before

    command.upgrade(config, "head")

    connection = sqlite3.connect(database)
    after = {
        table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        for table in before
        if table not in DROPPED
    }
    violations = connection.execute("PRAGMA foreign_key_check").fetchall()
    owner = connection.execute("SELECT owner_id FROM servers").fetchone()[0]
    connection.close()

    assert after == {table: count for table, count in before.items() if table not in DROPPED}
    assert violations == []
    assert owner == 1


def test_migrating_back_and_forth_keeps_every_row(migrations: tuple[Path, Config]) -> None:
    database, config = migrations
    command.upgrade(config, BASELINE)
    before = _fill(database)

    command.upgrade(config, "head")
    command.downgrade(config, BASELINE)
    command.upgrade(config, "head")

    connection = sqlite3.connect(database)
    for table, count in before.items():
        if table in DROPPED:
            continue
        assert connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == count, table
    connection.close()
