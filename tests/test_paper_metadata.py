from __future__ import annotations

import sqlite3
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from caeliae_read.storage.migrations import MIGRATIONS, migrate
from caeliae_read.storage.repository import CaeliaeReadRepository


def _metadata_body() -> dict[str, object]:
    return {
        "title": "  A Useful Paper  ",
        "authors": [" Alice Smith ", "", "Bob Chen", "Alice Smith"],
        "publication_year": 2026,
        "venue": "  Example Conference  ",
        "doi": "HTTPS://DOI.ORG/10.1145/XXXX.XXXX",
        "arxiv_id": "https://arxiv.org/abs/2401.12345v2",
        "citation_key": " Smith2026Example ",
    }


def _bootstrap(database: Path, through: int) -> sqlite3.Connection:
    connection = sqlite3.connect(database)
    connection.execute(
        "CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
    )
    for version, sql in MIGRATIONS[:through]:
        connection.executescript(sql)
        connection.execute(
            "INSERT INTO schema_migrations(version, applied_at) VALUES (?, 'old')",
            (version,),
        )
    connection.commit()
    return connection


def _insert_paper(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        INSERT INTO papers(
            paper_id, content_sha256, original_filename, storage_key,
            size_bytes, mime_type, page_count, created_at
        ) VALUES ('paper', 'hash', 'paper.pdf', 'paper.pdf', 1,
                  'application/pdf', 1, 'old')
        """
    )


def test_fresh_migration_reaches_v12_with_metadata_schema(tmp_path: Path) -> None:
    repository = CaeliaeReadRepository(tmp_path / "fresh.sqlite3")
    with repository.connection() as connection:
        assert connection.execute(
            "SELECT max(version) FROM schema_migrations"
        ).fetchone()[0] == 12
        columns = {row[1] for row in connection.execute("PRAGMA table_info(paper_metadata)")}
        assert columns == {
            "paper_id", "title", "authors_json", "publication_year", "venue",
            "doi", "arxiv_id", "citation_key", "updated_at",
        }
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []


def test_v11_to_v12_is_additive_without_automatic_metadata_rows(tmp_path: Path) -> None:
    database = tmp_path / "v11.sqlite3"
    connection = _bootstrap(database, 11)
    _insert_paper(connection)
    connection.commit()

    migrate(connection)

    assert connection.execute(
        "SELECT max(version) FROM schema_migrations"
    ).fetchone()[0] == 12
    assert connection.execute(
        "SELECT count(*) FROM paper_metadata"
    ).fetchone()[0] == 0
    assert connection.execute(
        "SELECT paper_id, original_filename FROM papers"
    ).fetchone()[:2] == ("paper", "paper.pdf")
    assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    connection.close()


def test_metadata_foreign_key_cascades_on_paper_delete(tmp_path: Path) -> None:
    repository = CaeliaeReadRepository(tmp_path / "cascade.sqlite3")
    with repository.connection() as connection:
        _insert_paper(connection)
        connection.execute(
            "INSERT INTO paper_metadata(paper_id, updated_at) VALUES ('paper', 'now')"
        )
        connection.execute("DELETE FROM papers WHERE paper_id='paper'")
        connection.commit()
        assert connection.execute(
            "SELECT count(*) FROM paper_metadata"
        ).fetchone()[0] == 0
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []


def test_metadata_absence_and_full_replacement(client: TestClient, uploaded: dict[str, object]) -> None:
    paper_id = str(uploaded["paper_id"])
    absent = client.get(f"/api/papers/{paper_id}/metadata")
    assert absent.status_code == 200
    assert absent.json() == {"paper_id": paper_id, "metadata": None}

    created = client.put(f"/api/papers/{paper_id}/metadata", json=_metadata_body())
    assert created.status_code == 200
    metadata = created.json()["metadata"]
    assert metadata["title"] == "A Useful Paper"
    assert metadata["authors"] == ["Alice Smith", "Bob Chen", "Alice Smith"]
    assert metadata["venue"] == "Example Conference"
    assert metadata["doi"] == "10.1145/xxxx.xxxx"
    assert metadata["arxiv_id"] == "2401.12345"
    assert metadata["citation_key"] == "Smith2026Example"
    previous_updated_at = metadata["updated_at"]

    time.sleep(0.001)
    replacement = {field: None for field in _metadata_body()}
    replaced = client.put(f"/api/papers/{paper_id}/metadata", json=replacement)
    assert replaced.status_code == 200
    assert replaced.json()["metadata"]["updated_at"] != previous_updated_at
    assert all(
        replaced.json()["metadata"][field] is None
        for field in replacement
    )

    persisted = client.get(f"/api/papers/{paper_id}/metadata")
    assert persisted.status_code == 200
    assert persisted.json()["metadata"] is not None
    assert persisted.json()["metadata"]["title"] is None


def test_metadata_put_requires_exact_full_shape(client: TestClient, uploaded: dict[str, object]) -> None:
    paper_id = str(uploaded["paper_id"])
    body = _metadata_body()
    missing = dict(body)
    del missing["venue"]
    assert client.put(f"/api/papers/{paper_id}/metadata", json=missing).status_code == 422

    unknown = {**body, "unexpected": "value"}
    assert client.put(f"/api/papers/{paper_id}/metadata", json=unknown).status_code == 422


@pytest.mark.parametrize(
    "field,value",
    [
        ("publication_year", "2026"),
        ("publication_year", 2026.0),
        ("publication_year", True),
        ("publication_year", 999),
        ("publication_year", 2101),
        ("authors", ["ok", 3]),
        ("doi", "not-a-doi"),
        ("arxiv_id", "not-an-arxiv-id"),
        ("citation_key", "has space"),
        ("citation_key", "has/slash"),
        ("citation_key", ".leading-punctuation"),
    ],
)
def test_metadata_validation_returns_422(
    client: TestClient, uploaded: dict[str, object], field: str, value: object
) -> None:
    body = _metadata_body()
    body[field] = value
    assert client.put(
        f"/api/papers/{uploaded['paper_id']}/metadata", json=body
    ).status_code == 422


def test_metadata_validation_boundaries_and_limits(
    client: TestClient, uploaded: dict[str, object]
) -> None:
    paper_id = str(uploaded["paper_id"])
    for year in (1000, 2100):
        body = _metadata_body()
        body["publication_year"] = year
        assert client.put(f"/api/papers/{paper_id}/metadata", json=body).status_code == 200

    body = _metadata_body()
    body["authors"] = ["a"] * 129
    assert client.put(f"/api/papers/{paper_id}/metadata", json=body).status_code == 422
    body["authors"] = ["a" * 501]
    assert client.put(f"/api/papers/{paper_id}/metadata", json=body).status_code == 422
    body["citation_key"] = "a" * 129
    assert client.put(f"/api/papers/{paper_id}/metadata", json=body).status_code == 422


def test_metadata_missing_paper_returns_404(client: TestClient) -> None:
    body = _metadata_body()
    assert client.get("/api/papers/00000000-0000-0000-0000-000000000000/metadata").status_code == 404
    assert client.put("/api/papers/00000000-0000-0000-0000-000000000000/metadata", json=body).status_code == 404
