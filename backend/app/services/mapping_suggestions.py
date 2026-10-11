"""Suggest a column -> standard field mapping from the detected header names.

Pure and deterministic. Scoring per (column, field) pair:

* 1.0 when the normalized header equals the field's normalized label or one of its synonyms
  (whitespace-insensitive, so "PartNumber" equals "Part Number");
* otherwise the best `difflib.SequenceMatcher` ratio against the label and synonyms, kept only
  if it is at least `FUZZY_THRESHOLD`.

Each column is only ever offered its own best-scoring field (ties: field order). Columns are
assigned greedily by descending score (ties: lower column index); a column whose best field was
already taken stays unmapped, so each column and each field is used at most once.
"""

import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from difflib import SequenceMatcher

from app.domain.bom_fields import STANDARD_FIELDS, BomField
from app.services.parsing.header import ColumnInfo

FUZZY_THRESHOLD = 0.8

# Words that all mean "number" in a header ("Part #", "Item No.", "Part Num", "Ref Nbr").
_NUMBER_WORDS = frozenset({"#", "no", "num", "nbr", "number"})
# Separators become spaces; every other non-alphanumeric character is dropped ("P/N" -> "pn").
_SEPARATORS = re.compile(r"[\s_\-]+")
_DROPPED = re.compile(r"[^a-z0-9# ]")


@dataclass(frozen=True, slots=True)
class ColumnSuggestion:
    source_column_index: int
    source_column_name: str
    target_field: str | None
    confidence: float


@dataclass(frozen=True, slots=True)
class MappingSuggestion:
    columns: list[ColumnSuggestion]
    missing_required: list[str]


def normalize_header(text: str) -> str:
    """Canonical form of a header: lowercase words separated by single spaces."""
    value = unicodedata.normalize("NFKC", text).casefold()
    value = value.replace("&", " and ").replace("#", " # ")
    value = _SEPARATORS.sub(" ", value)
    value = _DROPPED.sub("", value)
    words = ["number" if word in _NUMBER_WORDS else word for word in value.split()]
    return " ".join(words)


@dataclass(frozen=True, slots=True)
class _FieldTerms:
    field: BomField
    terms: tuple[str, ...]
    compact_terms: frozenset[str]


def _field_terms(field: BomField) -> _FieldTerms:
    normalized = (normalize_header(field.label), *map(normalize_header, field.synonyms))
    terms = tuple(dict.fromkeys(term for term in normalized if term))
    return _FieldTerms(field, terms, frozenset(term.replace(" ", "") for term in terms))


_FIELD_TERMS: tuple[_FieldTerms, ...] = tuple(_field_terms(field) for field in STANDARD_FIELDS)


def _score(header: str, terms: _FieldTerms) -> float:
    if header.replace(" ", "") in terms.compact_terms:
        return 1.0
    return max(SequenceMatcher(None, header, term).ratio() for term in terms.terms)


def suggest_mapping(columns: Sequence[ColumnInfo]) -> MappingSuggestion:
    """Map each column to at most one standard field, and each field to at most one column."""
    # Each column competes only for its own best target (ties: field order); there is no
    # fallback to a second-best target when a higher-ranked column has already taken it.
    candidates: list[tuple[float, int, int]] = []  # (score, column position, field position)
    for column_position, column in enumerate(columns):
        header = normalize_header(column.name)
        if not header:
            continue
        best_score, best_field = max(
            (_score(header, terms), -field_position)
            for field_position, terms in enumerate(_FIELD_TERMS)
        )
        if best_score >= FUZZY_THRESHOLD:
            candidates.append((best_score, column_position, -best_field))

    candidates.sort(key=lambda c: (-c[0], columns[c[1]].index, c[2]))
    assigned: dict[int, tuple[int, float]] = {}  # column position -> (field position, score)
    used_fields: set[int] = set()
    for score, column_position, field_position in candidates:
        if column_position in assigned or field_position in used_fields:
            continue
        assigned[column_position] = (field_position, score)
        used_fields.add(field_position)

    suggestions: list[ColumnSuggestion] = []
    for column_position, column in enumerate(columns):
        match = assigned.get(column_position)
        suggestions.append(
            ColumnSuggestion(
                source_column_index=column.index,
                source_column_name=column.name,
                target_field=STANDARD_FIELDS[match[0]].key if match else None,
                confidence=round(match[1], 2) if match else 0.0,
            )
        )

    missing = [
        field.key
        for position, field in enumerate(STANDARD_FIELDS)
        if field.required and position not in used_fields
    ]
    return MappingSuggestion(columns=suggestions, missing_required=missing)
