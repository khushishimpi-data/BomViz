"""Request and response bodies for BOM uploads, their jobs and their raw preview.

The upload request is multipart, so its fields are declared as form parameters on the route;
the workspace and uploader never come from the client unchecked (the workspace id is resolved
against the caller's membership, the uploader is the authenticated user).
"""

import uuid
from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.models import FileFormat, JobStatus, JobType, UploadStatus

# Mirrors the parser's allowlists (`app.services.parsing.parse`); a test keeps them in sync.
Delimiter = Literal[",", ";", "\t", "|"]
Encoding = Literal["utf-8", "utf-8-sig", "utf-16", "cp1252", "latin-1", "iso-8859-15"]


class JobResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    job_type: JobType
    status: JobStatus
    attempts: int
    error_code: str | None
    error_message: str | None
    queued_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


class ParseOptionsResponse(BaseModel):
    """Effective options of the last successful parse."""

    sheet_name: str | None = None
    header_row: int | None = None
    delimiter: str | None = None
    encoding: str | None = None


class BomSummaryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    workspace_id: uuid.UUID
    bom_name: str
    revision_label: str | None
    original_filename: str
    file_format: FileFormat
    size_bytes: int
    status: UploadStatus
    row_count: int | None
    column_count: int | None
    product_id: uuid.UUID | None
    product_revision_id: uuid.UUID | None
    uploaded_by_id: uuid.UUID | None
    uploaded_at: datetime
    latest_job: JobResponse | None


class BomDetailResponse(BomSummaryResponse):
    """An upload with its effective parse options and its last 10 jobs, newest first."""

    parse_options: ParseOptionsResponse | None
    jobs: list[JobResponse]


class FileUrlResponse(BaseModel):
    """A short-lived URL that downloads the original file, and when it stops working."""

    url: str
    expires_at: datetime


class ParseRequest(BaseModel):
    """Explicit parse options; an omitted (or null) option is detected from the file."""

    model_config = ConfigDict(extra="forbid")

    sheet_name: Annotated[str, Field(min_length=1, max_length=255)] | None = None
    header_row: Annotated[int, Field(ge=1, le=1000)] | None = None
    delimiter: Delimiter | None = None
    encoding: Encoding | None = None


class SheetResponse(BaseModel):
    name: str
    visible: bool


class ColumnResponse(BaseModel):
    """A detected column: 0-based position in the source and its display name."""

    index: int
    name: str


class PreviewRowResponse(BaseModel):
    """A sample data row: 1-based source row number and one text per column."""

    row_number: int
    cells: list[str]


class ColumnSuggestionResponse(BaseModel):
    source_column_index: int
    source_column_name: str
    target_field: str | None
    confidence: float


class TargetFieldResponse(BaseModel):
    """A standard BOM field a column can be mapped to."""

    key: str
    label: str
    required: bool


class PreviewResponse(BaseModel):
    """The raw preview stored by the last successful parse, with the suggested mapping."""

    status: UploadStatus
    parse_options: ParseOptionsResponse | None
    sheets: list[SheetResponse]
    columns: list[ColumnResponse]
    row_count: int
    column_count: int
    skipped_empty_rows: int
    # Formula cells without a saved value (they read as empty); 0 for snapshots taken before
    # the count existed.
    formula_cells_without_value: int = 0
    rows: list[PreviewRowResponse]
    suggested_mapping: list[ColumnSuggestionResponse]
    missing_required: list[str]
    target_fields: list[TargetFieldResponse]
