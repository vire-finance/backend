import uuid
import pytest
from pydantic import ValidationError
from app.api.card.schema import CardCreate, CardUpdate
from app.api.card.service import CardService
from app.api.auth.model import User
from app.api.funding.service import FundingService
from app.api.transaction.service import TransactionService
from app.api.dashboard.service import DashboardService
from app.core.context import Context
from app.shared.enums import UserRole
from test_main_funding import ctx, settings_card


def test_allowed_categories_saved_on_create_and_reload(ctx, settings_card):
    pocket = ctx.raw_card(settings_card).pocket_id
    service = CardService(ctx)
    created = service.create(CardCreate(pocket_id=pocket, name='Advertising', theme='BLUE', initial_balance=100, monthly_limit=100, allowed_categories=['Marketing', 'Operational']))
    assert service.detail(created['id'])['allowed_categories'] == ['Marketing', 'Operational']
    assert service.list()[0]['allowed_categories']


def test_employee_payment_cannot_bypass_card_categories_or_debit_balance(ctx, settings_card):
    service = CardService(ctx)
    service.update(settings_card, CardUpdate(allowed_categories=['Marketing', 'Operational']))
    employee = User(email='policy@example.test', username='policy', phone_number='0800000001', password_hash='test', role=UserRole.EMPLOYEE, employer_id=ctx.owner_id)
    ctx.db.add(employee); ctx.db.flush()
    service.access(settings_card, employee.id, True)
    tx = TransactionService(Context(ctx.db, employee))
    denied = tx.execute(settings_card, 50, 'Wrong category', uuid.uuid4(), category='Salary')
    assert denied['status'] == 'DECLINED'
    assert FundingService(ctx).get()['balance'] == 1000
    service.update(settings_card,CardUpdate(allowed_categories=['Operational']))
    denied = tx.execute(settings_card, 50, 'Allowed advertising', uuid.uuid4(), category='Marketing')
    assert denied['status'] == 'DECLINED'
    assert FundingService(ctx).get()['balance'] == 1000
    service.update(settings_card,CardUpdate(allowed_categories=['Marketing','Operational']))
    key=uuid.uuid4()
    paid=tx.execute(settings_card,50,'Allowed advertising',key,category='Marketing')
    assert paid['status']=='APPROVED'
    assert tx.execute(settings_card,50,'Allowed advertising',key,category='Marketing')['id']==paid['id']
    assert FundingService(ctx).get()['balance'] == 950
    assert DashboardService(ctx).spending()['categories'][0]['category'] == 'Marketing'


@pytest.mark.parametrize('categories', [[], ['Invalid'], ['Marketing', 'Marketing'], None])
def test_invalid_card_category_policy_rejected(categories):
    with pytest.raises(ValidationError):
        CardCreate(pocket_id=uuid.uuid4(), name='Card', theme='BLUE', initial_balance=100, monthly_limit=100, allowed_categories=categories)
    with pytest.raises(ValidationError):
        CardUpdate(allowed_categories=categories)
