"""Durable notification outbox and overdue reminders; never blocks payments."""
from app.api.notification.service import notification_wakeup
import asyncio
import logging
from datetime import timedelta
from sqlalchemy import select, or_
from app.core.database import SessionLocal
from app.core.config import settings
from app.api.notification.model import Notification, PushDevice
from app.api.request.model import FundRequest
from app.api.pocket.model import Pocket
from app.api.auth.model import User
from app.shared.enums import FundRequestStatus
from app.shared.utils import now_utc
logger = logging.getLogger(__name__)
_credentials = None

def overdue_reminders(db):
    rows = db.scalars(select(FundRequest).where(FundRequest.status == FundRequestStatus.APPROVED, FundRequest.receipt_status.in_(["AWAITING_RECEIPT", "NEEDS_CLARIFICATION"]), FundRequest.receipt_due_at < now_utc(), FundRequest.receipt_overdue_notified.is_(False)).with_for_update(skip_locked=True)).all()
    for row in rows:
        owner_id = db.get(Pocket, row.pocket_id).owner_id
        for user_id in [owner_id, row.requester_id]:
            db.add(Notification(user_id=user_id, request_id=row.id, title="Receipt overdue", message=f"Bukti pembelian {row.party_name} melewati tenggat. Request masih terbuka."))
        row.receipt_overdue_notified = True
    db.commit()

def dispatch(db):
    if not settings.FIREBASE_SERVICE_ACCOUNT or not settings.FIREBASE_PROJECT_ID: return
    from google.oauth2 import service_account
    from google.auth.transport.requests import Request
    import requests
    global _credentials
    if _credentials is None:
        _credentials = service_account.Credentials.from_service_account_file(settings.FIREBASE_SERVICE_ACCOUNT, scopes=["https://www.googleapis.com/auth/firebase.messaging"])
    credentials = _credentials
    if not credentials.valid: credentials.refresh(Request())
    rows = db.scalars(select(Notification).where(Notification.push_processed.is_(False), select(PushDevice.id).where(PushDevice.user_id == Notification.user_id).exists(), or_(Notification.push_retry_at.is_(None), Notification.push_retry_at <= now_utc())).order_by(Notification.created_at).limit(20).with_for_update(skip_locked=True)).all()
    for row in rows:
        user = db.get(User, row.user_id)
        if user is None or user.notification_preferences.get("push") is False:
            row.push_processed = True
            continue
        devices = db.scalars(select(PushDevice).where(PushDevice.user_id == row.user_id)).all()
        if not devices: continue
        success = True
        for device in devices:
            try:
                response = requests.post(f"https://fcm.googleapis.com/v1/projects/{settings.FIREBASE_PROJECT_ID}/messages:send", headers={"Authorization": f"Bearer {credentials.token}"}, json={"message": {"token": device.token, "notification": {"title": row.title, "body": row.message}, "data": {"request_id": str(row.request_id or ""), "notification_id": str(row.id)}, "android": {"priority": "HIGH", "notification": {"tag": str(row.id)}}}}, timeout=10)
                errors = response.json().get("error", {}).get("details", []) if not response.ok else []
                if any(item.get("errorCode") == "UNREGISTERED" for item in errors): db.delete(device)
                elif not response.ok: success = False
            except Exception:
                success = False
        row.push_processed = success
        row.push_attempts += 1
        row.push_retry_at = now_utc() + timedelta(seconds=min(3600, 10 * 2 ** min(row.push_attempts, 8)))
    db.commit()

def tick():
    with SessionLocal() as db:
        overdue_reminders(db)
        dispatch(db)

async def run_worker():
    while True:
        try: await asyncio.to_thread(tick)
        except Exception as error: logger.warning("Notification worker retry (%s)", type(error).__name__)
        await asyncio.to_thread(notification_wakeup.wait, 10)
        notification_wakeup.clear()
