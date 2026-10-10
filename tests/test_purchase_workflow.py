import uuid
from datetime import datetime, timezone, timedelta
from decimal import Decimal
import pytest
from fastapi import HTTPException
from sqlalchemy import select
from app.api.request.model import FundRequest
from app.api.request.schema import FundRequestInput, RequestSubmit, RequestApprove, RequestReject, ReceiptSubmit, ReceiptReview
from app.api.request.service import FundRequestService
from app.api.transaction.service import TransactionService
from app.api.notification.model import Notification
from app.api.notification.worker import overdue_reminders
from app.api.funding.service import FundingService
from app.api.ocr.model import OCRDocument
from app.shared.enums import OCRStatus
from test_main_funding import ctx, settings_card
from test_request_categories import employee_context

def document(ctx, employee, name="invoice.png"):
    doc=OCRDocument(user_id=employee.user.id, original_filename=name, stored_filename=name, storage_path=name, mime_type="image/png", file_size=100, ocr_status=OCRStatus.PROCESSING)
    ctx.db.add(doc);ctx.db.flush();return doc

def purchase(ctx, card_id, executor="EMPLOYEE_PAYMENT", category="Marketing", amount=50):
    card=ctx.raw_card(card_id);employee=employee_context(ctx, card.pocket_id)
    service=FundRequestService(employee)
    draft=service.save(FundRequestInput(pocket_id=card.pocket_id, card_id=card.id, payment_executor=executor, category=category, explanation="Buy business materials", request_type="PURCHASE", party_name="Merchant", total_amount=amount, payment_method="BANK_TRANSFER", recipient_account="BCA 123456", needed_by=datetime.now(timezone.utc)+timedelta(days=1)))
    if executor=="OWNER_PAYMENT":service.attach(draft['id'],document(ctx,employee).id)
    service.submit(draft['id'],RequestSubmit())
    return employee,ctx.db.get(FundRequest,draft['id'])

def pay(employee, request, key=None, **overrides):
    args=dict(card_id=request.card_id, amount=int(request.total_amount), description=request.explanation[:255], key=key or uuid.uuid4(), request_id=request.id, category=request.category, payment_method=request.payment_method, recipient_account=request.recipient_account)
    args.update(overrides);return TransactionService(employee).execute(**args)

def test_employee_approval_does_not_debit_and_payment_is_once(ctx, settings_card):
    employee,request=purchase(ctx,settings_card)
    assert pay(employee,request)['status']=='DECLINED'
    FundRequestService(ctx).approve(request.id,RequestApprove(card_id=settings_card),uuid.uuid4())
    assert FundingService(ctx).get()['balance']==1000
    assert request.receipt_status=='AWAITING_PAYMENT'
    key=uuid.uuid4();first=pay(employee,request,key)
    assert first['status']=='APPROVED'
    assert pay(employee,request,key)['id']==first['id']
    assert pay(employee,request)['status']=='DECLINED'
    assert FundingService(ctx).get()['balance']==950
    assert ctx.raw_card(settings_card).balance==550
    assert request.receipt_status=='AWAITING_RECEIPT'
    assert request.completed_at is None

def test_owner_invoice_payment_can_exceed_card_budget_and_category(ctx, settings_card):
    card=ctx.raw_card(settings_card);card.allowed_categories=['Marketing']
    employee,request=purchase(ctx,settings_card,'OWNER_PAYMENT','Salary',800)
    with pytest.raises(HTTPException):pay(employee,request)
    owner=FundRequestService(ctx);key=uuid.uuid4()
    first=owner.approve(request.id,RequestApprove(card_id=settings_card),key)
    assert first['status']=='APPROVED'
    assert owner.approve(request.id,RequestApprove(card_id=settings_card),key)['id']==first['id']
    assert FundingService(ctx).get()['balance']==200
    assert ctx.raw_card(settings_card).balance==600
    assert ctx.raw_pocket(card.pocket_id).remaining_amount==600
    assert first['category']=='Salary'
    assert request.status.value=='COMPLETED' and request.receipt_status=='INVOICE_PAID'
    assert request.receipt_due_at is None

def test_owner_request_invoice_is_mandatory(ctx, settings_card):
    card=ctx.raw_card(settings_card);employee=employee_context(ctx,card.pocket_id)
    service=FundRequestService(employee)
    request=service.save(FundRequestInput(pocket_id=card.pocket_id,card_id=card.id,payment_executor='OWNER_PAYMENT',explanation='Invoice required',request_type='PURCHASE',party_name='Merchant',total_amount=50,recipient_account='BCA 123',needed_by=datetime.now(timezone.utc)+timedelta(days=1)))
    with pytest.raises(HTTPException,match='Invoice'):service.submit(request['id'],RequestSubmit())
    assert FundingService(ctx).get()['balance']==1000

def test_rejected_request_and_changed_payment_details_are_blocked(ctx, settings_card):
    employee,request=purchase(ctx,settings_card)
    owner=FundRequestService(ctx)
    owner.reject(request.id,RequestReject(reason='Not needed'))
    assert pay(employee,request)['status']=='DECLINED'
    assert FundingService(ctx).get()['balance']==1000

@pytest.mark.parametrize('change',[{'category':'Salary'},{'recipient_account':'Other 999'},{'payment_method':'QRIS'},{'description':'Different purchase'},{'amount':51}])
def test_payment_cannot_change_approved_details(ctx, settings_card, change):
    employee,request=purchase(ctx,settings_card)
    FundRequestService(ctx).approve(request.id,RequestApprove(card_id=settings_card),uuid.uuid4())
    try:result=pay(employee,request,**change);assert result['status']=='DECLINED'
    except HTTPException:pass
    assert FundingService(ctx).get()['balance']==1000

def test_receipt_overdue_notifies_once_and_review_closes_without_new_debit(ctx, settings_card):
    employee,request=purchase(ctx,settings_card)
    owner=FundRequestService(ctx);owner.approve(request.id,RequestApprove(card_id=settings_card),uuid.uuid4());pay(employee,request)
    request.receipt_due_at=datetime.utcnow()-timedelta(hours=1);ctx.db.commit()
    overdue_reminders(ctx.db);overdue_reminders(ctx.db)
    notifications=ctx.db.scalars(select(Notification).where(Notification.request_id==request.id,Notification.title=='Receipt overdue')).all()
    assert len(notifications)==2
    assert owner.detail(request.id)['workflow_status']=='RECEIPT_OVERDUE'
    receipt=document(ctx,employee,'receipt.png')
    FundRequestService(employee).submit_receipt(request.id,ReceiptSubmit(document_id=receipt.id,note='Materials purchased'))
    owner.review_receipt(request.id,ReceiptReview(decision='NEEDS_CLARIFICATION',reason='Show item details'))
    assert request.completed_at is None and request.receipt_status=='NEEDS_CLARIFICATION'
    FundRequestService(employee).submit_receipt(request.id,ReceiptSubmit(document_id=receipt.id,note='Updated item details'))
    owner.review_receipt(request.id,ReceiptReview(decision='VERIFIED'))
    assert request.status.value=='COMPLETED' and request.completed_at is not None
    assert FundingService(ctx).get()['balance']==950
    with pytest.raises(HTTPException):FundRequestService(employee).submit_receipt(request.id,ReceiptSubmit(document_id=receipt.id))

def test_receipt_before_payment_is_blocked(ctx, settings_card):
    employee,request=purchase(ctx,settings_card)
    with pytest.raises(HTTPException):FundRequestService(employee).submit_receipt(request.id,ReceiptSubmit(document_id=document(ctx,employee).id))

def test_owner_payment_cash_shortage_keeps_request_pending(ctx, settings_card):
    employee,request=purchase(ctx,settings_card,'OWNER_PAYMENT')
    FundingService(ctx).account().balance=25;ctx.db.commit()
    result=FundRequestService(ctx).approve(request.id,RequestApprove(card_id=settings_card),uuid.uuid4())
    assert result['status']=='DECLINED'
    assert request.status.value=='PENDING_APPROVAL' and request.paid_at is None
    assert FundingService(ctx).get()['balance']==25

def test_employee_request_over_monthly_limit_requires_owner_invoice_route(ctx, settings_card):
    card=ctx.raw_card(settings_card);card.monthly_limit=20
    with pytest.raises(HTTPException):purchase(ctx,settings_card)

def test_owner_invoice_payment_does_not_consume_employee_monthly_budget(ctx, settings_card):
    from app.api.transaction.repository import TransactionRepository
    employee,request=purchase(ctx,settings_card,'OWNER_PAYMENT')
    FundRequestService(ctx).approve(request.id,RequestApprove(card_id=settings_card),uuid.uuid4())
    assert TransactionRepository(ctx.db).spent(card_id=settings_card)==0
    assert FundingService(ctx).get()['balance']==950

def test_push_outbox_retries_without_blocking_purchase(ctx, settings_card, monkeypatch):
    from app.api.notification.model import PushDevice
    from app.api.notification import worker
    from app.core.config import settings
    from google.oauth2 import service_account
    import requests
    ctx.db.add(PushDevice(user_id=ctx.user.id,token='fake-device-token-123456789'))
    event=Notification(user_id=ctx.user.id,title='Purchase request',message='Review invoice')
    ctx.db.add(event);ctx.db.commit()
    class Credentials:
        valid=True
        token='fake-access-token'
    class Response:
        def __init__(self,ok):self.ok=ok
        def json(self):return {}
    monkeypatch.setattr(worker,'_credentials',Credentials())
    monkeypatch.setattr(settings,'FIREBASE_SERVICE_ACCOUNT','fake.json')
    monkeypatch.setattr(settings,'FIREBASE_PROJECT_ID','demo-project')
    monkeypatch.setattr(requests,'post',lambda *a,**kw:Response(False))
    worker.dispatch(ctx.db)
    assert not event.push_processed and event.push_attempts==1 and event.push_retry_at>datetime.utcnow()
    event.push_retry_at=datetime.utcnow()-timedelta(seconds=1);ctx.db.commit()
    monkeypatch.setattr(requests,'post',lambda *a,**kw:Response(True))
    worker.dispatch(ctx.db)
    assert event.push_processed and event.push_attempts==2
    assert FundingService(ctx).get()['balance']==1000
