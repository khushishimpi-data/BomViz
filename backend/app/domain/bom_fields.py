"""The standard BOM fields an uploaded column can be mapped to.

Synonyms are matched after `normalize_header` (see `app.services.mapping_suggestions`), so they
may be written naturally ("Mfr. P/N", "Qty per"). A synonym must belong to exactly one field.
"""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class BomField:
    key: str
    label: str
    required: bool
    synonyms: tuple[str, ...]


STANDARD_FIELDS: tuple[BomField, ...] = (
    BomField(
        key="part_number",
        label="Part Number",
        required=True,
        synonyms=(
            "part number",
            "part no",
            "part #",
            "part num",
            "pn",
            "p/n",
            "part id",
            "part code",
            "item number",
            "item code",
            "component number",
            "sku",
        ),
    ),
    BomField(
        key="part_name",
        label="Part Name",
        required=True,
        synonyms=(
            "name",
            "part name",
            "item name",
            "component",
            "component name",
            "component / material",
            "component description",
            "short description",
            "part description short",
            "title",
        ),
    ),
    BomField(
        key="quantity",
        label="Quantity",
        required=True,
        synonyms=(
            "qty",
            "quantity",
            "qty per",
            "qty/assy",
            "qty per assembly",
            "quantity per",
            "quantity per assembly",
            "qty per finished unit",
            "qty per unit",
            "quantity per unit",
            "qty per parent",
            "usage per unit",
            "usage",
            "count",
        ),
    ),
    BomField(
        key="parent_part_number",
        label="Parent Part Number",
        required=False,
        synonyms=(
            "parent",
            "parent pn",
            "parent p/n",
            "parent part",
            "parent item",
            "assembly",
            "assembly number",
            "next higher assembly",
            "nha",
        ),
    ),
    BomField(
        key="bom_level",
        label="BOM Level",
        required=False,
        synonyms=("level", "lvl", "indent", "indent level", "find number", "hierarchy level"),
    ),
    BomField(
        key="unit_of_measure",
        label="Unit of Measure",
        required=False,
        synonyms=("uom", "unit", "units", "unit of measure", "unit of measurement"),
    ),
    BomField(
        key="description",
        label="Description",
        required=False,
        synonyms=(
            "description",
            "desc",
            "part description",
            "item description",
            "long description",
        ),
    ),
    BomField(
        key="unit_cost",
        label="Unit Cost",
        required=False,
        synonyms=("unit cost", "cost", "price", "unit price", "cost each", "price each"),
    ),
    BomField(
        key="total_cost",
        label="Total Cost",
        required=False,
        synonyms=(
            "total cost",
            "total price",
            "extended cost",
            "extended price",
            "ext cost",
            "ext price",
            "line total",
        ),
    ),
    BomField(
        key="currency",
        label="Currency",
        required=False,
        synonyms=("currency", "currency code", "ccy", "curr"),
    ),
    BomField(
        key="supplier_name",
        label="Supplier Name",
        required=False,
        synonyms=("supplier", "supplier name", "vendor", "vendor name"),
    ),
    BomField(
        key="supplier_part_number",
        label="Supplier Part Number",
        required=False,
        synonyms=(
            "supplier part number",
            "supplier pn",
            "supplier p/n",
            "vendor part number",
            "vendor pn",
            "vendor p/n",
        ),
    ),
    BomField(
        key="manufacturer_name",
        label="Manufacturer Name",
        required=False,
        synonyms=("manufacturer", "manufacturer name", "mfr", "mfg", "mfr name", "make"),
    ),
    BomField(
        key="manufacturer_part_number",
        label="Manufacturer Part Number",
        required=False,
        synonyms=(
            "manufacturer part number",
            "manufacturer pn",
            "mpn",
            "mfr pn",
            "mfr p/n",
            "mfr part number",
            "mfg pn",
            "mfg part number",
        ),
    ),
    BomField(
        key="material",
        label="Material",
        required=False,
        synonyms=("material", "material spec", "matl"),
    ),
    BomField(
        key="lead_time_days",
        label="Lead Time (Days)",
        required=False,
        synonyms=("lead time", "lead time days", "lt days", "leadtime"),
    ),
    BomField(
        key="reference_designator",
        label="Reference Designator",
        required=False,
        synonyms=("ref des", "refdes", "designator", "reference designator", "reference"),
    ),
    BomField(
        key="notes",
        label="Notes",
        required=False,
        synonyms=("notes", "note", "remarks", "remark", "comment", "comments"),
    ),
)
