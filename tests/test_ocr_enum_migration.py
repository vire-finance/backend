"""Real Alembic/PostgreSQL regression tests in a disposable test database.

The configured application database is never migrated or written. Each test
uses an isolated schema inside a separately created, randomly named database.
"""
import asyncio
import hashlib
import importlib.util
import io
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
import uuid

from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
from fastapi import UploadFile
from PIL import Image
import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import sessionmaker
from starlette.datastructures import Headers

import app.core.models
from app.api.auth.model import User
from app.api.ocr import service, worker
from app.api.ocr.model import OCRDocument
from app.core.config import settings
from app.shared.enums import OCRStatus, UserRole
from app.shared.utils import now_utc

ROOT = Path(__file__).resolve().parents[1]
BASE_REVISION = "a4b5c6d7e8f9"
REVISION = "b5c6d7e8f9a0"
UPPER_LABELS = ["PENDING", "PROCESSING", "COMPLETED", "FAILED"]
spec = importlib.util.spec_from_file_location(
    "ocr_enum_normalization", ROOT / "alembic/versions/b5c6d7e8f9a0_normalize_ocr_enum_labels.py")
normalization = importlib.util.module_from_spec(spec)
spec.loader.exec_module(normalization)


@pytest.fixture(scope="module")
def scratch_database():
    application_url = make_url(settings.DATABASE_URL)
    if settings.APP_ENV != "development" or application_url.host not in {"localhost", "127.0.0.1", "::1"}:
        pytest.skip("OCR migration tests require a loopback development PostgreSQL server")
    name = "test_vire_ocr_" + uuid.uuid4().hex
    assert name != application_url.database and name.startswith("test_vire_ocr_")
    admin = create_engine(application_url.set(database="postgres"),
                          isolation_level="AUTOCOMMIT", hide_parameters=True,
                          connect_args={"connect_timeout": 5})
    created = False
    try:
        with admin.connect() as connection:
            allowed = connection.scalar(text(
                "SELECT rolsuper OR rolcreatedb FROM pg_roles WHERE rolname = current_user"))
            if not allowed:
                pytest.skip("PostgreSQL test role needs CREATEDB for the disposable test database")
            connection.execute(text(f'CREATE DATABASE "{name}"'))
            created = True
        yield application_url.set(database=name)
    finally:
        if created:
            with admin.connect() as connection:
                # Only the generated, dedicated test database may be cleaned up.
                assert name.startswith("test_vire_ocr_") and name != application_url.database
                connection.execute(text(f'DROP DATABASE "{name}"'))
        admin.dispose()


@pytest.fixture
def migrated_schema(scratch_database, monkeypatch):
    schema = "ocr_enum_" + uuid.uuid4().hex
    admin = create_engine(scratch_database, hide_parameters=True)
    isolated_url = scratch_database.update_query_dict({"options": "-csearch_path=" + schema})
    isolated = create_engine(isolated_url, hide_parameters=True)
    # Avoid changing the process's logging handlers while testing migrations.
    config = Config()
    config.set_main_option("script_location", str(ROOT / "alembic"))
    try:
        with admin.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        # env.py uses the already-created Settings instance; direct it exclusively
        # to the disposable test database and this test's private search_path.
        monkeypatch.setattr(settings, "DATABASE_URL", isolated_url.render_as_string(hide_password=False))
        command.upgrade(config, BASE_REVISION)
        factory = sessionmaker(bind=isolated, expire_on_commit=False)
        monkeypatch.setattr(service, "SessionLocal", factory)
        yield isolated, factory, config, schema
    finally:
        isolated.dispose()
        with admin.begin() as connection:
            assert schema.startswith("ocr_enum_")
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()


def labels(engine):
    with engine.connect() as connection:
        return connection.scalars(text(
            "SELECT enumlabel::text FROM pg_enum WHERE enumtypid = 'ocrstatus'::regtype "
            "ORDER BY enumsortorder")).all()


def make_lowercase(engine, schema):
    with engine.begin() as connection:
        for label in UPPER_LABELS:
            connection.execute(text(f'ALTER TYPE "{schema}".ocrstatus RENAME VALUE '
                                    f"'{label}' TO '{label.lower()}'"))


def snapshots(engine):
    with engine.connect() as connection:
        return connection.scalars(text(
            "SELECT row_to_json(d) FROM ocr_documents d ORDER BY id")).all()


def physical_rows(engine):
    with engine.connect() as connection:
        return connection.execute(text(
            "SELECT id, xmin::text, ctid::text FROM ocr_documents ORDER BY id")).all()


def seed_records(factory, directory, *, recovery_jobs=False):
    directory.mkdir()
    with factory() as db:
        user = User(email="ocr-migration@example.test", username="ocr-migration",
                    phone_number="0800000000", password_hash="test", role=UserRole.EMPLOYEE)
        db.add(user)
        db.flush()
        records = []
        statuses = [OCRStatus.COMPLETED] * 8 + [OCRStatus.FAILED] * 28
        if recovery_jobs:
            statuses += [OCRStatus.PENDING, OCRStatus.PROCESSING, OCRStatus.PROCESSING]
        for index, status in enumerate(statuses):
            identifier = uuid.uuid4()
            stored_name = f"{identifier}.png"
            path = directory / stored_name
            path.write_bytes(f"synthetic receipt {index}".encode())
            timestamp = datetime(2020, 1, 2, 3, 4, 5, 123456)
            if recovery_jobs and index == len(statuses) - 1:
                timestamp = now_utc()
            doc = OCRDocument(id=identifier, user_id=user.id, original_filename=f"receipt-{index}.png",
                stored_filename=stored_name, storage_path=str(path), mime_type="image/png",
                file_size=path.stat().st_size, ocr_status=status,
                raw_ocr_text="Synthetic extraction\nTOTAL Rp 150.000" if status == OCRStatus.COMPLETED else None,
                extracted_other_party_name="Synthetic Shop" if status == OCRStatus.COMPLETED else None,
                extracted_total_amount=Decimal("150000") if status == OCRStatus.COMPLETED else None,
                extracted_date=date(2020, 1, 2) if status == OCRStatus.COMPLETED else None,
                ocr_error="Synthetic OCR failure" if status == OCRStatus.FAILED else None,
                created_at=timestamp, updated_at=timestamp)
            db.add(doc)
            records.append(identifier)
        db.commit()
        return user.id, records


@pytest.mark.parametrize("lowercase", [True, False], ids=["legacy-lowercase", "already-uppercase"])
def test_migration_preserves_records_and_enables_orm_and_recovery(migrated_schema, tmp_path, lowercase):
    engine, factory, config, schema = migrated_schema
    receipts = tmp_path / "receipts"
    _, identifiers = seed_records(factory, receipts)
    if lowercase:
        make_lowercase(engine, schema)
    before = snapshots(engine)
    physical_before = physical_rows(engine)
    files_before = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in receipts.iterdir()}
    command.upgrade(config, REVISION)
    assert labels(engine) == UPPER_LABELS
    after = snapshots(engine)
    assert after == [{**row, "ocr_status": row["ocr_status"].upper()} for row in before]
    assert physical_rows(engine) == physical_before
    assert {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in receipts.iterdir()} == files_before
    with factory() as db:
        records = db.scalars(select(OCRDocument).order_by(OCRDocument.id)).all()
        assert {doc.id for doc in records} == set(identifiers)
        assert sum(doc.ocr_status == OCRStatus.COMPLETED for doc in records) == 8
        assert sum(doc.ocr_status == OCRStatus.FAILED for doc in records) == 28
    assert worker.pending_documents() == []
    assert snapshots(engine) == after
    # Exercise the migration's already-correct branch directly too: Alembic
    # normally skips an applied revision, which alone would not test idempotence.
    with engine.begin() as connection:
        with Operations.context(MigrationContext.configure(connection)):
            normalization.upgrade()
    assert snapshots(engine) == after and physical_rows(engine) == physical_before
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == REVISION


def test_recovery_after_legacy_migration_preserves_fresh_jobs(migrated_schema, tmp_path):
    engine, factory, config, schema = migrated_schema
    _, identifiers = seed_records(factory, tmp_path / "receipts", recovery_jobs=True)
    make_lowercase(engine, schema)
    command.upgrade(config, REVISION)
    pending, stale, fresh = identifiers[-3:]
    assert pending in worker.pending_documents()
    with factory() as db:
        assert db.get(OCRDocument, stale).ocr_status == OCRStatus.FAILED
        assert "interrupted" in db.get(OCRDocument, stale).ocr_error
        assert db.get(OCRDocument, fresh).ocr_status == OCRStatus.PROCESSING
        assert db.get(OCRDocument, pending).ocr_status == OCRStatus.PENDING


@pytest.mark.parametrize("lowercase", [True, False], ids=["legacy-lowercase", "already-uppercase"])
def test_new_upload_and_processing_after_migration(migrated_schema, tmp_path, monkeypatch, lowercase):
    engine, factory, config, schema = migrated_schema
    user_id, _ = seed_records(factory, tmp_path / "existing-receipts")
    if lowercase:
        make_lowercase(engine, schema)
    command.upgrade(config, REVISION)
    upload_dir = tmp_path / "new-uploads"
    monkeypatch.setattr(settings, "UPLOAD_DIR", str(upload_dir))
    image = Image.new("RGB", (20, 20), "white")
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    image.close()
    original_bytes = buffer.getvalue()
    upload = UploadFile(file=io.BytesIO(original_bytes), filename="new-receipt.png",
                        headers=Headers({"content-type": "image/png"}))
    with factory() as db:
        doc = asyncio.run(service.OCRService(db).upload_document(upload, user_id))
        identifier = doc.id
        assert doc.ocr_status == OCRStatus.PENDING
        assert Path(doc.storage_path).read_bytes() == original_bytes
    monkeypatch.setattr(service, "run_ocr", lambda **kwargs: "Synthetic Shop\nTOTAL Rp 150.000")
    service.process_ocr_document_task(identifier)
    with factory() as db:
        doc = db.get(OCRDocument, identifier)
        assert doc.ocr_status == OCRStatus.COMPLETED
        assert doc.extracted_total_amount == Decimal("150000")
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT count(*) FROM ocr_documents")) == 37


@pytest.mark.parametrize("configuration", ["mixed", "duplicate-case", "extra", "missing", "varchar"])
def test_unexpected_enum_is_rejected_without_partial_changes(migrated_schema, configuration):
    engine, _, config, schema = migrated_schema
    with engine.begin() as connection:
        if configuration == "mixed":
            connection.execute(text(f'ALTER TYPE "{schema}".ocrstatus RENAME VALUE \'PENDING\' TO \'pending\''))
        elif configuration in {"duplicate-case", "extra"}:
            addition = "pending" if configuration == "duplicate-case" else "UNKNOWN"
            connection.execute(text(f'ALTER TYPE "{schema}".ocrstatus ADD VALUE \'{addition}\''))
        elif configuration == "missing":
            connection.execute(text(f'ALTER TYPE "{schema}".ocrstatus RENAME VALUE \'FAILED\' TO \'UNKNOWN\''))
        else:
            connection.execute(text("ALTER TABLE ocr_documents ALTER COLUMN ocr_status TYPE varchar USING ocr_status::text"))
    before = labels(engine)
    with pytest.raises(DBAPIError, match="OCR enum normalization"):
        command.upgrade(config, REVISION)
    assert labels(engine) == before
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == BASE_REVISION


def test_revision_chain_and_offline_sql():
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "alembic"))
    scripts = ScriptDirectory.from_config(config)
    assert len(scripts.get_heads()) == 1
    assert REVISION in {step.revision for step in scripts.walk_revisions()}
    assert scripts.get_revision(REVISION).down_revision == BASE_REVISION
    output = io.StringIO()
    context = MigrationContext.configure(dialect_name="postgresql",
        opts={"as_sql": True, "output_buffer": output})
    with Operations.context(context):
        normalization.upgrade()
    emitted = output.getvalue()
    assert "RENAME VALUE" in emitted and "DO $ocr_enum_fix$" in emitted
