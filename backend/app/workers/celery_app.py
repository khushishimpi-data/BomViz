"""Celery application for background parsing, validation, import and export jobs.

Start a worker with: ``celery -A app.workers.celery_app:celery_app worker``
"""

from typing import Any

from celery import Celery
from celery.signals import setup_logging

from app.core.config import Settings, get_settings
from app.core.logging import configure_logging


def create_celery_app(settings: Settings) -> Celery:
    app = Celery(
        "bomviz", broker=settings.celery_broker_url, backend=settings.celery_result_backend
    )
    app.conf.update(
        task_always_eager=settings.celery_task_always_eager,
        task_eager_propagates=True,
        task_serializer="json",
        result_serializer="json",
        accept_content=["json"],
        timezone="UTC",
        enable_utc=True,
        task_track_started=True,
        task_acks_late=True,
        task_reject_on_worker_lost=True,
        worker_prefetch_multiplier=1,
        broker_connection_retry_on_startup=True,
    )
    return app


def _use_application_logging(**_: Any) -> None:
    """Replace Celery's logging setup so worker logs share the API's structured format."""
    configure_logging(get_settings())


setup_logging.connect(_use_application_logging)
celery_app = create_celery_app(get_settings())
