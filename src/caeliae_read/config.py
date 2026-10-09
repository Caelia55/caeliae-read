from __future__ import annotations

from dataclasses import dataclass
import os
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class Settings:
    data_root: Path
    database_path: Path
    papers_root: Path
    temp_root: Path
    max_pdf_bytes: int = 25 * 1024 * 1024
    max_page_text_chars: int = 4_000
    selection_context_chars: int = 160
    api_host: str = "127.0.0.1"
    api_port: int = 8765
    allowed_origins: tuple[str, ...] = (
        "http://127.0.0.1:5174",
        "http://localhost:5174",
    )


_reported: set[str] = set()


def _report(message: str) -> None:
    if message not in _reported:
        print(message, file=sys.stderr)
        _reported.add(message)


def _environment(name: str, default: str) -> str:
    new = os.environ.get(f"CAELIAE_READ_{name}")
    old = os.environ.get(f"COREAD_{name}")
    if new is not None and old is not None:
        if name == "DATA_ROOT" and Path(new).resolve() != Path(old).resolve():
            raise ValueError("configuration conflict: CAELIAE_READ_DATA_ROOT and COREAD_DATA_ROOT resolve to different locations")
        _report(f"COREAD_{name} is deprecated and ignored; CAELIAE_READ_{name} is configured.")
    elif old is not None:
        _report(f"COREAD_{name} is deprecated; use CAELIAE_READ_{name} in the next migration version.")
    return new if new is not None else old if old is not None else default


def get_settings() -> Settings:
    data_root = Path(_environment("DATA_ROOT", str(PROJECT_ROOT / "data"))).resolve()
    origins = tuple(
        item.strip()
        for item in _environment(
            "ALLOWED_ORIGINS",
            "http://127.0.0.1:5174,http://localhost:5174",
        ).split(",")
        if item.strip()
    )
    return Settings(
        data_root=data_root,
        database_path=data_root / "coread.sqlite3",
        papers_root=data_root / "papers",
        temp_root=data_root / "tmp",
        max_pdf_bytes=int(_environment("MAX_PDF_BYTES", str(25 * 1024 * 1024))),
        max_page_text_chars=int(_environment("MAX_PAGE_TEXT_CHARS", "4000")),
        selection_context_chars=int(_environment("SELECTION_CONTEXT_CHARS", "160")),
        api_host=_environment("API_HOST", "127.0.0.1"),
        api_port=int(_environment("API_PORT", "8765")),
        allowed_origins=origins,
    )
