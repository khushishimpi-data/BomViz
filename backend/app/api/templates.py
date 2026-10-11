"""Blank BOM templates (CSV or XLSX) whose header row is the standard field labels.

Any authenticated user may download a template; it holds no workspace data. The format is a
closed set validated by the path parameter, so anything else is a 422.
"""

from http import HTTPStatus
from typing import Literal

from fastapi import APIRouter
from starlette.responses import Response

from app.core.dependencies import CurrentUserDep
from app.schemas.common import ErrorResponse
from app.services.template_service import build_csv_template, build_xlsx_template

router = APIRouter(tags=["templates"])

TemplateFormat = Literal["csv", "xlsx"]

CSV_MEDIA_TYPE = "text/csv; charset=utf-8"
XLSX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

_MEDIA_TYPES: dict[str, str] = {"csv": CSV_MEDIA_TYPE, "xlsx": XLSX_MEDIA_TYPE}


@router.get(
    "/templates/{template_format}",
    response_class=Response,
    responses={
        HTTPStatus.OK: {
            "description": "The template file as an attachment.",
            "content": {CSV_MEDIA_TYPE: {}, XLSX_MEDIA_TYPE: {}},
        },
        HTTPStatus.UNAUTHORIZED: {
            "model": ErrorResponse,
            "description": "Not authenticated, or the access token was rejected.",
        },
        HTTPStatus.UNPROCESSABLE_ENTITY: {
            "model": ErrorResponse,
            "description": "The template format is not csv or xlsx.",
        },
    },
)
async def download_template(template_format: TemplateFormat, _: CurrentUserDep) -> Response:
    """Download a blank BOM template with the standard field labels as headers, required first."""
    content = build_csv_template() if template_format == "csv" else build_xlsx_template()
    return Response(
        content=content,
        media_type=_MEDIA_TYPES[template_format],
        headers={
            "Content-Disposition": f'attachment; filename="bomviz-template.{template_format}"',
            "Cache-Control": "no-store",
        },
    )
