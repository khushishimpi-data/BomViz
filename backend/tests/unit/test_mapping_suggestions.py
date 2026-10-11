from collections import Counter
from difflib import SequenceMatcher

import pytest

from app.domain.bom_fields import STANDARD_FIELDS
from app.services.mapping_suggestions import (
    ColumnSuggestion,
    MappingSuggestion,
    normalize_header,
    suggest_mapping,
)
from app.services.parsing.header import ColumnInfo


def _suggest(*names: str) -> MappingSuggestion:
    return suggest_mapping([ColumnInfo(index, name) for index, name in enumerate(names)])


def _targets(*names: str) -> list[str | None]:
    return [column.target_field for column in _suggest(*names).columns]


# --- standard fields -------------------------------------------------------------------------


def test_standard_fields_list_eighteen_fields_with_the_required_ones_first() -> None:
    keys = [field.key for field in STANDARD_FIELDS]

    assert len(keys) == 18
    assert len(set(keys)) == 18
    assert keys[:3] == ["part_number", "part_name", "quantity"]
    assert [field.key for field in STANDARD_FIELDS if field.required] == keys[:3]


def test_no_normalized_synonym_or_label_belongs_to_two_fields() -> None:
    owners: Counter[str] = Counter()
    for field in STANDARD_FIELDS:
        terms = {normalize_header(term).replace(" ", "") for term in (field.label, *field.synonyms)}
        owners.update(terms)

    assert [term for term, count in owners.items() if count > 1] == []


# --- normalize_header ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("  Part   Number ", "part number"),
        ("Part #", "part number"),
        ("Part#", "part number"),
        ("Item No.", "item number"),
        ("Part Num", "part number"),
        ("Ref Nbr", "ref number"),
        ("QTY.", "qty"),
        ("Mfr. P/N", "mfr pn"),
        ("R&D", "r and d"),
        ("part_number", "part number"),
        ("Lead-Time (Days)", "lead time days"),
        ("\uff31\uff34\uff39", "qty"),  # full-width "QTY" folds via NFKC
        ("", ""),
    ],
)
def test_normalize_header_produces_the_canonical_form(raw: str, expected: str) -> None:
    assert normalize_header(raw) == expected


def test_normalize_header_keeps_words_that_only_contain_no() -> None:
    assert normalize_header("Notes") == "notes"


# --- exact matches ---------------------------------------------------------------------------


def test_exact_synonyms_map_with_full_confidence() -> None:
    result = _suggest("PN", "Item Name", "Qty per", "UOM", "Vendor", "MPN")

    assert result.columns == [
        ColumnSuggestion(0, "PN", "part_number", 1.0),
        ColumnSuggestion(1, "Item Name", "part_name", 1.0),
        ColumnSuggestion(2, "Qty per", "quantity", 1.0),
        ColumnSuggestion(3, "UOM", "unit_of_measure", 1.0),
        ColumnSuggestion(4, "Vendor", "supplier_name", 1.0),
        ColumnSuggestion(5, "MPN", "manufacturer_part_number", 1.0),
    ]


@pytest.mark.parametrize(
    ("header", "field"),
    [
        ("Part #", "part_number"),
        ("PART NO.", "part_number"),
        ("QTY.", "quantity"),
        ("Mfr. P/N", "manufacturer_part_number"),
        ("Ref. Des.", "reference_designator"),
        ("Unit Price ($)", "unit_cost"),
        ("Lead Time (days)", "lead_time_days"),
        ("PartNumber", "part_number"),
        ("Description", "description"),
    ],
)
def test_punctuation_and_case_variants_match_exactly(header: str, field: str) -> None:
    [column] = _suggest(header).columns

    assert (column.target_field, column.confidence) == (field, 1.0)


def test_the_field_label_itself_is_an_exact_match() -> None:
    result = _suggest(*(field.label for field in STANDARD_FIELDS))

    assert [column.target_field for column in result.columns] == [f.key for f in STANDARD_FIELDS]
    assert {column.confidence for column in result.columns} == {1.0}


# --- fuzzy matches ---------------------------------------------------------------------------


def test_close_misspelling_maps_with_its_rounded_ratio() -> None:
    [column] = _suggest("Materal").columns

    expected = round(SequenceMatcher(None, "materal", "material").ratio(), 2)
    assert (column.target_field, column.confidence) == ("material", expected)
    assert 0.8 <= column.confidence < 1.0


def test_a_ratio_of_exactly_the_threshold_is_kept() -> None:
    # "designated" vs "designator": 8 shared characters over 20 -> 0.8.
    assert SequenceMatcher(None, "designated", "designator").ratio() == 0.8

    [column] = _suggest("Designated").columns

    assert (column.target_field, column.confidence) == ("reference_designator", 0.8)


def test_a_ratio_just_below_the_threshold_is_not_mapped() -> None:
    # Best candidate is "material" at 10/13 (~0.77).
    assert SequenceMatcher(None, "materialistic", "material").ratio() < 0.8

    [column] = _suggest("Materialistic").columns

    assert (column.target_field, column.confidence) == (None, 0.0)


def test_unrelated_headers_stay_unmapped() -> None:
    assert _targets("Colour", "Weight (kg)", "Status", "Revision", "Drawing") == [None] * 5


def test_blank_header_is_unmapped() -> None:
    assert _targets("", "   ") == [None, None]


# --- assignment ------------------------------------------------------------------------------


def test_each_target_is_used_by_at_most_one_column() -> None:
    assert _targets("Notes", "Remarks", "Note") == ["notes", None, None]


def test_each_column_maps_to_at_most_one_target() -> None:
    targets = _targets("Part Number", "Name", "Qty", "Supplier", "Mfr", "Notes")

    mapped = [target for target in targets if target is not None]
    assert len(mapped) == len(set(mapped)) == 6


def test_exact_match_beats_an_earlier_fuzzy_match_for_the_same_target() -> None:
    # "Qtys" is a fuzzy match for quantity, but the later exact "Qty" wins it.
    assert _targets("Qtys", "Qty") == [None, "quantity"]


def test_a_column_whose_best_target_is_taken_stays_unmapped() -> None:
    # "Vendor Name" is exact for supplier_name, which the lower-index "Vendor" takes first. Its
    # second-best candidate (fuzzy "vendor pn" -> supplier_part_number) is not used.
    result = _suggest("Vendor", "Vendor Name")

    assert result.columns == [
        ColumnSuggestion(0, "Vendor", "supplier_name", 1.0),
        ColumnSuggestion(1, "Vendor Name", None, 0.0),
    ]


def test_a_free_second_best_target_is_not_assigned_to_a_losing_column() -> None:
    # "Part No" scores 1.0 for part_number and >= 0.8 for manufacturer_part_number ("mfg part
    # number"). Once "Part #" takes part_number, manufacturer_part_number stays free and unmapped.
    assert SequenceMatcher(None, "part number", "mfg part number").ratio() >= 0.8

    result = _suggest("Part #", "Part No")

    assert [c.target_field for c in result.columns] == ["part_number", None]
    assert result.columns[1].confidence == 0.0


def test_equal_scores_go_to_the_lower_column_index() -> None:
    result = suggest_mapping([ColumnInfo(7, "Quantity"), ColumnInfo(2, "Qty")])

    assert [(c.source_column_index, c.target_field) for c in result.columns] == [
        (7, None),
        (2, "quantity"),
    ]


def test_output_preserves_column_order_and_source_names() -> None:
    result = suggest_mapping([ColumnInfo(3, "Notes"), ColumnInfo(1, "Colour")])

    assert [(c.source_column_index, c.source_column_name) for c in result.columns] == [
        (3, "Notes"),
        (1, "Colour"),
    ]


def test_suggestion_is_deterministic() -> None:
    names = ("Item #", "Part #", "Qtty", "Qtys", "Desc", "Descr", "Notes", "Remarks")

    assert _suggest(*names) == _suggest(*names)


# --- missing_required ------------------------------------------------------------------------


def test_missing_required_lists_unmapped_required_fields_in_field_order() -> None:
    assert _suggest("Qty", "Description").missing_required == ["part_number", "part_name"]


def test_missing_required_is_empty_when_all_required_fields_are_mapped() -> None:
    assert _suggest("Part #", "Part Name", "Qty", "Colour").missing_required == []


def test_no_columns_reports_every_required_field_missing() -> None:
    result = suggest_mapping([])

    assert result == MappingSuggestion(
        columns=[], missing_required=["part_number", "part_name", "quantity"]
    )
