from __future__ import annotations

import sqlite3
from typing import Any

import pytest
from fastapi.testclient import TestClient

from caeliae_read.api.app import create_app
from caeliae_read.application.lookup import LocalWordNetProvider
from caeliae_read.application.service import CaeliaeReadService


class FakeLemma:
    def __init__(self, value: str) -> None:
        self._value = value

    def name(self) -> str:
        return self._value


class FakeSynset:
    def __init__(self, pos: str, definition: str, lemma: str) -> None:
        self._pos = pos
        self._definition = definition
        self._lemma = lemma

    def pos(self) -> str:
        return self._pos

    def definition(self) -> str:
        return self._definition

    def lemmas(self) -> list[FakeLemma]:
        return [FakeLemma(self._lemma)]


class FakeWordNet:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self._synsets: dict[tuple[str, str], list[FakeSynset]] = {
            ("traces", "n"): [FakeSynset("n", "a mark left by something", "trace")],
            ("trace", "n"): [FakeSynset("n", "a mark left by something", "trace")],
            ("traces", "v"): [FakeSynset("v", "to follow or discover by signs", "trace")],
            ("trace", "v"): [FakeSynset("v", "to follow or discover by signs", "trace")],
            ("paper", "n"): [FakeSynset("n", "a material made from cellulose pulp", "paper")],
        }

    def get_version(self) -> str:
        return "3.0-fixture"

    def morphy(self, word: str, *, pos: str) -> str | None:
        if word == "traces" and pos in {"n", "v"}:
            return "trace"
        return None

    def synsets(self, word: str, *, pos: str) -> list[FakeSynset]:
        self.calls.append((word, pos))
        return self._synsets.get((word, pos), [])


class UnavailableWordNet:
    def synsets(self, word: str, *, pos: str) -> list[FakeSynset]:
        raise LookupError("fixture corpus is unavailable")


class FailingProvider:
    def lookup(self, text: str, *, locale: str, target_locale: str | None = None) -> Any:
        raise RuntimeError("provider fixture failure")


@pytest.fixture
def wordnet_provider() -> LocalWordNetProvider:
    return LocalWordNetProvider(FakeWordNet())


@pytest.fixture
def lookup_client(service: CaeliaeReadService, wordnet_provider: LocalWordNetProvider) -> TestClient:
    with TestClient(create_app(service, lookup_provider=wordnet_provider)) as client:
        yield client


def test_wordnet_lookup_returns_multiple_lemma_pos_candidates_and_provenance(lookup_client: TestClient) -> None:
    response = lookup_client.post("/api/vocabulary/lookup", json={"text": "traces", "locale": "en"})

    assert response.status_code == 200
    body = response.json()
    assert body["query"] == "traces"
    assert body["normalized_query"] == "traces"
    assert body["entry_type"] == "word"
    assert body["status"] == "ok"
    assert {(item["lemma"], item["part_of_speech"]) for item in body["candidates"]} == {
        ("trace", "noun"),
        ("trace", "verb"),
    }
    assert body["translations"] == []
    assert body["candidates"][0]["definitions"][0]["source"] == {
        "kind": "dictionary",
        "provider": "wordnet",
        "version": "3.0-fixture",
    }


def test_wordnet_lookup_handles_straightforward_word_and_unknown_academic_token(
    lookup_client: TestClient,
) -> None:
    known = lookup_client.post("/api/vocabulary/lookup", json={"text": "paper"})
    unknown = lookup_client.post("/api/vocabulary/lookup", json={"text": "xylophonic_quasientity"})

    assert known.status_code == 200
    assert known.json()["status"] == "ok"
    assert unknown.status_code == 200
    assert unknown.json()["status"] == "no_result"
    assert unknown.json()["candidates"] == []


def test_phrase_lookup_uses_full_query_without_split_and_compose(
    lookup_client: TestClient,
    wordnet_provider: LocalWordNetProvider,
) -> None:
    response = lookup_client.post("/api/vocabulary/lookup", json={"text": "in terms of"})

    assert response.status_code == 200
    assert response.json()["entry_type"] == "phrase"
    assert response.json()["status"] == "unsupported_phrase"
    assert {word for word, _ in wordnet_provider._corpus.calls} == {"in_terms_of"}


def test_missing_wordnet_returns_unavailable_without_query_in_message(
    service: CaeliaeReadService,
) -> None:
    provider = LocalWordNetProvider(UnavailableWordNet())
    with TestClient(create_app(service, lookup_provider=provider)) as client:
        response = client.post("/api/vocabulary/lookup", json={"text": "private phrase"})

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "unavailable"
    assert "private phrase" not in body["message"]
    assert "automatic download" in body["message"]


def test_lookup_rejects_blank_and_oversized_input(lookup_client: TestClient) -> None:
    assert lookup_client.post("/api/vocabulary/lookup", json={"text": "   "}).status_code == 422
    assert lookup_client.post("/api/vocabulary/lookup", json={"text": "x" * 201}).status_code == 422


def test_lookup_is_deterministic_and_does_not_write_vocabulary_or_review_rows(
    lookup_client: TestClient,
    service: CaeliaeReadService,
) -> None:
    def counts() -> tuple[int, int, int, int]:
        with sqlite3.connect(service.settings.database_path) as connection:
            return tuple(
                connection.execute(
                    "SELECT "
                    "(SELECT COUNT(*) FROM vocabulary_entries), "
                    "(SELECT COUNT(*) FROM vocabulary_occurrences), "
                    "(SELECT COUNT(*) FROM vocabulary_occurrence_events), "
                    "(SELECT COUNT(*) FROM vocabulary_review_results)"
                ).fetchone()
            )  # type: ignore[return-value]

    before = counts()
    first = lookup_client.post("/api/vocabulary/lookup", json={"text": "traces"}).json()
    second = lookup_client.post("/api/vocabulary/lookup", json={"text": "traces"}).json()

    assert first == second
    assert counts() == before


def test_provider_failure_isolated_from_existing_vocabulary_api(service: CaeliaeReadService) -> None:
    with TestClient(create_app(service, lookup_provider=FailingProvider())) as client:
        lookup = client.post("/api/vocabulary/lookup", json={"text": "trace"})
        created = client.post(
            "/api/vocabulary",
            json={"term": "trace", "examples": [], "tags": [], "idempotency_key": "lookup-safe-key"},
        )

    assert lookup.status_code == 200
    assert lookup.json()["status"] == "unavailable"
    assert created.status_code == 201


def test_lookup_openapi_contract_is_public(lookup_client: TestClient) -> None:
    schema = lookup_client.get("/openapi.json").json()

    assert "/api/vocabulary/lookup" in schema["paths"]
    assert schema["paths"]["/api/vocabulary/lookup"]["post"]["responses"]["200"]
    components = schema["components"]["schemas"]
    assert "VocabularyLookupInput" in components
    assert "VocabularyLookupOutput" in components
