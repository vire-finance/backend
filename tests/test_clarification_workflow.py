from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select

from app.api.funding.service import FundingService
from app.api.request.model import RequestEvent
from app.api.request.schema import ClarificationMessage, RequestApprove, RequestReject, RequestSubmit
from app.api.request.service import FundRequestService
from app.api.transaction.model import Transaction
from test_main_funding import ctx, settings_card
from test_purchase_workflow import purchase, document
from test_e2e_authorization import client, headers


def test_clarification_round_trip_history_and_payment_once(ctx, settings_card):
    employee, row = purchase(ctx, settings_card)
    owner = FundRequestService(ctx)
    requester = FundRequestService(employee)
    for question in ('Please explain the price', 'Please confirm delivery'):
        key = uuid4()
        payload = ClarificationMessage(message=question)
        assert owner.clarify(row.id, payload, key)['workflow_status'] == 'NEEDS_CLARIFICATION'
        owner.clarify(row.id, payload, key)
        with pytest.raises(HTTPException):
            owner.clarify(row.id, ClarificationMessage(message='Changed message'), key)
        with pytest.raises(HTTPException):
            requester.submit(row.id, RequestSubmit())
        doc = document(ctx, employee, str(uuid4()) + '.png')
        requester.attach(row.id, doc.id)
        reply_key = uuid4()
        requester.reply(row.id, ClarificationMessage(message='Confirmed with supporting evidence'), reply_key)
        requester.reply(row.id, ClarificationMessage(message='Confirmed with supporting evidence'), reply_key)
        assert requester.submit(row.id, RequestSubmit())['status'] == 'PENDING_APPROVAL'
        requester.submit(row.id, RequestSubmit())
    approval = RequestApprove(card_id=settings_card, message='Thank you; approved.')
    key = uuid4()
    first = owner.approve(row.id, approval, key)
    assert first['status'] == 'APPROVED'
    assert owner.approve(row.id, approval, key)['id'] == first['id']
    with pytest.raises(HTTPException):
        owner.approve(row.id, RequestApprove(card_id=settings_card, message='Different'), key)
    events = owner.detail(row.id)['history']
    assert [event['event_type'] for event in events] == ['SUBMITTED',
        'CLARIFICATION_REQUESTED', 'CLARIFICATION_REPLY', 'RESUBMITTED',
        'CLARIFICATION_REQUESTED', 'CLARIFICATION_REPLY', 'RESUBMITTED', 'APPROVED']
    assert events[-1]['message'] == 'Thank you; approved.'
    assert FundingService(ctx).get()['balance'] == 950
    assert ctx.db.scalar(select(func.count(Transaction.id))) == 1
    with pytest.raises(HTTPException):
        owner.clarify(row.id, ClarificationMessage(message='Late question'), uuid4())


def test_clarification_permissions_pin_and_missing_invoice(client, ctx, settings_card):
    employee, row = purchase(ctx, settings_card)
    path = f'/fund-requests/{row.id}/clarification'
    payload = {'message': 'Please clarify'}
    assert client.post(path, json=payload).status_code == 401
    assert client.post(path, headers=headers(ctx.db, employee.user), json=payload).status_code == 403
    owner_headers = headers(ctx.db, ctx.user)
    missing_pin = {k: v for k, v in owner_headers.items() if k != 'X-Action-Token'}
    assert client.post(path, headers=missing_pin, json=payload).status_code == 401
    assert client.post(path, headers=owner_headers, json={'message': '  '}).status_code == 422
    assert client.post(path, headers=owner_headers, json=payload).status_code == 200
    requester = FundRequestService(employee)
    for doc in requester.repo.documents(row.id):
        requester.attach(row.id, doc.id, remove=True)
    requester.reply(row.id, ClarificationMessage(message='Reply without invoice'), uuid4())
    with pytest.raises(HTTPException, match='Invoice'):
        requester.submit(row.id, RequestSubmit())
    assert row.status.value == 'NEEDS_CLARIFICATION'
    assert FundingService(ctx).get()['balance'] == 1000
    assert client.post(path + '/reply', headers=headers(ctx.db, ctx.user), json=payload).status_code == 403


def test_reject_without_message_and_invalid_followup(ctx, settings_card):
    _, row = purchase(ctx, settings_card)
    service = FundRequestService(ctx)
    assert service.reject(row.id, RequestReject())['status'] == 'REJECTED'
    with pytest.raises(HTTPException):
        service.reject(row.id, RequestReject())
    with pytest.raises(HTTPException):
        service.clarify(row.id, ClarificationMessage(message='Too late'), uuid4())
    assert ctx.db.scalar(select(func.count(RequestEvent.id)).where(RequestEvent.event_type == 'REJECTED')) == 1
