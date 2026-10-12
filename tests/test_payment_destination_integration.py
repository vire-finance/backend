"""Real persistence/HTTP regressions using isolated PostgreSQL test schemas."""
import hashlib
import importlib.util
import io
import json
from pathlib import Path
from uuid import uuid4

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.config import Config
from alembic.script import ScriptDirectory
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.api.auth.dependencies import get_current_user
from app.api.auth.security import create_access_token
from app.api.funding.service import FundingService
from app.api.history.service import HistoryService
from app.api.security.tokens import ActionType, create_pin_verification_token
from app.api.transaction.model import Transaction
from app.api.transaction.schema import TransactionCreate
from app.api.transaction.service import TransactionService
from app.core.context import get_context
from app.core.database import get_db
from app.main import app
from app.shared.payment_destination import PaymentDestination
from test_main_funding import ctx, settings_card
from test_request_categories import employee_context


def pay(ctx, card, destination=None, key=None):
    extras = destination.model_dump(exclude_none=True) if destination else {}
    payload = TransactionCreate(card_id=card, amount=50, description="Supplies", category="Marketing", **extras)
    return TransactionService(ctx).execute(payload.card_id, payload.amount, payload.description, key or uuid4(),
        category=payload.category, payment_method=payload.payment_method,
        recipient_account=payload.recipient_account, destination=payload.destination)


@pytest.mark.parametrize("data", [
    dict(payment_type="BANK", destination_name="BCA", destination_account_number="000123"),
    dict(payment_type="E_WALLET", destination_name="GoPay", destination_account_number="008123"),
    dict(payment_type="BANK", destination_name="Other", destination_bank_name="Bank Jago", destination_account_number="001"),
])
def test_destination_persists_and_history_returns_metadata(ctx, settings_card, data):
    employee = employee_context(ctx, ctx.raw_card(settings_card).pocket_id)
    key = uuid4()
    destination = PaymentDestination(**data)
    result = pay(employee, settings_card, destination, key)
    assert result["status"] == "APPROVED"
    ctx.db.expire_all()
    stored = ctx.db.get(Transaction, result["id"])
    for name, value in destination.model_dump().items():
        assert getattr(stored, name) == value
        assert result[name] == value
    assert pay(employee, settings_card, destination, key)["id"] == result["id"]
    history = HistoryService(employee).detail(result["id"])
    assert history["destination_account_number"] == data["destination_account_number"]
    assert FundingService(ctx).get()["balance"] == 950
    assert ctx.raw_card(settings_card).balance == 550


def test_changed_metadata_with_same_display_cannot_reuse_key(ctx, settings_card):
    key = uuid4()
    normal = PaymentDestination(payment_type="BANK", destination_name="BCA", destination_account_number="001")
    other = PaymentDestination(payment_type="BANK", destination_name="Other", destination_bank_name="BCA", destination_account_number="001")
    assert normal.recipient_account == other.recipient_account
    pay(ctx, settings_card, normal, key)
    with pytest.raises(HTTPException) as error:
        pay(ctx, settings_card, other, key)
    assert error.value.status_code == 409
    assert FundingService(ctx).get()["balance"] == 950


def test_legacy_fingerprint_and_record_readability(ctx, settings_card):
    key = uuid4()
    result = pay(ctx, settings_card, key=key)
    expected = hashlib.sha256(json.dumps(dict(card=str(settings_card), request=None, amount=50,
        payment_method="QRIS", recipient_account=None, description="Supplies", type="CARD_PAYMENT", category="Marketing"), sort_keys=True).encode()).hexdigest()
    stored = ctx.db.get(Transaction, result["id"])
    assert stored.fingerprint == expected
    assert result["payment_type"] is None
    assert result["destination_account_number"] is None
    assert pay(ctx, settings_card, key=key)["id"] == result["id"]


def test_declined_payment_retains_destination_without_debit(ctx, settings_card):
    from app.api.card.schema import CardUpdate
    from app.api.card.service import CardService
    CardService(ctx).update(settings_card, CardUpdate(allowed_categories=["Operational"]))
    employee = employee_context(ctx, ctx.raw_card(settings_card).pocket_id)
    destination = PaymentDestination(payment_type="BANK", destination_name="BCA", destination_account_number="001")
    result = pay(employee, settings_card, destination)
    assert result["status"] == "DECLINED"
    assert result["destination_account_number"] == "001"
    assert FundingService(ctx).get()["balance"] == 1000


def test_other_employee_cannot_read_destination(ctx, settings_card):
    from app.api.auth.model import User
    from app.api.card.service import CardService
    from app.core.context import Context
    from app.shared.enums import UserRole
    card = ctx.raw_card(settings_card)
    first = employee_context(ctx, card.pocket_id)
    user = User(email="second-destination@example.test", username="second", phone_number="0800000002",
                password_hash="test", role=UserRole.EMPLOYEE, employer_id=ctx.owner_id)
    ctx.db.add(user)
    ctx.db.flush()
    CardService(ctx).access(settings_card, user.id, True)
    second = Context(ctx.db, user)
    destination = PaymentDestination(payment_type="BANK", destination_name="BCA", destination_account_number="001")
    result = pay(first, settings_card, destination)
    with pytest.raises(HTTPException) as error:
        HistoryService(second).detail(result["id"])
    assert error.value.status_code == 404


@pytest.fixture
def payment_client(ctx):
    previous = app.dependency_overrides.copy()
    app.dependency_overrides[get_context] = lambda: ctx
    app.dependency_overrides[get_current_user] = lambda: ctx.user
    app.dependency_overrides[get_db] = lambda: ctx.db
    sid = uuid4()
    headers = {"Authorization": "Bearer " + create_access_token(ctx.user.id, ctx.user.role.value, sid),
        "X-Action-Token": create_pin_verification_token(str(ctx.user.id), ActionType.PAYMENT, str(sid), ctx.user.security_pin_version),
        "Idempotency-Key": str(uuid4())}
    try:
        with TestClient(app) as client:
            yield client, headers
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous)


def test_http_submission_and_private_history(ctx, settings_card, payment_client):
    client, headers = payment_client
    response = client.post("/transactions", headers=headers, json=dict(card_id=str(settings_card), amount=50,
        description="Supplies", category="Marketing", payment_type="BANK", destination_name="BCA", destination_account_number="000123"))
    assert response.status_code == 200
    assert response.json()["destination_account_number"] == "000123"
    history = client.get("/history/" + response.json()["id"], headers=headers)
    assert history.status_code == 200
    assert history.json()["destination_name"] == "BCA"


@pytest.mark.parametrize("changes", [
    {"destination_name": "Arbitrary bank"}, {"destination_account_number": ""},
    {"destination_name": "Other"}, {"payment_type": "E_WALLET"},
    {"destination_account_number": 123}, {"destination_account_number": None},
])
def test_http_rejects_invalid_destination_without_debit(ctx, settings_card, payment_client, changes):
    client, headers = payment_client
    payload = dict(card_id=str(settings_card), amount=50, description="Supplies", payment_type="BANK",
                   destination_name="BCA", destination_account_number="001")
    response = client.post("/transactions", headers=headers, json=payload | changes)
    assert response.status_code == 422
    assert FundingService(ctx).get()["balance"] == 1000


def test_pin_still_required(ctx, settings_card, payment_client):
    client, headers = payment_client
    headers.pop("X-Action-Token")
    response = client.post("/transactions", headers=headers, json=dict(card_id=str(settings_card), amount=50,
        description="Supplies", payment_type="BANK", destination_name="BCA", destination_account_number="001"))
    assert response.status_code == 401
    assert FundingService(ctx).get()["balance"] == 1000


def test_migration_preserves_existing_payment(ctx, settings_card, monkeypatch):
    result = pay(ctx, settings_card)
    def snapshot():
        return ctx.db.execute(text("SELECT id, amount, actor_id, fingerprint, recipient_account, created_at, processed_at, status FROM transactions")).all()
    before = snapshot()
    names = ("payment_type", "destination_name", "destination_account_number", "destination_bank_name")
    for name in names:
        ctx.db.execute(text('ALTER TABLE transactions DROP COLUMN "' + name + '"'))
    path = Path(__file__).resolve().parents[1] / "alembic/versions/c6d7e8f9a0b1_structured_payment_destinations.py"
    spec = importlib.util.spec_from_file_location("destination_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    monkeypatch.setattr(migration, "op", Operations(MigrationContext.configure(ctx.db.connection())))
    migration.upgrade()
    assert snapshot() == before
    ctx.db.expire_all()
    assert TransactionService(ctx).view(ctx.db.get(Transaction, result["id"]))["destination_name"] is None
    structured = PaymentDestination(payment_type="BANK", destination_name="BCA", destination_account_number="001")
    assert pay(ctx, settings_card, structured)["destination_account_number"] == "001"


def test_additive_revision_and_offline_ddl():
    config = Config()
    config.set_main_option("script_location", str(Path(__file__).resolve().parents[1] / "alembic"))
    scripts = ScriptDirectory.from_config(config)
    assert len(scripts.get_heads()) == 1
    assert "c6d7e8f9a0b1" in {revision.revision for revision in scripts.walk_revisions()}
    assert scripts.get_revision("c6d7e8f9a0b1").down_revision == "b5c6d7e8f9a0"
    output = io.StringIO()
    context = MigrationContext.configure(dialect_name="postgresql", opts={"as_sql": True, "output_buffer": output})
    spec = importlib.util.spec_from_file_location("destination_offline", Path(__file__).resolve().parents[1] / "alembic/versions/c6d7e8f9a0b1_structured_payment_destinations.py")
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    migration.op = Operations(context)
    migration.upgrade()
    ddl = output.getvalue()
    assert ddl.count("ADD COLUMN") == 4
    assert "UPDATE " not in ddl and "DELETE " not in ddl
