from __future__ import annotations

import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PYTEST_DATA_ROOT = (PROJECT_ROOT / ".artifacts" / "pytest-bootstrap-data").resolve()
DEFAULT_DATABASE = PROJECT_ROOT / "data" / "coread.sqlite3"


def _database_fingerprint(path: Path) -> tuple[int, int] | None:
    if not path.exists():
        return None
    stat = path.stat()
    return stat.st_size, stat.st_mtime_ns


DEFAULT_DATABASE_BEFORE_TESTS = _database_fingerprint(DEFAULT_DATABASE)
os.environ["CAELIAE_READ_DATA_ROOT"] = str(PYTEST_DATA_ROOT)
os.environ.pop("COREAD_DATA_ROOT", None)

from caeliae_read.api.app import create_app
from caeliae_read.application.service import CaeliaeReadService
from caeliae_read.config import Settings, get_settings


@pytest.fixture(scope="session", autouse=True)
def guard_pytest_data_isolation() -> None:
    assert get_settings().data_root == PYTEST_DATA_ROOT
    yield
    assert get_settings().data_root == PYTEST_DATA_ROOT
    assert _database_fingerprint(DEFAULT_DATABASE) == DEFAULT_DATABASE_BEFORE_TESTS


@pytest.fixture
def fixture_pdf() -> Path:
    return Path(__file__).parent / "fixtures" / "selectable-paper.pdf"


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    data_root = tmp_path / "data"
    return Settings(
        data_root=data_root,
        database_path=data_root / "coread.sqlite3",
        papers_root=data_root / "papers",
        temp_root=data_root / "tmp",
        max_pdf_bytes=1_000_000,
        max_page_text_chars=4_000,
    )


@pytest.fixture
def service(settings: Settings) -> CaeliaeReadService:
    return CaeliaeReadService(settings)


@pytest.fixture
def client(service: CaeliaeReadService) -> TestClient:
    with TestClient(create_app(service)) as test_client:
        yield test_client


@pytest.fixture
def uploaded(client: TestClient, fixture_pdf: Path) -> dict[str, object]:
    with fixture_pdf.open("rb") as source:
        response = client.post(
            "/api/papers",
            files={"file": ("research-paper.pdf", source, "application/pdf")},
        )
    assert response.status_code == 201
    return response.json()["paper"]


@pytest.fixture
def session(client: TestClient, uploaded: dict[str, object]) -> dict[str, str]:
    response = client.post(f"/api/papers/{uploaded['paper_id']}/sessions")
    assert response.status_code == 201
    return response.json()


def state_payload(
    session: dict[str, str],
    *,
    event_id: str,
    revision: int = 1,
    page_number: int = 2,
    with_selection: bool = True,
) -> dict[str, object]:
    selection = None
    if with_selection:
        selection = {
            "paper_id": session["paper_id"],
            "session_id": session["session_id"],
            "page_number": page_number,
            "exact_text": "Selected passage",
            "prefix": "Caeliae Read acceptance page two ",
            "suffix": " for cross-client verification.",
            "normalized_quads": [
                {"x": 0.10, "y": 0.12, "width": 0.22, "height": 0.03}
            ],
            "page_width": 612,
            "page_height": 792,
            "rotation": 0,
            "revision": revision,
        }
    return {
        "paper_id": session["paper_id"],
        "session_id": session["session_id"],
        "client_event_id": event_id,
        "page_number": page_number,
        "page_text": "Caeliae Read acceptance page two Selected passage for cross-client verification.",
        "revision": revision,
        "selection": selection,
    }
