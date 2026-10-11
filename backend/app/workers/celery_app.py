"""Celery worker entry point: the module-level app and the worker's logging setup.

Start a worker with: ``celery -A app.workers.celery_app:celery_app worker``

Importing this module reads the settings and connects a Celery signal, so only the worker (and
the task modules it loads) import it; the API uses `app.workers.factory` instead.
"""

from typing import Any

from celery.signals import setup_logging

from app.core.config import get_settings
from app.core.logging import configure_logging
from app.workers.factory import create_celery_app

__all__ = ["celery_app", "create_celery_app"]


def _use_application_logging(**_: Any) -> None:
    """Replace Celery's logging setup so worker logs share the API's structured format."""
    configure_logging(get_settings())


setup_logging.connect(_use_application_logging)
celery_app = create_celery_app(get_settings())
