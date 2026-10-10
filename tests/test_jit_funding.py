import uuid
import pytest
from app.api.funding.service import FundingService
from app.api.card.schema import CardUpdate
from app.api.card.service import CardService
from app.api.transaction.service import TransactionService
from app.api.dashboard.service import DashboardService
from app.api.auth.model import User
from app.api.pocket.model import PocketAccess
from app.core.context import Context
from app.shared.enums import UserRole
from test_main_funding import ctx, settings_card


def test_success_debits_main_once_and_dashboard_does_not_count_budget_twice(ctx, settings_card):
    funding = FundingService(ctx)
    assert funding.get()['balance'] == 1000
    assert DashboardService(ctx).summary()['total_balance'] == 1000
    key = uuid.uuid4()
    tx = TransactionService(ctx)
    first = tx.execute(settings_card, 50, 'Valid QR expense', key, category='Marketing')
    assert first['status'] == 'APPROVED'
    assert tx.execute(settings_card, 50, 'Valid QR expense', key, category='Marketing')['id'] == first['id']
    assert funding.get()['balance'] == 950
    assert DashboardService(ctx).summary()['total_balance'] == 950
    debits = [row for row in funding.history() if row['kind'] == 'PAYMENT']
    assert len(debits) == 1
    assert debits[0]['amount'] == 50
    assert debits[0]['balance_after'] == 950


def test_cash_shortage_declines_without_consuming_card_or_pocket_budget(ctx, settings_card):
    funding = FundingService(ctx)
    funding.account().balance = 25
    ctx.db.commit()
    card = ctx.raw_card(settings_card)
    result = TransactionService(ctx).execute(settings_card, 50, 'Too much for main account', uuid.uuid4())
    assert result['status'] == 'DECLINED'
    assert 'Saldo utama' in result['failure_reason']
    assert funding.get()['balance'] == 25
    assert ctx.raw_card(settings_card).balance == 600
    assert ctx.raw_pocket(card.pocket_id).remaining_amount == 600
    assert not any(row['kind'] == 'PAYMENT' for row in funding.history())


def test_monthly_limit_decline_does_not_debit_main_cash(ctx, settings_card):
    CardService(ctx).update(settings_card, CardUpdate(monthly_limit=20))
    result = TransactionService(ctx).execute(settings_card, 50, 'Over monthly limit', uuid.uuid4())
    assert result['status'] == 'DECLINED'
    assert FundingService(ctx).get()['balance'] == 1000
    assert ctx.raw_card(settings_card).balance == 600


def test_employee_payment_uses_owner_main_account(ctx, settings_card):
    card = ctx.raw_card(settings_card)
    employee = User(email='jit@example.test', username='jit', phone_number='0800000001', password_hash='test', role=UserRole.EMPLOYEE, employer_id=ctx.owner_id)
    ctx.db.add(employee)
    ctx.db.flush()
    ctx.db.add(PocketAccess(pocket_id=card.pocket_id, employee_id=employee.id))
    ctx.db.flush()
    from app.api.request.schema import FundRequestInput, RequestApprove, RequestSubmit
    from app.api.request.service import FundRequestService
    from datetime import datetime, timezone, timedelta
    employee_ctx = Context(ctx.db, employee)
    service = FundRequestService(employee_ctx)
    request = service.save(FundRequestInput(pocket_id=card.pocket_id, card_id=card.id, payment_executor='EMPLOYEE_PAYMENT', explanation='Employee QR', category='Others', request_type='PURCHASE', party_name='Merchant', total_amount=50, needed_by=datetime.now(timezone.utc)+timedelta(days=1)))
    service.submit(request['id'],RequestSubmit())
    FundRequestService(ctx).approve(request['id'],RequestApprove(card_id=card.id),uuid.uuid4())
    result = TransactionService(employee_ctx).execute(settings_card, 50, 'Employee QR', uuid.uuid4(), request_id=request['id'])
    assert result['status'] == 'APPROVED'
    assert FundingService(ctx).get()['balance'] == 950


def test_technical_failure_after_debit_rolls_back_all_balances(ctx, settings_card, monkeypatch):
    from app.api.funding.service import FundingService
    original = FundingService.record
    def fail_payment_record(self, account, kind, amount, **kwargs):
        if kind == 'PAYMENT':
            raise RuntimeError('Injected ledger failure')
        return original(self, account, kind, amount, **kwargs)
    monkeypatch.setattr(FundingService, 'record', fail_payment_record)
    result = TransactionService(ctx).execute(settings_card, 50, 'Failed payment', uuid.uuid4())
    assert result['status'] == 'FAILED'
    assert FundingService(ctx).get()['balance'] == 1000
    assert ctx.raw_card(settings_card).balance == 600
    assert ctx.raw_pocket(ctx.raw_card(settings_card).pocket_id).remaining_amount == 600
