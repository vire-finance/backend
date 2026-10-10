import uuid
from decimal import Decimal
import pytest
from fastapi import HTTPException
from sqlalchemy import select, text
from sqlalchemy.orm import Session
import app.core.models
from app.core.database import engine
from app.core.context import Context
from app.api.auth.model import User
from app.shared.base import Base
from app.shared.enums import UserRole
from app.api.funding.model import FundMovement
from app.api.funding.schema import FundAccountInput, AllocationInput
from app.api.funding.service import FundingService
from app.api.payment.schema import TopUpCreate
from app.api.payment.service import PaymentService
from app.api.pocket.schema import PocketCreate, PocketUpdate
from app.api.pocket.service import PocketService

@pytest.fixture
def ctx():
    # Real PostgreSQL, isolated tables; outer rollback removes the whole test schema.
    with engine.connect() as connection:
        transaction = connection.begin()
        schema = 'test_funding_' + uuid.uuid4().hex
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        connection.execute(text(f'SET LOCAL search_path TO "{schema}"'))
        Base.metadata.create_all(connection)
        with Session(bind=connection, join_transaction_mode='create_savepoint', expire_on_commit=False) as db:
            user = User(email='funding@example.test', username='funding', phone_number='0800000000', password_hash='test', role=UserRole.OWNER)
            db.add(user); db.flush()
            yield Context(db, user)
        transaction.rollback()


def setup(ctx, balance=1000):
    service = FundingService(ctx)
    service.save(FundAccountInput(account_type='BANK', provider_name='Demo Bank', account_number='123456', account_holder='Demo Owner'))
    # Use a controlled balance for funding-rule tests, independent of demo seed.
    account = service.account()
    account.balance = 0
    for movement in ctx.db.scalars(select(FundMovement).where(FundMovement.account_id == account.id)).all():
        ctx.db.delete(movement)
    ctx.db.commit()
    if balance:
        payments = PaymentService(ctx)
        pending = payments.create(TopUpCreate(amount=balance, method='BANK_TRANSFER'), uuid.uuid4())
        payments.confirm(pending['id'])
    return service


def pocket(ctx, amount=200):
    result = PocketService(ctx).create(PocketCreate(name='Supplies', allocated_amount=amount, monthly_limit=1000))
    return ctx.raw_pocket(result['id'])


def test_setup_zero_balance_and_edit_preserves_balance(ctx):
    service = setup(ctx)
    service.save(FundAccountInput(account_type='E_WALLET', provider_name='Demo Wallet', account_number='987654', account_holder='Owner'))
    assert service.get()['balance'] == 1000
    assert service.get()['is_simulated'] is True
    assert ctx.user.fund_account_type == 'E_WALLET'


def test_topup_retries_do_not_duplicate_credit(ctx):
    service = setup(ctx, 0)
    key = uuid.uuid4(); payload = TopUpCreate(amount=500, method='QR_CODE')
    payments = PaymentService(ctx)
    first = payments.create(payload, key)
    assert payments.create(payload, key)['id'] == first['id']
    assert service.get()['balance'] == 0
    payments.confirm(first['id']); payments.confirm(first['id'])
    assert service.get()['balance'] == 500
    assert len(service.history()) == 1


def test_topup_key_rejects_different_amount(ctx):
    setup(ctx, 0); key = uuid.uuid4(); payments = PaymentService(ctx)
    payments.create(TopUpCreate(amount=100, method='QR_CODE'), key)
    with pytest.raises(HTTPException) as error:
        payments.create(TopUpCreate(amount=101, method='QR_CODE'), key)
    assert error.value.status_code == 409


def test_pocket_creation_and_allocation_leave_main_funds_unchanged(ctx):
    service = setup(ctx); target = pocket(ctx)
    key = uuid.uuid4(); payload = AllocationInput(pocket_id=target.id, amount=300)
    service.allocate(payload, key); service.allocate(payload, key)
    assert service.get()['balance'] == 1000
    assert target.remaining_amount == 500
    assert service.get()['balance'] == 1000
    assert len(service.history()) == 3


def test_allocation_can_exceed_cash_without_moving_funds(ctx):
    service = setup(ctx); target = pocket(ctx)
    service.allocate(AllocationInput(pocket_id=target.id, amount=5000), uuid.uuid4())
    assert service.get()['balance'] == 1000
    assert target.remaining_amount == 5200


def test_allocation_key_rejects_mismatch(ctx):
    service = setup(ctx); target = pocket(ctx); key = uuid.uuid4()
    service.allocate(AllocationInput(pocket_id=target.id, amount=100), key)
    with pytest.raises(HTTPException):
        service.allocate(AllocationInput(pocket_id=target.id, amount=101), key)
    assert service.get()['balance'] == 1000


def test_budget_edit_keeps_main_funds_unchanged(ctx):
    service = setup(ctx); target = pocket(ctx, 400)
    pockets = PocketService(ctx)
    pockets.update(target.id, PocketUpdate(allocated_amount=200))
    assert service.get()['balance'] == 1000
    pockets.update(target.id, PocketUpdate(allocated_amount=500))
    assert service.get()['balance'] == 1000
    assert target.remaining_amount == 500


def test_missing_account_blocks_new_topup(ctx):
    with pytest.raises(HTTPException) as error:
        PaymentService(ctx).create(TopUpCreate(amount=100, method='BANK_TRANSFER'), uuid.uuid4())
    assert error.value.status_code == 409


def test_employee_cannot_access_funding(ctx):
    setup(ctx)
    employee = User(email='employee@example.test', username='employee', phone_number='0800000001', password_hash='test', role=UserRole.EMPLOYEE, employer_id=ctx.owner_id)
    ctx.db.add(employee); ctx.db.flush()
    employee_ctx = Context(ctx.db, employee)
    with pytest.raises(HTTPException) as error:
        FundingService(employee_ctx).get()
    assert error.value.status_code == 403


def test_other_owner_pocket_is_rejected(ctx):
    service = setup(ctx); target = pocket(ctx)
    other = User(email='other@example.test', username='other', phone_number='0800000002', password_hash='test', role=UserRole.OWNER)
    ctx.db.add(other); ctx.db.flush()
    other_ctx = Context(ctx.db, other); setup(other_ctx)
    with pytest.raises(HTTPException) as error:
        FundingService(other_ctx).allocate(AllocationInput(pocket_id=target.id, amount=100), uuid.uuid4())
    assert error.value.status_code == 404
    assert service.get()['balance'] == 1000


def test_new_topup_cannot_bypass_main_account(ctx):
    setup(ctx); target = pocket(ctx)
    with pytest.raises(HTTPException) as error:
        PaymentService(ctx).create(TopUpCreate(pocket_id=target.id, amount=100, method='QR_CODE'), uuid.uuid4())
    assert error.value.status_code == 422


def test_legacy_pending_topup_keeps_original_destination(ctx):
    from app.api.payment.model import TopUp
    service = setup(ctx); target = pocket(ctx)
    old = TopUp(owner_id=ctx.owner_id, pocket_id=target.id, amount=50, method='QR_CODE', idempotency_key=uuid.uuid4(), status='PENDING')
    ctx.db.add(old); ctx.db.flush()
    PaymentService(ctx).confirm(old.id)
    assert target.remaining_amount == 250
    assert service.get()['balance'] == 1000

def test_http_funding_contract_and_pin_requirement(ctx):
    from fastapi.testclient import TestClient
    from app.main import app
    from app.core.context import get_context
    from app.core.database import get_db
    from app.api.auth.dependencies import get_current_user
    # Overrides only for isolated test data. PIN validation remains active.
    app.dependency_overrides[get_context] = lambda: ctx
    app.dependency_overrides[get_current_user] = lambda: ctx.user
    app.dependency_overrides[get_db] = lambda: ctx.db
    try:
        with TestClient(app) as client:
            assert client.get('/fund-account').json() is None
            response = client.put('/fund-account', json={'account_type': 'BANK', 'provider_name': 'Demo Bank', 'account_number': '123456', 'account_holder': 'Owner'})
            assert response.status_code == 200
            assert response.json()['balance'] == 20_000_000
            assert client.get('/fund-account/movements').json()[0]['amount'] == 20_000_000
            response = client.post('/payments', json={'amount': 100, 'method': 'BANK_TRANSFER'}, headers={'Idempotency-Key': str(uuid.uuid4())})
            assert response.status_code == 201
            assert response.json()['pocket_id'] is None
            assert client.get('/fund-account').json()['balance'] == 20_000_000
            # No authorization or PIN token must not confirm or allocate funds.
            denied = client.post('/payments/' + response.json()['id'] + '/confirm', json={'confirmed': True})
            assert denied.status_code in (401, 403)
            assert client.get('/fund-account').json()['balance'] == 20_000_000
    finally:
        app.dependency_overrides.clear()

def test_payment_debits_and_updates_history_and_spending(ctx):
    from app.api.card.schema import CardCreate
    from app.api.card.service import CardService
    from app.api.transaction.service import TransactionService
    from app.api.history.service import HistoryService
    from app.api.dashboard.service import DashboardService
    funding = setup(ctx, 1000)
    target = pocket(ctx, 600)
    card = CardService(ctx).create(CardCreate(pocket_id=target.id, name='QR Expenses', theme='BLUE', initial_balance=600, monthly_limit=600, category='Supplies'))
    key = uuid.uuid4()
    result = TransactionService(ctx).execute(card['id'], 150, 'QR merchant payment', key)
    assert result['status'] == 'APPROVED'
    retry = TransactionService(ctx).execute(card['id'], 150, 'QR merchant payment', key)
    assert retry['id'] == result['id']
    assert ctx.raw_card(card['id']).balance == 450
    assert ctx.raw_pocket(target.id).remaining_amount == 450
    assert funding.get()['balance'] == 850
    history = HistoryService(ctx).list()
    assert history['total'] == 1
    assert history['items'][0]['id'] == result['id']
    spending = DashboardService(ctx).spending()
    assert spending['total_spent'] == 150
    assert spending['categories'][0]['category'] == 'Supplies'
    assert spending['categories'][0]['amount'] == 150


def test_declined_payment_does_not_reduce_balance_or_inflate_spending(ctx):
    from app.api.card.schema import CardCreate
    from app.api.card.service import CardService
    from app.api.transaction.service import TransactionService
    from app.api.dashboard.service import DashboardService
    setup(ctx, 1000)
    target = pocket(ctx, 600)
    card = CardService(ctx).create(CardCreate(pocket_id=target.id, name='QR Expenses', theme='BLUE', initial_balance=100, monthly_limit=600))
    result = TransactionService(ctx).execute(card['id'], 150, 'QR merchant payment', uuid.uuid4())
    assert result['status'] == 'DECLINED'
    assert ctx.raw_card(card['id']).balance == 100
    assert ctx.raw_pocket(target.id).remaining_amount == 600
    assert DashboardService(ctx).spending()['total_spent'] == 0


@pytest.fixture
def settings_card(ctx):
    from app.api.card.schema import CardCreate
    from app.api.card.service import CardService
    setup(ctx, 1000)
    target = pocket(ctx, 600)
    card = CardService(ctx).create(CardCreate(pocket_id=target.id, name='Settings Card', theme='BLUE', initial_balance=600, monthly_limit=600))
    return card['id']


def test_card_settings_save_and_block_payment(ctx, settings_card):
    from datetime import timedelta
    from app.shared.utils import local_time, now_utc
    from app.api.card.schema import CardUpdate
    from app.api.card.service import CardService
    from app.api.transaction.service import TransactionService
    expiry = local_time(now_utc()).date() + timedelta(days=90)
    service = CardService(ctx)
    saved = service.update(settings_card, CardUpdate(expires_on=expiry, usage_type='SUBSCRIPTION', blocked=True))
    assert saved['expires_on'] == expiry
    assert saved['expiry_month'] == expiry.month
    assert saved['usage_type'] == 'SUBSCRIPTION'
    assert saved['status'] == 'NONACTIVE'
    denied = TransactionService(ctx).execute(settings_card, 50, 'Blocked payment', uuid.uuid4())
    assert denied['status'] == 'DECLINED'
    assert ctx.raw_card(settings_card).balance == 600
    service.update(settings_card, CardUpdate(blocked=False))
    for _ in range(2):
        assert TransactionService(ctx).execute(settings_card, 50, 'Subscription payment', uuid.uuid4())['status'] == 'APPROVED'
    assert ctx.raw_card(settings_card).balance == 500


def test_single_use_consumed_only_after_success_and_retry_is_safe(ctx, settings_card):
    from app.api.card.schema import CardUpdate
    from app.api.card.service import CardService
    from app.api.transaction.service import TransactionService
    service = CardService(ctx)
    service.update(settings_card, CardUpdate(usage_type='SINGLE_USE'))
    tx = TransactionService(ctx)
    assert tx.execute(settings_card, 601, 'Insufficient balance', uuid.uuid4())['status'] == 'DECLINED'
    assert ctx.raw_card(settings_card).single_use_consumed_at is None
    key = uuid.uuid4()
    first = tx.execute(settings_card, 50, 'Single use', key)
    assert first['status'] == 'APPROVED'
    assert tx.execute(settings_card, 50, 'Single use', key)['id'] == first['id']
    assert tx.execute(settings_card, 50, 'Second payment', uuid.uuid4())['status'] == 'DECLINED'
    assert ctx.raw_card(settings_card).balance == 550
    with pytest.raises(HTTPException):
        service.update(settings_card, CardUpdate(usage_type='LONG_TERM'))
    service.update(settings_card, CardUpdate(blocked=True))
    service.update(settings_card, CardUpdate(blocked=False))
    assert tx.execute(settings_card, 50, 'Unblocked used card', uuid.uuid4())['status'] == 'DECLINED'


def test_exact_expiry_date_blocks_after_date_but_allows_today(ctx, settings_card):
    from datetime import timedelta
    from app.shared.utils import local_time, now_utc
    from app.api.card.schema import CardUpdate
    from app.api.card.service import CardService
    from app.api.transaction.service import TransactionService
    today = local_time(now_utc()).date()
    service = CardService(ctx)
    service.update(settings_card, CardUpdate(expires_on=today))
    assert TransactionService(ctx).execute(settings_card, 50, 'Today payment', uuid.uuid4())['status'] == 'APPROVED'
    row = ctx.raw_card(settings_card)
    row.expires_on = today - timedelta(days=1)
    ctx.db.commit()
    assert TransactionService(ctx).execute(settings_card, 50, 'Expired payment', uuid.uuid4())['status'] == 'DECLINED'
    assert ctx.raw_card(settings_card).balance == 550
    with pytest.raises(HTTPException):
        service.update(settings_card, CardUpdate(expires_on=today - timedelta(days=1)))


def test_card_settings_are_owner_only_and_validate_type(ctx, settings_card):
    from app.api.card.schema import CardUpdate
    from app.api.card.service import CardService
    from pydantic import ValidationError
    employee = User(email='settings@example.test', username='employee', phone_number='0800000009', password_hash='test', role=UserRole.EMPLOYEE, employer_id=ctx.owner_id)
    ctx.db.add(employee); ctx.db.flush()
    with pytest.raises(HTTPException) as error:
        CardService(Context(ctx.db, employee)).update(settings_card, CardUpdate(blocked=True))
    assert error.value.status_code == 403
    with pytest.raises(ValidationError):
        CardUpdate(usage_type='INVALID')


@pytest.mark.parametrize('account_type,provider', [('BANK', 'Bank BCA'), ('E_WALLET', 'GoPay')])
def test_simulated_account_starts_with_twenty_million_and_edit_preserves_balance(ctx, account_type, provider):
    service = FundingService(ctx)
    payload = FundAccountInput(account_type=account_type, provider_name=provider, account_number='123456', account_holder='Owner')
    assert service.save(payload)['balance'] == 20_000_000
    assert service.history()[0]['amount'] == 20_000_000
    target = pocket(ctx, amount=2_000_000)
    assert service.get()['balance'] == 20_000_000
    assert target.remaining_amount == 2_000_000
    assert service.save(payload)['balance'] == 20_000_000
