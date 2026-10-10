import uuid
from decimal import Decimal

import pytest
from fastapi import HTTPException
from sqlalchemy import select, func

from app.api.funding.service import FundingService
from app.api.request.model import FundRequest
from app.api.request.schema import ReceiptSubmit, ReceiptReview
from app.api.request.service import FundRequestService
from app.api.transaction.service import TransactionService
from test_main_funding import ctx, settings_card
from test_request_categories import employee_context
from test_purchase_workflow import document


@pytest.mark.parametrize('method,recipient', [
    ('QRIS', None), ('BANK_TRANSFER', 'BCA 123456'), ('E_WALLET', 'GoPay 08123456'),
])
def test_direct_payment_needs_no_approval_and_creates_one_receipt(ctx, settings_card, method, recipient):
    employee = employee_context(ctx, ctx.raw_card(settings_card).pocket_id)
    key = uuid.uuid4()
    args = dict(card_id=settings_card, amount=50, description='Buy supplies',
                key=key, category='Marketing', payment_method=method, recipient_account=recipient)
    service = TransactionService(employee)
    result = service.execute(**args)
    assert result['status'] == 'APPROVED'
    assert result['payment_method'] == method and result['recipient_account'] == recipient
    record = ctx.db.get(FundRequest, result['fund_request_id'])
    assert record.requester_id == employee.user.id
    assert record.receipt_status == 'AWAITING_RECEIPT'
    assert record.paid_at and record.receipt_due_at and record.reviewed_by is None
    assert (record.receipt_due_at - record.paid_at).total_seconds() == 48 * 3600
    assert service.execute(**args)['id'] == result['id']
    assert ctx.db.scalar(select(func.count()).select_from(FundRequest)) == 1
    assert FundingService(ctx).get()['balance'] == 950
    receipt = document(ctx, employee, 'receipt.png')
    FundRequestService(employee).submit_receipt(record.id, ReceiptSubmit(document_id=receipt.id))
    assert record.receipt_status == 'RECEIPT_SUBMITTED'
    FundRequestService(ctx).review_receipt(record.id, ReceiptReview(decision='VERIFIED'))
    assert record.status.value == 'COMPLETED'
    assert FundingService(ctx).get()['balance'] == 950


@pytest.mark.parametrize('restriction', ['category', 'budget', 'monthly'])
def test_direct_payment_still_enforces_card_policy(ctx, settings_card, restriction):
    card = ctx.raw_card(settings_card)
    card.allowed_categories = ['Marketing']
    employee = employee_context(ctx, card.pocket_id)
    category = 'Salary' if restriction == 'category' else 'Marketing'
    if restriction == 'budget': card.balance = Decimal(20)
    if restriction == 'monthly': card.monthly_limit = Decimal(20)
    ctx.db.commit()
    result = TransactionService(employee).execute(settings_card, 50, 'Supplies', uuid.uuid4(), category=category)
    assert result['status'] == 'DECLINED'
    assert FundingService(ctx).get()['balance'] == 1000
    assert ctx.db.scalar(select(func.count()).select_from(FundRequest)) == 0


def test_direct_bank_payment_requires_destination(ctx, settings_card):
    employee = employee_context(ctx, ctx.raw_card(settings_card).pocket_id)
    with pytest.raises(HTTPException):
        TransactionService(employee).execute(settings_card, 50, 'Supplies', uuid.uuid4(),
            category='Marketing', payment_method='BANK_TRANSFER', recipient_account='  ')
    assert FundingService(ctx).get()['balance'] == 1000
