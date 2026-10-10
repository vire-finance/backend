import uuid
from datetime import timedelta, datetime, timezone
from decimal import Decimal
import pytest
from pydantic import ValidationError
from app.core.context import Context
from app.api.auth.model import User
from app.api.pocket.model import PocketAccess
from app.api.ocr.model import OCRDocument
from app.api.request.schema import FundRequestInput, RequestSubmit, RequestApprove
from app.api.request.service import FundRequestService
from app.api.dashboard.service import DashboardService
from app.api.ai.analytics import AnalyticsService
from app.api.ai.repository import AnalyticsRepository
from app.api.ai.periods import resolve_period
from app.shared.enums import UserRole, OCRStatus
from app.shared.utils import now_utc
from test_main_funding import ctx, settings_card

CATEGORIES = ['Operational', 'Production', 'Marketing', 'Emergency', 'Administration & Tax', 'Others']


def employee_context(ctx, pocket_id):
    employee = User(email='request@example.test', username='employee', phone_number='0800000001', password_hash='test', role=UserRole.EMPLOYEE, employer_id=ctx.user.id)
    ctx.db.add(employee)
    ctx.db.flush()
    ctx.db.add(PocketAccess(pocket_id=pocket_id, employee_id=employee.id))
    ctx.db.flush()
    return Context(ctx.db, employee)


@pytest.mark.parametrize('category', CATEGORIES)
def test_request_category_reaches_transaction_dashboard_and_ai(ctx, settings_card, category):
    card = ctx.raw_card(settings_card)
    card.category = 'Salary'
    employee = employee_context(ctx, card.pocket_id)
    service = FundRequestService(employee)
    draft = service.save(FundRequestInput(pocket_id=card.pocket_id, card_id=card.id, payment_executor='OWNER_PAYMENT', recipient_account='QR merchant', explanation='Employee wrote this reason', request_type='PURCHASE', category=category, party_name='Merchant', total_amount=50, needed_by=datetime.now(timezone.utc) + timedelta(days=1)))
    assert draft['category'] == category
    doc=OCRDocument(user_id=employee.user.id,original_filename='invoice.png',stored_filename='invoice.png',storage_path='invoice.png',mime_type='image/png',file_size=100,ocr_status=OCRStatus.COMPLETED)
    ctx.db.add(doc);ctx.db.flush()
    service.attach(draft['id'],doc.id)
    service.submit(draft['id'], RequestSubmit())
    owner = FundRequestService(ctx)
    key = uuid.uuid4()
    result = owner.approve(draft['id'], RequestApprove(card_id=card.id), key)
    assert result['status'] == 'APPROVED'
    assert result['category'] == category
    assert ctx.raw_card(card.id).balance == 600
    from app.api.funding.service import FundingService
    assert FundingService(ctx).get()['balance'] == 950
    spending = DashboardService(ctx).spending()
    assert spending['total_spent'] == 50
    assert spending['categories'][0]['category'] == category
    now = now_utc()
    analysis = AnalyticsService(ctx)._calculate(AnalyticsRepository(ctx), resolve_period(now=now), now)
    assert analysis.category_breakdown[0].category == category
    assert analysis.category_breakdown[0].amount == 50


def test_ocr_prefill_leaves_explanation_for_user(ctx, settings_card):
    employee = employee_context(ctx, ctx.raw_card(settings_card).pocket_id)
    doc = OCRDocument(user_id=employee.user.id, original_filename='invoice.png', stored_filename='test.png', storage_path='test.png', mime_type='image/png', file_size=100, ocr_status=OCRStatus.COMPLETED, raw_ocr_text='Merchant\nA street address\nTotal Rp27.000', extracted_other_party_name='Merchant', extracted_total_amount=Decimal('27000'))
    ctx.db.add(doc)
    ctx.db.flush()
    result = FundRequestService(employee).prefill(doc.id)
    assert result['explanation'] is None
    assert result['party_name'] == 'Merchant'
    assert result['total_amount'] == 27000


@pytest.mark.parametrize('category', ['Not a category'])
def test_invalid_request_category_rejected(category):
    with pytest.raises(ValidationError):
        FundRequestInput(pocket_id=uuid.uuid4(), explanation='Reason', request_type='PURCHASE', category=category, party_name='Merchant', total_amount=50, needed_by=datetime.now(timezone.utc) + timedelta(days=1))
