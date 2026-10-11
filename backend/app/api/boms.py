"""BOM uploads: upload, list, detail, original-file download, re-parse and raw preview.

Routes stay thin: resolve access, one `BomService` call, shape the response. A workspace is
resolved against the caller's membership (the `workspace_id` query parameter for the list, the
form field for an upload); a BOM through `require_bom_permission`, which reaches it only via the
caller's membership of its workspace. Either way, something the caller cannot see is a 404, and a
role lacking the permission is a 403.

The upload route runs on `UploadSizeLimitedRoute`, because FastAPI reads and spools the whole
multipart body before it resolves any dependency. Before a single body byte is read, the route
class therefore (1) authenticates the bearer token with the same checks as `CurrentUserDep`
(401 otherwise), (2) counts the request against the per-user `bom_upload_body` limit (429), and
(3) refuses a declared `Content-Length` that is not ASCII digits (400) or exceeds the file limit
plus form overhead (413). The unread body is simply left unconsumed: the error response is sent
without reading it, and the server discards it or closes the connection. Only then is the body
parsed, its stream capped at the same size for requests that declare no length. The file part is
counted again, exactly, while it is streamed to a temporary file. The route still declares
`CurrentUserDep` and the `bom_upload` limit, so the documented checks run as on any route.
"""

import uuid
from collections.abc import Awaitable, Callable, Coroutine
from http import HTTPStatus
from typing import Annotated, Any, Literal

from fastapi import (
    APIRouter,
    Body,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    UploadFile,
)
from fastapi.routing import APIRoute
from pydantic import StringConstraints
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import Response
from starlette.types import Message, Receive

from app.core.dependencies import (
    CurrentUserDep,
    DbSessionDep,
    RateLimiterDep,
    SettingsDep,
    StorageDep,
    authenticate_request,
    check_workspace_permission,
    get_rate_limiter,
    require_workspace_permission,
)
from app.core.exceptions import AppError
from app.core.permissions import Permission, has_permission
from app.domain.bom_fields import STANDARD_FIELDS
from app.models import BomUpload, UploadStatus, User
from app.repositories.bom_repository import BomAccess, BomRepository
from app.repositories.workspace_repository import WorkspaceAccess
from app.schemas.bom import (
    BomDetailResponse,
    BomSummaryResponse,
    FileUrlResponse,
    JobResponse,
    ParseOptionsResponse,
    ParseRequest,
    PreviewResponse,
    TargetFieldResponse,
)
from app.schemas.common import ErrorResponse
from app.schemas.pagination import Page, PaginationDep
from app.services.bom_service import (
    BOM_NAME_MAX_LENGTH,
    REVISION_LABEL_MAX_LENGTH,
    SEARCH_MAX_LENGTH,
    BomDetail,
    BomNotFoundError,
    BomService,
    BomSummary,
    UploadMetadata,
)
from app.services.job_queue import JobQueue, get_job_queue
from app.services.parse_job_service import ParseRequestService
from app.services.upload_intake import (
    FORM_OVERHEAD_BYTES,
    FileTooLargeError,
    request_exceeds_limit,
)
from app.services.workspace_service import ForbiddenError

BOM_UPLOAD_RATE_LIMIT = (30, 3600)  # attempts, window seconds; per user
# Every upload request that reaches the body, valid or not; checked before the body is read.
BOM_UPLOAD_BODY_RATE_LIMIT = (60, 3600)

_ERROR_DESCRIPTIONS: dict[int, str] = {
    HTTPStatus.BAD_REQUEST: (
        "The file is empty, of an unsupported type, or password-protected, or the "
        "Content-Length header is malformed."
    ),
    HTTPStatus.UNAUTHORIZED: "Not authenticated, or the access token was rejected.",
    HTTPStatus.FORBIDDEN: "Your role in this workspace does not allow this action.",
    HTTPStatus.NOT_FOUND: "The workspace, BOM, product or revision was not found.",
    HTTPStatus.CONFLICT: (
        "Conflicts with the BOM's state: a duplicate upload, a job in progress, a state that "
        "cannot be parsed, or not parsed yet."
    ),
    HTTPStatus.REQUEST_ENTITY_TOO_LARGE: "The file exceeds the upload size limit.",
    HTTPStatus.UNPROCESSABLE_ENTITY: "The request failed validation.",
    HTTPStatus.TOO_MANY_REQUESTS: "Too many uploads; retry after the Retry-After seconds.",
    HTTPStatus.SERVICE_UNAVAILABLE: "File storage is temporarily unavailable.",
}

_STANDARD_ERRORS = (HTTPStatus.UNAUTHORIZED, HTTPStatus.FORBIDDEN, HTTPStatus.NOT_FOUND)


def _error_responses(*status_codes: int) -> dict[int | str, dict[str, Any]]:
    """OpenAPI entries documenting the standard error envelope for `status_codes`."""
    return {
        code: {"model": ErrorResponse, "description": _ERROR_DESCRIPTIONS[code]}
        for code in status_codes
    }


class _RequestBodyTooLarge(HTTPException):
    """Raised from the capped receive channel. An `HTTPException` so FastAPI's body parsing
    re-raises it unchanged (it turns other exceptions into a generic 400)."""

    def __init__(self) -> None:
        super().__init__(status_code=HTTPStatus.REQUEST_ENTITY_TOO_LARGE)


def _capped_receive(receive: Receive, max_body_bytes: int) -> Receive:
    """Wrap `receive` so that a request body larger than `max_body_bytes` aborts reading."""
    received = 0

    async def capped() -> Message:
        nonlocal received
        message = await receive()
        if message["type"] == "http.request":
            received += len(message.get("body", b""))
            if received > max_body_bytes:
                raise _RequestBodyTooLarge()
        return message

    return capped


class UploadSizeLimitedRoute(APIRoute):
    """An `APIRoute` that authenticates, rate-limits and bounds the request before FastAPI
    reads the multipart body (see the module docstring for the order)."""

    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        handler = super().get_route_handler()

        async def size_limited_handler(request: Request) -> Response:
            user = await authenticate_request(request)
            await get_rate_limiter(request).hit(
                "bom_upload_body", str(user.id), *BOM_UPLOAD_BODY_RATE_LIMIT
            )
            max_upload_bytes: int = request.app.state.settings.max_upload_bytes
            if request_exceeds_limit(request.headers.get("content-length"), max_upload_bytes):
                raise FileTooLargeError(max_upload_bytes)
            capped = Request(
                request.scope,
                _capped_receive(request.receive, max_upload_bytes + FORM_OVERHEAD_BYTES),
            )
            try:
                return await handler(capped)
            except _RequestBodyTooLarge as exc:
                raise FileTooLargeError(max_upload_bytes) from exc

        return size_limited_handler


router = APIRouter(tags=["boms"])


def get_bom_service(
    session: DbSessionDep, storage: StorageDep, settings: SettingsDep
) -> BomService:
    return BomService(session, storage, settings)


BomServiceDep = Annotated[BomService, Depends(get_bom_service)]


def get_parse_request_service(session: DbSessionDep, settings: SettingsDep) -> ParseRequestService:
    return ParseRequestService(session, settings)


ParseRequestServiceDep = Annotated[ParseRequestService, Depends(get_parse_request_service)]
JobQueueDep = Annotated[JobQueue, Depends(get_job_queue)]


def require_bom_permission(
    permission: Permission,
) -> Callable[[uuid.UUID, User, AsyncSession], Awaitable[BomAccess]]:
    """Dependency factory resolving the `{bom_id}` path param for the current user.

    The upload and the caller's role come from one query joining the upload's workspace
    membership for the caller. Nonexistent, deleted, or not a member -> 404 `NOT_FOUND` (the
    cases are indistinguishable); a member whose role lacks `permission` -> 403 `FORBIDDEN`.
    """

    async def resolve_bom_access(
        bom_id: uuid.UUID, current_user: CurrentUserDep, session: DbSessionDep
    ) -> BomAccess:
        access = await BomRepository(session).get_access(bom_id, current_user.id)
        if access is None:
            raise BomNotFoundError()
        if not has_permission(access.role, permission):
            raise ForbiddenError()
        return access

    return resolve_bom_access


WorkspaceBomReadDep = Annotated[
    WorkspaceAccess, Depends(require_workspace_permission(Permission.BOM_READ))
]
BomReadDep = Annotated[BomAccess, Depends(require_bom_permission(Permission.BOM_READ))]
BomUploadDep = Annotated[BomAccess, Depends(require_bom_permission(Permission.BOM_UPLOAD))]

BomName = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=BOM_NAME_MAX_LENGTH)
]
RevisionLabel = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=REVISION_LABEL_MAX_LENGTH),
]


def _bom_fields(bom: BomUpload) -> dict[str, Any]:
    """The `BomSummaryResponse` fields read straight from the upload row."""
    return {
        "id": bom.id,
        "workspace_id": bom.workspace_id,
        "bom_name": bom.bom_name,
        "revision_label": bom.revision_label,
        "original_filename": bom.original_filename,
        "file_format": bom.file_format,
        "size_bytes": bom.size_bytes,
        "status": bom.status,
        "row_count": bom.row_count,
        "column_count": bom.column_count,
        "product_id": bom.product_id,
        "product_revision_id": bom.product_revision_id,
        "uploaded_by_id": bom.uploaded_by_id,
        "uploaded_at": bom.uploaded_at,
    }


def _summary(item: BomSummary) -> BomSummaryResponse:
    latest = JobResponse.model_validate(item.latest_job) if item.latest_job else None
    return BomSummaryResponse(**_bom_fields(item.bom), latest_job=latest)


def _detail(detail: BomDetail) -> BomDetailResponse:
    bom = detail.bom
    jobs = [JobResponse.model_validate(job) for job in detail.jobs]
    parse_options = (
        ParseOptionsResponse.model_validate(bom.parse_options) if bom.parse_options else None
    )
    return BomDetailResponse(
        **_bom_fields(bom),
        latest_job=jobs[0] if jobs else None,
        parse_options=parse_options,
        jobs=jobs,
    )


async def upload_bom(
    *,
    file: Annotated[UploadFile, File(description="A .csv, .xlsx or .xls BOM file.")],
    workspace_id: Annotated[uuid.UUID, Form()],
    bom_name: Annotated[BomName, Form()],
    current_user: CurrentUserDep,
    session: DbSessionDep,
    service: BomServiceDep,
    queue: JobQueueDep,
    rate_limiter: RateLimiterDep,
    revision_label: Annotated[RevisionLabel | None, Form()] = None,
    product_id: Annotated[uuid.UUID | None, Form()] = None,
    product_revision_id: Annotated[uuid.UUID | None, Form()] = None,
) -> BomDetailResponse:
    """Upload a BOM file (30 uploads per hour per user). Parsing starts automatically.

    The format is detected from the extension and the file's contents; the stored original is
    immutable. The response is 201 even if the parse job could not be queued: the job then shows
    `FAILED` / `QUEUE_UNAVAILABLE` and the BOM can be re-parsed.
    """
    access = await check_workspace_permission(
        session, workspace_id, current_user, Permission.BOM_UPLOAD
    )
    await rate_limiter.hit("bom_upload", str(current_user.id), *BOM_UPLOAD_RATE_LIMIT)
    detail = await service.upload(
        access,
        current_user,
        file,
        UploadMetadata(
            bom_name=bom_name,
            revision_label=revision_label,
            product_id=product_id,
            product_revision_id=product_revision_id,
        ),
        queue,
    )
    return _detail(detail)


# Registered explicitly: only `add_api_route` accepts a per-route `route_class_override`.
router.add_api_route(
    "/boms/upload",
    upload_bom,
    methods=["POST"],
    status_code=HTTPStatus.CREATED,
    response_model=BomDetailResponse,
    responses=_error_responses(
        HTTPStatus.BAD_REQUEST,
        *_STANDARD_ERRORS,
        HTTPStatus.CONFLICT,
        HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
        HTTPStatus.UNPROCESSABLE_ENTITY,
        HTTPStatus.TOO_MANY_REQUESTS,
        HTTPStatus.SERVICE_UNAVAILABLE,
    ),
    route_class_override=UploadSizeLimitedRoute,
)


@router.get(
    "/boms",
    response_model=Page[BomSummaryResponse],
    responses=_error_responses(*_STANDARD_ERRORS, HTTPStatus.UNPROCESSABLE_ENTITY),
)
async def list_boms(
    *,
    access: WorkspaceBomReadDep,
    service: BomServiceDep,
    pagination: PaginationDep,
    q: Annotated[
        str | None,
        Query(
            max_length=SEARCH_MAX_LENGTH,
            description="Case-insensitive substring of the BOM name, filename or revision label.",
        ),
    ] = None,
    bom_status: Annotated[
        UploadStatus | None, Query(alias="status", description="Only BOMs in this status.")
    ] = None,
    product_id: Annotated[
        uuid.UUID | None, Query(description="Only BOMs linked to this product.")
    ] = None,
) -> Page[BomSummaryResponse]:
    """BOMs of the workspace (`workspace_id` query parameter), newest first."""
    items, total = await service.list_boms(
        access,
        q=q.strip() if q else None,
        status=bom_status,
        product_id=product_id,
        limit=pagination.limit,
        offset=pagination.offset,
    )
    return Page(
        items=[_summary(item) for item in items],
        total=total,
        limit=pagination.limit,
        offset=pagination.offset,
    )


@router.get(
    "/boms/{bom_id}",
    response_model=BomDetailResponse,
    responses=_error_responses(*_STANDARD_ERRORS, HTTPStatus.UNPROCESSABLE_ENTITY),
)
async def get_bom(access: BomReadDep, service: BomServiceDep) -> BomDetailResponse:
    """One BOM with its parse options and its last 10 jobs, newest first."""
    return _detail(await service.get_detail(access))


@router.get(
    "/boms/{bom_id}/file",
    response_model=FileUrlResponse,
    responses=_error_responses(
        *_STANDARD_ERRORS, HTTPStatus.UNPROCESSABLE_ENTITY, HTTPStatus.SERVICE_UNAVAILABLE
    ),
)
async def get_bom_file(
    access: BomReadDep, current_user: CurrentUserDep, service: BomServiceDep
) -> FileUrlResponse:
    """A short-lived URL that downloads the original file. Every request is audited."""
    url, expires_at = await service.file_url(access, current_user)
    return FileUrlResponse(url=url, expires_at=expires_at)


class PreviewStageNotAvailableError(AppError):
    """Only the raw preview exists in this phase; the normalized stage comes with mapping."""

    status_code = HTTPStatus.UNPROCESSABLE_ENTITY
    code = "VALIDATION_ERROR"

    def __init__(self) -> None:
        super().__init__(
            "Request validation failed.",
            details=[
                {
                    "field": "query.stage",
                    "message": "The normalized preview is not available yet; use stage=raw.",
                    "type": "value_error",
                }
            ],
        )


_TARGET_FIELDS = [
    TargetFieldResponse(key=field.key, label=field.label, required=field.required)
    for field in STANDARD_FIELDS
]


@router.post(
    "/boms/{bom_id}/parse",
    status_code=HTTPStatus.ACCEPTED,
    response_model=JobResponse,
    responses=_error_responses(
        *_STANDARD_ERRORS, HTTPStatus.CONFLICT, HTTPStatus.UNPROCESSABLE_ENTITY
    ),
)
async def parse_bom(
    access: BomUploadDep,
    current_user: CurrentUserDep,
    service: ParseRequestServiceDep,
    queue: JobQueueDep,
    options: Annotated[ParseRequest | None, Body()] = None,
) -> JobResponse:
    """Parse the BOM again, with explicit options overriding detection (omitted ones are detected).

    Returns 202 with the new job. If the job could not be queued it is returned as `FAILED` /
    `QUEUE_UNAVAILABLE` (the BOM becomes `PARSE_FAILED` and can be re-parsed). A job still
    queued or running blocks the request (409 `JOB_IN_PROGRESS`) until it is 15 minutes old;
    after that it is marked `FAILED` / `STALE_JOB` and replaced.
    """
    parameters = options.model_dump(exclude_none=True) if options else {}
    job = await service.request_parse(access, current_user, parameters, queue)
    return JobResponse.model_validate(job)


@router.get(
    "/boms/{bom_id}/preview",
    response_model=PreviewResponse,
    responses=_error_responses(
        *_STANDARD_ERRORS, HTTPStatus.CONFLICT, HTTPStatus.UNPROCESSABLE_ENTITY
    ),
)
async def get_bom_preview(
    access: BomReadDep,
    service: ParseRequestServiceDep,
    stage: Annotated[
        Literal["raw", "normalized"],
        Query(description="Only `raw` is available; `normalized` arrives with column mapping."),
    ] = "raw",
) -> PreviewResponse:
    """The raw preview of the last successful parse: first rows and the suggested mapping."""
    if stage != "raw":
        raise PreviewStageNotAvailableError()
    bom = await service.raw_preview(access)
    return PreviewResponse.model_validate(
        {
            **(bom.parse_result or {}),
            "status": bom.status,
            "parse_options": bom.parse_options,
            "target_fields": _TARGET_FIELDS,
        }
    )
