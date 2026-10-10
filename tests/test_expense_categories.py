import uuid
import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from app.api.card.schema import CardUpdate
from app.api.card.service import CardService
from app.api.dashboard.service import DashboardService
from app.api.history.service import HistoryService
from app.api.transaction.schema import TransactionCreate
from app.api.transaction.service import TransactionService
from test_main_funding import ctx, settings_card

CATEGORIES = ['Salary', 'Operational', 'Production', 'Marketing', 'Emergency', 'Administration & Tax', 'Others']


def test_all_categories_persist_and_spending_totals_match(ctx, settings_card):
    tx = TransactionService(ctx)
    expected = {}
    for index, category in enumerate(CATEGORIES, start=1):
        amount = index * 10
        result = tx.execute(settings_card, amount, category + ' payment', uuid.uuid4(), category=category)
        assert result['status'] == 'APPROVED'
        assert result['category'] == category
        assert HistoryService(ctx).detail(result['id'])['category'] == category
        expected[category] = amount
    denied = tx.execute(settings_card, 999, 'Insufficient balance', uuid.uuid4(), category='Salary')
    assert denied['status'] == 'DECLINED'
    spending = DashboardService(ctx).spending()
    assert spending['total_spent'] == sum(expected.values())
    assert {row['category']: row['amount'] for row in spending['categories']} == expected
    for row in spending['categories']:
        assert row['percentage'] == round(expected[row['category']] * 100 / sum(expected.values()), 2)
    assert len(HistoryService(ctx).list(category='Marketing')['items']) == 1


def test_category_is_snapshot_and_retry_does_not_duplicate_or_change_it(ctx, settings_card):
    tx = TransactionService(ctx)
    key = uuid.uuid4()
    first = tx.execute(settings_card, 50, 'Expense', key, category='Salary')
    assert tx.execute(settings_card, 50, 'Expense', key, category='Salary')['id'] == first['id']
    with pytest.raises(HTTPException) as error:
        tx.execute(settings_card, 50, 'Expense', key, category='Marketing')
    assert error.value.status_code == 409
    CardService(ctx).update(settings_card, CardUpdate(category='Updated card category'))
    assert HistoryService(ctx).detail(first['id'])['category'] == 'Salary'
    assert DashboardService(ctx).spending()['categories'][0]['category'] == 'Salary'
    assert ctx.raw_card(settings_card).balance == 550


def test_api_category_validation_and_legacy_default(settings_card):
    for category in CATEGORIES:
        assert TransactionCreate(card_id=settings_card, amount=10, description='Payment', category=category).category == category
    with pytest.raises(ValidationError):
        TransactionCreate(card_id=settings_card, amount=10, description='Payment', category='Invalid')
    assert TransactionCreate(card_id=settings_card, amount=10, description='Payment').category == 'Others'


def test_ai_analytics_uses_transaction_category(ctx, settings_card):
    from app.api.ai.analytics import AnalyticsService
    from app.api.ai.repository import AnalyticsRepository
    from app.api.ai.periods import resolve_period
    from app.shared.utils import now_utc
    TransactionService(ctx).execute(settings_card, 50, 'Expense', uuid.uuid4(), category='Marketing')
    now = now_utc()
    response = AnalyticsService(ctx)._calculate(AnalyticsRepository(ctx), resolve_period(now=now), now)
    assert response.category_breakdown[0].category == 'Marketing'
    assert response.category_breakdown[0].amount == 50
