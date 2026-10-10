import json
import logging
import sys

from app.core.logging import JsonFormatter, RequestContextFilter, request_id_ctx


def _format(record: logging.LogRecord) -> dict[str, object]:
    RequestContextFilter().filter(record)
    parsed: dict[str, object] = json.loads(JsonFormatter().format(record))
    return parsed


def _record(message: str, **extra: object) -> logging.LogRecord:
    record = logging.LogRecord("app.test", logging.INFO, __file__, 1, message, None, None)
    for key, value in extra.items():
        setattr(record, key, value)
    return record


def test_json_log_includes_request_id_and_domain_context() -> None:
    token = request_id_ctx.set("req-42")
    try:
        payload = _format(_record("parsed upload", bom_upload_id="b-1", unrelated="ignored"))
    finally:
        request_id_ctx.reset(token)

    assert payload["message"] == "parsed upload"
    assert payload["level"] == "INFO"
    assert payload["request_id"] == "req-42"
    assert payload["bom_upload_id"] == "b-1"
    assert "unrelated" not in payload


def test_json_log_omits_request_id_outside_a_request() -> None:
    assert "request_id" not in _format(_record("worker started"))


def test_json_log_includes_exception_traceback() -> None:
    try:
        raise ValueError("bad row")
    except ValueError:
        record = logging.LogRecord(
            "app.test", logging.ERROR, __file__, 1, "failed", None, sys.exc_info()
        )

    assert "ValueError: bad row" in str(_format(record)["exception"])
