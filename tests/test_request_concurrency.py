"""Committed, independent PostgreSQL connections exercise transition races."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
from uuid import uuid4

from alembic import command
from alembic.config import Config
from fastapi import HTTPException
import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from app.api.auth.model import User
from app.api.card.schema import CardCreate
from app.api.card.service import CardService
from app.api.funding.model import FundAccount
from app.api.request.model import FundRequest, RequestEvent
from app.api.request.schema import ClarificationMessage, RequestApprove
from app.api.request.service import FundRequestService
from app.api.transaction.model import Transaction
from app.core.config import settings
from app.core.context import Context
from app.shared.enums import UserRole
from test_main_funding import setup, pocket
from test_purchase_workflow import purchase
from test_ocr_enum_migration import scratch_database


@pytest.fixture
def committed_request(scratch_database, monkeypatch):
    config = Config()
    config.set_main_option('script_location', str(Path(__file__).resolve().parents[1] / 'alembic'))
    monkeypatch.setattr(settings, 'DATABASE_URL', scratch_database.render_as_string(hide_password=False))
    command.upgrade(config, 'head')
    engine = create_engine(scratch_database, hide_parameters=True)
    factory = sessionmaker(engine, expire_on_commit=False)
    try:
        with factory() as db:
            user = User(email=f'{uuid4()}@example.com', username='Owner', phone_number='0800000000', password_hash='test', role=UserRole.OWNER)
            db.add(user); db.commit()
            ctx = Context(db, user)
            setup(ctx, 1000)
            target = pocket(ctx, 600)
            card = CardService(ctx).create(CardCreate(pocket_id=target.id, name='Race card', theme='BLUE', initial_balance=600, monthly_limit=600))
            employee, request = purchase(ctx, card['id'])
            employee.user.email = f'{uuid4()}@example.com'
            for doc in FundRequestService(ctx).repo.documents(request.id):
                doc.stored_filename = f'{uuid4()}.png'
            db.commit()
            yield factory, user.id, card['id'], request.id
    finally:
        engine.dispose()


@pytest.mark.parametrize('mode', ['duplicate', 'clarify_vs_approve'])
def test_concurrent_reviews_have_one_consistent_financial_outcome(committed_request, mode):
    factory, owner_id, card_id, request_id = committed_request
    barrier = Barrier(2)
    key = uuid4()
    def act(index):
        with factory() as db:
            ctx = Context(db, db.get(User, owner_id))
            barrier.wait(timeout=10)
            ctx.lock()  # Same business lock acquired by HTTP get_context.
            try:
                service = FundRequestService(ctx)
                if index == 1 and mode == 'clarify_vs_approve':
                    return service.clarify(request_id, ClarificationMessage(message='More evidence'), uuid4())
                return service.approve(request_id, RequestApprove(card_id=card_id), key)
            except HTTPException as error:
                db.rollback()
                assert error.status_code == 409
                return None
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(act, range(2)))
    with factory() as db:
        request = db.get(FundRequest, request_id)
        approved = db.scalar(select(func.count(Transaction.id)).where(Transaction.fund_request_id == request_id, Transaction.status == 'APPROVED'))
        balance = db.scalar(select(FundAccount.balance).where(FundAccount.owner_id == owner_id))
        assert approved in (0, 1)
        assert balance == 1000 - approved * 50
        assert request.status.value == ('COMPLETED' if approved else 'NEEDS_CLARIFICATION')
        if mode == 'duplicate':
            assert approved == 1 and results[0]['id'] == results[1]['id']
            assert db.scalar(select(func.count(RequestEvent.id)).where(RequestEvent.request_id == request_id, RequestEvent.event_type == 'APPROVED')) == 1
