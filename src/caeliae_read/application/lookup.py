from __future__ import annotations

from functools import lru_cache
import unicodedata
from typing import Any, Protocol

from caeliae_read.api.schemas import LookupCandidate, LookupDefinition, LookupSource, VocabularyLookupOutput


class LookupProvider(Protocol):
    """Provider boundary for read-only vocabulary lookup."""

    def lookup(
        self,
        text: str,
        *,
        locale: str,
        target_locale: str | None = None,
    ) -> VocabularyLookupOutput:
        ...


_POS_ORDER = ("noun", "verb", "adjective", "adverb")
_WORDNET_POS = (("n", "noun"), ("v", "verb"), ("a", "adjective"), ("r", "adverb"))
_POS_RANK = {value: index for index, value in enumerate(_POS_ORDER)}


def normalize_lookup_text(text: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", text.strip()).split()).casefold()


def _entry_type(normalized_query: str) -> str:
    return "phrase" if " " in normalized_query else "word"


def _unavailable_result(
    text: str,
    normalized_query: str,
    entry_type: str,
    message: str,
) -> VocabularyLookupOutput:
    return VocabularyLookupOutput(
        query=text.strip(),
        normalized_query=normalized_query,
        entry_type=entry_type,
        status="unavailable",
        message=message,
    )


class LocalWordNetProvider:
    """Read-only adapter for an explicitly installed local NLTK WordNet corpus.

    The import and corpus access are deliberately lazy. This module never calls
    nltk.download() and therefore cannot silently fetch data during a lookup.
    """

    provider_id = "wordnet"

    def __init__(self, corpus: Any | None = None) -> None:
        self._corpus = corpus

    def _load_corpus(self) -> Any:
        if self._corpus is None:
            from nltk.corpus import wordnet

            self._corpus = wordnet
        return self._corpus

    @staticmethod
    def _version(corpus: Any) -> str | None:
        get_version = getattr(corpus, "get_version", None)
        if not callable(get_version):
            return None
        try:
            return str(get_version())
        except Exception:
            return None

    @staticmethod
    def _lemma_name(synset: Any, fallback: str) -> str:
        lemmas = synset.lemmas()
        if lemmas:
            name = str(lemmas[0].name()).replace("_", " ").strip()
            if name:
                return name
        return fallback.replace("_", " ").strip()

    def lookup(
        self,
        text: str,
        *,
        locale: str,
        target_locale: str | None = None,
    ) -> VocabularyLookupOutput:
        del target_locale
        query = text.strip()
        normalized_query = normalize_lookup_text(query)
        entry_type = _entry_type(normalized_query)
        if locale.casefold() not in {"en", "en-us", "en-gb"}:
            return _unavailable_result(
                query,
                normalized_query,
                entry_type,
                "The local WordNet provider currently supports English locale only.",
            )

        try:
            corpus = self._load_corpus()
            version = self._version(corpus)
            source = LookupSource(kind="dictionary", provider=self.provider_id, version=version)
            aggregates: dict[tuple[str, str], dict[str, set[str]]] = {}
            lookup_key = normalized_query.replace(" ", "_")
            for wordnet_pos, pos_name in _WORDNET_POS:
                forms = [lookup_key]
                morphy = getattr(corpus, "morphy", None)
                if callable(morphy):
                    base_form = morphy(lookup_key, pos=wordnet_pos)
                    if base_form and base_form not in forms:
                        forms.append(str(base_form))
                for form in forms:
                    for synset in corpus.synsets(form, pos=wordnet_pos):
                        synset_pos = synset.pos() if callable(getattr(synset, "pos", None)) else wordnet_pos
                        resolved_pos = dict(_WORDNET_POS).get(synset_pos, pos_name)
                        lemma = self._lemma_name(synset, form)
                        key = (lemma.casefold(), resolved_pos)
                        aggregate = aggregates.setdefault(key, {"definitions": set(), "variants": set()})
                        definition = str(synset.definition()).strip()
                        if definition:
                            aggregate["definitions"].add(definition)
                        if normalize_lookup_text(lemma) != normalized_query:
                            aggregate["variants"].add(query)

            candidates: list[LookupCandidate] = []
            for (lemma_key, pos_name), aggregate in sorted(
                aggregates.items(),
                key=lambda item: (_POS_RANK.get(item[0][1], len(_POS_ORDER)), item[0][0]),
            ):
                lemma = lemma_key
                candidates.append(
                    LookupCandidate(
                        lemma=lemma,
                        part_of_speech=pos_name,
                        definitions=[
                            LookupDefinition(text=definition, source=source)
                            for definition in sorted(aggregate["definitions"], key=str.casefold)[:3]
                        ],
                        variants=sorted(aggregate["variants"], key=str.casefold),
                    )
                )
                if len(candidates) >= 20:
                    break
        except (ImportError, LookupError):
            return _unavailable_result(
                query,
                normalized_query,
                entry_type,
                "Local WordNet is unavailable. Install the optional lookup dependency and an explicit local WordNet corpus; no automatic download is performed.",
            )
        except Exception:
            return _unavailable_result(
                query,
                normalized_query,
                entry_type,
                "The local WordNet provider is unavailable.",
            )

        if not candidates:
            return VocabularyLookupOutput(
                query=query,
                normalized_query=normalized_query,
                entry_type=entry_type,
                status="unsupported_phrase" if entry_type == "phrase" else "no_result",
            )
        return VocabularyLookupOutput(
            query=query,
            normalized_query=normalized_query,
            entry_type=entry_type,
            status="ok",
            candidates=candidates,
        )


class VocabularyLookupService:
    """Application boundary that converts provider failures to safe read results."""

    def __init__(self, provider: LookupProvider) -> None:
        self.provider = provider

    def lookup(self, text: str, *, locale: str, target_locale: str | None = None) -> VocabularyLookupOutput:
        normalized_query = normalize_lookup_text(text)
        entry_type = _entry_type(normalized_query)
        try:
            return self.provider.lookup(text, locale=locale, target_locale=target_locale)
        except Exception:
            return _unavailable_result(
                text,
                normalized_query,
                entry_type,
                "The local vocabulary lookup provider is unavailable.",
            )


@lru_cache(maxsize=1)
def get_lookup_provider() -> LookupProvider:
    return LocalWordNetProvider()
