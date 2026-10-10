from app.workers.celery_app import create_celery_app
from tests.conftest import make_settings


def test_celery_uses_configured_broker_and_safe_defaults() -> None:
    celery = create_celery_app(
        make_settings(celery_broker_url="redis://broker:6379/1", celery_task_always_eager=True)
    )

    assert celery.conf.broker_url == "redis://broker:6379/1"
    assert celery.conf.task_always_eager is True
    assert celery.conf.accept_content == ["json"]
    assert celery.conf.task_acks_late is True
