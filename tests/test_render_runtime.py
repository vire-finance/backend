"""Render runtime checks use synthetic configuration and no DB/cloud calls."""
import asyncio
import importlib.util
import os
from pathlib import Path
import runpy
import sys

import pytest
from sqlalchemy.pool import NullPool


ROOT = Path(__file__).resolve().parents[1]
DATABASE_URL = "postgresql+psycopg://synthetic_user:synthetic_password@db.example/synthetic_db"


def engine_options(database_url=DATABASE_URL, app_env="production"):
    from app.core.database_options import database_engine_options

    return database_engine_options(database_url, app_env)


def test_production_engine_uses_tls_and_pooler_safe_psycopg_options():
    options = engine_options()

    assert options["poolclass"] is NullPool
    assert options["pool_pre_ping"] is True
    assert options["isolation_level"] == "READ COMMITTED"
    assert options["hide_parameters"] is True
    assert options["connect_args"] == {
        "connect_timeout": 10,
        "sslmode": "require",
        "prepare_threshold": None,
    }


@pytest.mark.parametrize("sslmode", ["require", "verify-ca", "verify-full"])
def test_production_tls_requirements_preserve_stronger_certificate_validation(sslmode):
    options = engine_options(DATABASE_URL + "?sslmode=" + sslmode)

    assert options["connect_args"]["sslmode"] == sslmode
    assert options["connect_args"]["prepare_threshold"] is None


@pytest.mark.parametrize("driver", ["postgresql", "postgresql+psycopg2"])
def test_psycopg2_retains_tls_without_psycopg3_only_arguments(driver):
    url = DATABASE_URL.replace("postgresql+psycopg", driver, 1)
    options = engine_options(url)

    assert options["poolclass"] is NullPool
    assert options["connect_args"] == {"connect_timeout": 10, "sslmode": "require"}


@pytest.mark.parametrize("sslmode", ["disable", "allow", "prefer", "", "invalid_mode"])
def test_insecure_or_unknown_tls_configuration_is_rejected_without_url_disclosure(sslmode):
    url = DATABASE_URL + "?sslmode=" + sslmode

    with pytest.raises(ValueError) as error:
        engine_options(url)

    message = str(error.value)
    for private_value in (url, "synthetic_user", "synthetic_password", "db.example", "synthetic_db"):
        assert private_value not in message


@pytest.mark.parametrize("query", ["sslmode=require&sslmode=disable", "sslmode=require&sslmode=require"])
def test_duplicate_tls_parameters_are_rejected_without_url_disclosure(query):
    url = DATABASE_URL + "?" + query

    with pytest.raises(ValueError) as error:
        engine_options(url)

    for private_value in (url, "synthetic_user", "synthetic_password", "db.example", "synthetic_db"):
        assert private_value not in str(error.value)


def test_url_preparation_threshold_cannot_override_pooler_safe_connection_settings(monkeypatch):
    from sqlalchemy import create_engine

    url = DATABASE_URL + "?prepare_threshold=0"
    engine = create_engine(url, **engine_options(url))
    calls = []

    def blocked_connect(*args, **kwargs):
        calls.append(kwargs)
        raise RuntimeError("synthetic_connection_blocked")

    # Capture the actual SQLAlchemy/driver boundary before any socket or DB I/O.
    monkeypatch.setattr(engine.dialect.loaded_dbapi, "connect", blocked_connect)
    try:
        with pytest.raises(RuntimeError, match="synthetic_connection_blocked"):
            engine.connect()
    finally:
        engine.dispose()

    assert len(calls) == 1
    assert calls[0]["prepare_threshold"] is None
    assert calls[0]["sslmode"] == "require"


@pytest.mark.parametrize("driver", ["sqlite", "mysql+pymysql", "postgresql+asyncpg", "postgresql+pg8000"])
def test_production_rejects_unsupported_database_driver_without_url_disclosure(driver):
    url = DATABASE_URL.replace("postgresql+psycopg", driver, 1)

    with pytest.raises(ValueError) as error:
        engine_options(url)

    assert "synthetic_password" not in str(error.value)
    assert "db.example" not in str(error.value)
    assert url not in str(error.value)


def test_malformed_production_url_is_rejected_without_disclosure():
    url = "postgresql+psycopg://synthetic_user:synthetic_password@db.example:not-a-port/synthetic_db"

    with pytest.raises(ValueError) as error:
        engine_options(url)

    assert "synthetic_user" not in str(error.value)
    assert "synthetic_password" not in str(error.value)
    assert url not in str(error.value)


def test_development_keeps_existing_connection_and_pool_defaults():
    options = engine_options(DATABASE_URL + "?sslmode=disable", "development")

    assert options == {
        "pool_pre_ping": True,
        "isolation_level": "READ COMMITTED",
        "hide_parameters": True,
    }


@pytest.mark.parametrize("app_env", ["development", "production"])
def test_app_environment_selects_expected_database_and_ai_settings(app_env):
    from app.core.config import Settings

    configured = Settings(
        _env_file=None,
        APP_ENV=app_env,
        SECRET_KEY="synthetic-signing-key",
        DEV_DATABASE_URL="postgresql+psycopg://development.example/development",
        PROD_DATABASE_URL="postgresql+psycopg://production.example/production?sslmode=require",
        DEV_LLM_PROVIDER="ollama",
        PROD_LLM_PROVIDER="groq",
        DEV_LLM_MODEL="synthetic-local-model",
        PROD_LLM_MODEL="synthetic-production-model",
        DEV_LLM_BASE_URL="http://localhost:11434",
        PROD_LLM_BASE_URL="",
        PROD_LLM_API_KEY="synthetic-groq-key",
        SUPABASE_URL="https://project.example",
        SUPABASE_SECRET_KEY="sb_secret_SYNTHETIC_BACKEND_KEY",
    )

    prefix = "PROD" if app_env == "production" else "DEV"
    assert configured.DATABASE_URL == getattr(configured, prefix + "_DATABASE_URL")
    assert configured.LLM_PROVIDER == getattr(configured, prefix + "_LLM_PROVIDER")
    assert configured.LLM_MODEL == getattr(configured, prefix + "_LLM_MODEL")
    assert configured.LLM_BASE_URL == getattr(configured, prefix + "_LLM_BASE_URL")
    assert configured.LLM_API_KEY == ("synthetic-groq-key" if app_env == "production" else "")


@pytest.fixture
def entrypoint():
    spec = importlib.util.spec_from_file_location("vire_render_entrypoint", ROOT / "docker/entrypoint.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_entrypoint_defaults_to_single_worker_bound_to_render_interface(entrypoint):
    command = entrypoint.server_command({})

    assert command[:4] == [sys.executable, "-m", "uvicorn", "app.main:app"]
    assert command[command.index("--host") + 1] == "0.0.0.0"
    assert command[command.index("--port") + 1] == "10000"
    assert command[command.index("--workers") + 1] == "1"
    assert "--proxy-headers" in command
    assert command[command.index("--forwarded-allow-ips") + 1] == "127.0.0.1"
    assert command[command.index("--timeout-graceful-shutdown") + 1] == "25"
    assert "--reload" not in command


@pytest.mark.parametrize("port", ["1", "10000", "65535", "00080"])
def test_entrypoint_reads_render_port_and_explicit_proxy_trust(entrypoint, port):
    command = entrypoint.server_command({"PORT": port, "FORWARDED_ALLOW_IPS": "*"})

    assert command[command.index("--port") + 1] == str(int(port))
    assert command[command.index("--forwarded-allow-ips") + 1] == "*"


@pytest.mark.parametrize("port", ["", "0", "65536", " 10000 ", "-1", "1.5", "10000\n", "\uff11\uff12\uff13", "synthetic-private-value"])
def test_entrypoint_rejects_invalid_ports_with_sanitized_error(entrypoint, port):
    with pytest.raises(ValueError) as error:
        entrypoint.server_command({"PORT": port})

    message = str(error.value)
    assert message == "PORT must be an integer between 1 and 65535."
    assert "synthetic-private-value" not in message


def test_entrypoint_replaces_pid_one_with_uvicorn_without_a_shell(monkeypatch):
    calls = []
    monkeypatch.setenv("PORT", "12345")
    monkeypatch.setenv("FORWARDED_ALLOW_IPS", "127.0.0.1")
    monkeypatch.setattr(os, "execv", lambda executable, command: calls.append((executable, command)))

    runpy.run_path(str(ROOT / "docker/entrypoint.py"), run_name="__main__")

    assert len(calls) == 1
    executable, command = calls[0]
    assert executable == sys.executable == command[0]
    assert command[command.index("--port") + 1] == "12345"


@pytest.mark.parametrize("executable,prefix", [
    ("/usr/bin/tesseract", "/usr/share/tesseract-ocr/5/tessdata"),
    (r"C:\Program Files\Tesseract-OCR\tesseract.exe", r"C:\Program Files\Tesseract-OCR\tessdata"),
])
def test_tesseract_explicit_executable_preserves_linux_and_windows_configuration(monkeypatch, executable, prefix):
    from app.api.ocr import tesseract

    monkeypatch.setattr(tesseract.settings, "TESSERACT_CMD", executable)
    monkeypatch.setattr(tesseract.settings, "TESSDATA_PREFIX", prefix)
    monkeypatch.setattr(tesseract.pytesseract.pytesseract, "tesseract_cmd", "synthetic-initial-command")
    monkeypatch.setenv("TESSDATA_PREFIX", "synthetic-initial-prefix")

    tesseract.configure_tesseract()

    assert tesseract.pytesseract.pytesseract.tesseract_cmd == executable
    assert os.environ["TESSDATA_PREFIX"] == prefix


def test_lifespan_validates_storage_and_awaits_both_workers_before_closing_ai(monkeypatch):
    import app.main as main

    events, tasks = [], []
    original_create_task = asyncio.create_task

    async def worker(name):
        events.append(name + "_started")
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            await asyncio.sleep(0)
            events.append(name + "_stopped")
            raise

    def create_task(coroutine):
        task = original_create_task(coroutine)
        tasks.append(task)
        return task

    def close_ai():
        assert {"notification_stopped", "ocr_stopped"}.issubset(events)
        events.append("ai_closed")

    monkeypatch.setattr(main, "validate_production_storage", lambda: events.append("storage_validated"))
    monkeypatch.setattr(main, "run_worker", lambda: worker("notification"))
    monkeypatch.setattr(main, "run_ocr_worker", lambda: worker("ocr"))
    monkeypatch.setattr(main.asyncio, "create_task", create_task)
    monkeypatch.setattr(main, "close_inference_client", close_ai)

    async def exercise():
        async with main.lifespan(main.app):
            await asyncio.sleep(0)
            assert events[0] == "storage_validated"
            assert {"notification_started", "ocr_started"}.issubset(events)
            assert main.health() == {"status": "ok"}

    asyncio.run(exercise())

    assert len(tasks) == 2 and all(task.done() and task.cancelled() for task in tasks)
    assert events[-1] == "ai_closed"


def test_lifespan_storage_validation_failure_prevents_workers_from_starting(monkeypatch):
    import app.main as main
    from app.core.storage import StorageError

    def unavailable_storage():
        raise StorageError("bucket_not_private")

    monkeypatch.setattr(main, "validate_production_storage", unavailable_storage)
    monkeypatch.setattr(main, "run_worker", lambda: pytest.fail("Workers must not start before Storage validation"))
    monkeypatch.setattr(main, "run_ocr_worker", lambda: pytest.fail("OCR must not start before Storage validation"))

    async def exercise():
        with pytest.raises(StorageError) as error:
            async with main.lifespan(main.app):
                pytest.fail("The application must not serve requests with an invalid production bucket")
        assert error.value.code == "bucket_not_private"

    asyncio.run(exercise())
