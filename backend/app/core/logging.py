"""Structured logging with request correlation.

Every log record carries the active `request_id` (set by `RequestContextMiddleware`) and any
domain identifiers passed through `extra=` such as `bom_upload_id` or `workspace_id`.
"""

import json
import logging
import sys
from contextvars import ContextVar
from datetime import UTC, datetime

from app.core.config import Settings

request_id_ctx: ContextVar[str | None] = ContextVar("request_id", default=None)

CONTEXT_FIELDS = (
    "bom_upload_id",
    "workspace_id",
    "user_id",
    "job_id",
    "method",
    "path",
    "status_code",
    "duration_ms",
)


class RequestContextFilter(logging.Filter):
    """Attach the current request id to every record so formatters can use it."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_ctx.get()
        return True


class JsonFormatter(logging.Formatter):
    """Render records as single-line JSON objects for log aggregation."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "timestamp": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        request_id = getattr(record, "request_id", None)
        if request_id:
            payload["request_id"] = request_id
        for field in CONTEXT_FIELDS:
            value = getattr(record, field, None)
            if value is not None:
                payload[field] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def configure_logging(settings: Settings) -> None:
    """Install a single stdout handler on the root logger. Safe to call more than once."""
    handler = logging.StreamHandler(sys.stdout)
    handler.addFilter(RequestContextFilter())
    if settings.log_format == "json":
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)-8s [%(request_id)s] %(name)s: %(message)s")
        )

    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(settings.log_level.upper())
    for noisy in ("uvicorn.access", "botocore", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
