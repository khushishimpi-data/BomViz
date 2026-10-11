"""Generate sample-bom.xlsx for the BOM upload e2e spec.

Run from the backend project (openpyxl is a backend dependency):
    cd backend && uv run --frozen python ../frontend/e2e/fixtures/make_sample_xlsx.py
"""

from pathlib import Path

from openpyxl import Workbook

ROWS = [
    ["Part Number", "Part Name", "Qty", "Parent Part Number", "Unit Cost"],
    ["ASM-200", "Pump Assembly", 1, None, 210.0],
    ["SUB-210", "Impeller Subassembly", 1, "ASM-200", 64.5],
    ["PRT-211", "Impeller", 1, "SUB-210", 22.0],
    ["PRT-212", "Shaft Seal", 2, "SUB-210", 4.8],
    ["PRT-220", "Pump Casing", 1, "ASM-200", 55.0],
]


def main() -> None:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "BOM"
    for row in ROWS:
        sheet.append(row)
    target = Path(__file__).with_name("sample-bom.xlsx")
    workbook.save(target)
    print(f"wrote {target} ({target.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
