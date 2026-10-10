import uuid
from datetime import datetime, timezone, timedelta
import pytest
from fastapi import HTTPException
from app.api.auth.model import User
from app.api.card.service import CardService
from app.api.request.schema import FundRequestInput, RequestSubmit
from app.api.request.service import FundRequestService
from app.core.context import Context
from app.shared.enums import UserRole
from test_main_funding import ctx, settings_card
from test_purchase_workflow import document

def card_only_employee(ctx, card_id):
    user = User(email='card-only@example.test', username='cardonly', phone_number='0812000001',
                password_hash='test', role=UserRole.EMPLOYEE, employer_id=ctx.owner_id)
    ctx.db.add(user); ctx.db.flush()
    CardService(ctx).access(card_id, user.id, True)
    return Context(ctx.db, user)

@pytest.mark.parametrize('revoked', [False, True])
def test_card_only_invoice_access_is_allowed_until_revoked(ctx, settings_card, revoked):
    card = ctx.raw_card(settings_card)
    employee = card_only_employee(ctx, settings_card)
    assert not employee.can_pocket(employee.raw_pocket(card.pocket_id))
    service = FundRequestService(employee)
    draft = service.save(FundRequestInput(pocket_id=card.pocket_id, card_id=card.id,
        payment_executor='OWNER_PAYMENT', payment_method='BANK_TRANSFER', recipient_account='BCA 123456',
        category='Marketing', explanation='Invoice for supplies', party_name='Merchant', total_amount=50,
        request_type='PURCHASE', needed_by=datetime.now(timezone.utc)+timedelta(days=1)))
    invoice = document(ctx, employee)
    if revoked:
        CardService(ctx).access(card.id, employee.user.id, False)
        with pytest.raises(HTTPException) as error: service.attach(draft['id'], invoice.id)
        assert error.value.status_code == 403
        assert not invoice.fund_request_id
    else:
        service.attach(draft['id'], invoice.id)
        service.submit(draft['id'], RequestSubmit())
        assert service.detail(draft['id'])['status'] == 'PENDING_APPROVAL'
