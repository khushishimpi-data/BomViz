"""BOM template downloads over HTTP: authentication, response headers, and that each template
parses back with the upload parser into exactly the standard labels, which the mapping
suggestions map one-to-one with full confidence."""

from pathlib import Path

import pytest
from httpx import AsyncClient

from app.core.config import Settings
from app.domain.bom_fields import STANDARD_FIELDS
from app.services.mapping_suggestions import suggest_mapping
from app.services.parsing.parse import ParseOutcome, parse_file
from tests.conftest import make_settings

pytestmark = pytest.mark.integration

API = "/api/v1"
TEMPLATES = f"{API}/templates"
PASSWORD = "a very long passphrase"
SECRET = "test-secret-key-that-is-long-enough-for-hs256-0123456789"
XLSX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
LABELS = [field.label for field in STANDARD_FIELDS]


@pytest.fixture
def db_settings(migrated_database_url: str, truncate_tables: None) -> Settings:
    """The shared `db_settings` plus a signing key, so the test user can log in."""
    return make_settings(
        database_url=migrated_database_url, jwt_secret_key=SECRET, rate_limit_enabled=False
    )


async def _auth_headers(client: AsyncClient) -> dict[str, str]:
    email = "engineer@example.com"
    registered = await client.post(
        f"{API}/auth/register",
        json={"email": email, "password": PASSWORD, "full_name": "Test User"},
    )
    assert registered.status_code == 201, registered.text
    login = await client.post(f"{API}/auth/login", json={"email": email, "password": PASSWORD})
    assert login.status_code == 200, login.text
    client.cookies.clear()
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


async def _download_and_parse(
    client: AsyncClient, template_format: str, tmp_path: Path
) -> ParseOutcome:
    response = await client.get(
        f"{TEMPLATES}/{template_format}", headers=await _auth_headers(client)
    )
    assert response.status_code == 200, response.text
    path = tmp_path / f"template.{template_format}"
    path.write_bytes(response.content)
    return parse_file(path, template_format)


@pytest.mark.parametrize("template_format", ["csv", "xlsx"])
async def test_template_requires_authentication(
    db_client: AsyncClient, template_format: str
) -> None:
    response = await db_client.get(f"{TEMPLATES}/{template_format}")

    assert response.status_code == 401
    assert response.json()["error"]["code"]


async def test_csv_template_is_a_utf8_csv_attachment(db_client: AsyncClient) -> None:
    response = await db_client.get(f"{TEMPLATES}/csv", headers=await _auth_headers(db_client))

    assert response.status_code == 200
    assert response.headers["content-type"] == "text/csv; charset=utf-8"
    assert response.headers["content-disposition"] == 'attachment; filename="bomviz-template.csv"'
    assert response.headers["cache-control"] == "no-store"
    assert response.content == ("﻿" + ",".join(LABELS) + "\r\n").encode("utf-8")


async def test_xlsx_template_is_an_xlsx_attachment(db_client: AsyncClient) -> None:
    response = await db_client.get(f"{TEMPLATES}/xlsx", headers=await _auth_headers(db_client))

    assert response.status_code == 200
    assert response.headers["content-type"] == XLSX_MEDIA_TYPE
    assert response.headers["content-disposition"] == 'attachment; filename="bomviz-template.xlsx"'
    assert response.headers["cache-control"] == "no-store"
    assert response.content.startswith(b"PK\x03\x04")


@pytest.mark.parametrize("template_format", ["csv", "xlsx"])
async def test_template_parses_back_to_the_labels_and_no_rows(
    db_client: AsyncClient, template_format: str, tmp_path: Path
) -> None:
    outcome = await _download_and_parse(db_client, template_format, tmp_path)

    assert [column.name for column in outcome.columns] == LABELS
    assert outcome.row_count == 0
    assert outcome.sample_rows == []


async def test_xlsx_template_parses_from_the_bom_sheet(
    db_client: AsyncClient, tmp_path: Path
) -> None:
    outcome = await _download_and_parse(db_client, "xlsx", tmp_path)

    assert outcome.effective_options.sheet_name == "BOM"
    assert [sheet.name for sheet in outcome.sheets] == ["BOM", "Instructions"]


@pytest.mark.parametrize("template_format", ["csv", "xlsx"])
async def test_template_columns_map_to_their_fields_with_full_confidence(
    db_client: AsyncClient, template_format: str, tmp_path: Path
) -> None:
    outcome = await _download_and_parse(db_client, template_format, tmp_path)

    suggestion = suggest_mapping(outcome.columns)

    assert [(c.target_field, c.confidence) for c in suggestion.columns] == [
        (field.key, 1.0) for field in STANDARD_FIELDS
    ]
    assert suggestion.missing_required == []


def test_template_lists_required_fields_first() -> None:
    required = [field.required for field in STANDARD_FIELDS]

    assert required == sorted(required, reverse=True)


async def test_unknown_template_format_is_rejected(db_client: AsyncClient) -> None:
    response = await db_client.get(f"{TEMPLATES}/xls", headers=await _auth_headers(db_client))

    assert response.status_code == 422
