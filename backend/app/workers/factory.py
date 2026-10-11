"""Builds the Celery application. Importing this module has no side effects.

The API imports only this module (to send tasks by name); the worker entry point
`app.workers.celery_app` builds its module-level app from here and wires up logging.
"""

from celery import Celery

from app.core.config import Settings


def create_celery_app(settings: Settings) -> Celery:
    app = Celery(
        "bomviz",
        broker=settings.celery_broker_url,
        backend=settings.celery_result_backend,
        include=["app.workers.tasks"],
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
