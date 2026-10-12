"""Durable notification outbox and overdue reminders; never blocks payments."""
from app.api.notification.service import notification_wakeup
import asyncio
import logging
from datetime import timedelta, datetime, timezone
from sqlalchemy import select, or_
from app.core.database import SessionLocal
from app.core.config import settings
from app.api.notification.model import Notification, PushDevice, PushDelivery
from app.api.request.model import FundRequest
from app.api.pocket.model import Pocket
from app.api.auth.model import User, AuthSession
from app.shared.enums import FundRequestStatus
from app.shared.utils import now_utc
logger = logging.getLogger(__name__)
_credentials = None
_missing_configuration_logged = False

def overdue_reminders(db):
    rows = db.scalars(select(FundRequest).where(FundRequest.status == FundRequestStatus.APPROVED, FundRequest.receipt_status.in_(["AWAITING_RECEIPT", "NEEDS_CLARIFICATION"]), FundRequest.receipt_due_at < now_utc(), FundRequest.receipt_overdue_notified.is_(False)).with_for_update(skip_locked=True)).all()
    for row in rows:
        owner_id = db.get(Pocket, row.pocket_id).owner_id
        for user_id in [owner_id, row.requester_id]:
            db.add(Notification(user_id=user_id, request_id=row.id, title="Receipt overdue", message=f"Bukti pembelian {row.party_name} melewati tenggat. Request masih terbuka."))
        row.receipt_overdue_notified = True
    db.commit()

def dispatch(db):
    global _credentials, _missing_configuration_logged
    if not settings.FIREBASE_SERVICE_ACCOUNT or not settings.FIREBASE_PROJECT_ID:
        if not _missing_configuration_logged:
            logger.warning("Push delivery disabled: FIREBASE_SERVICE_ACCOUNT or FIREBASE_PROJECT_ID missing")
            _missing_configuration_logged = True
        return
    _missing_configuration_logged = False
    from google.oauth2 import service_account
    from google.auth.transport.requests import Request
    import requests
    if _credentials is None:
        _credentials = service_account.Credentials.from_service_account_file(settings.FIREBASE_SERVICE_ACCOUNT, scopes=["https://www.googleapis.com/auth/firebase.messaging"])
    credentials = _credentials
    if not credentials.valid:
        credentials.refresh(Request())
    rows = db.scalars(select(Notification).where(Notification.push_processed.is_(False),
        select(PushDevice.id).where(PushDevice.user_id == Notification.user_id,
                                   PushDevice.session_id.is_not(None)).exists(),
        or_(Notification.push_retry_at.is_(None), Notification.push_retry_at <= now_utc()))
        .order_by(Notification.created_at).limit(20).with_for_update(skip_locked=True)).all()
    for row in rows:
        user = db.get(User, row.user_id)
        if user is None or user.notification_preferences.get("push") is False:
            row.push_processed = True
            continue
        devices = db.scalars(select(PushDevice).where(PushDevice.user_id == row.user_id)).all()
        success = True
        for candidate in devices:
            # Lock session before device, matching refresh/registration order.
            session = db.scalar(select(AuthSession).where(AuthSession.id == candidate.session_id)
                                .with_for_update().execution_options(populate_existing=True))
            if session is None or session.user_id != row.user_id or session.revoked_at is not None or session.expires_at <= datetime.now(timezone.utc):
                continue
            device = db.scalar(select(PushDevice).where(PushDevice.id == candidate.id)
                               .with_for_update().execution_options(populate_existing=True))
            if device is None or device.user_id != row.user_id or device.session_id != session.id:
                continue
            if db.get(PushDelivery, (row.id, device.id)) is not None:
                continue
            try:
                # Lock-screen content remains generic even if FCM delivers an
                # already-accepted notification after the device logs out.
                response = requests.post(f"https://fcm.googleapis.com/v1/projects/{settings.FIREBASE_PROJECT_ID}/messages:send",
                    headers={"Authorization": f"Bearer {credentials.token}"},
                    json={"message": {"token": device.token,
                        "notification": {"title": "VIRE", "body": "Open VIRE to view a new notification."},
                        "data": {"request_id": str(row.request_id or ""), "notification_id": str(row.id)},
                        "android": {"priority": "HIGH", "notification": {"tag": str(row.id)}}}}, timeout=10)
                errors = response.json().get("error", {}).get("details", []) if not response.ok else []
                if any(isinstance(item, dict) and item.get("errorCode") == "UNREGISTERED" for item in errors):
                    db.delete(device)
                elif response.ok:
                    db.add(PushDelivery(notification_id=row.id, device_id=device.id))
                else:
                    success = False
                    logger.warning("Push delivery retry notification=%s http_status=%s", row.id, getattr(response, 'status_code', 'unknown'))
            except Exception as error:
                success = False
                logger.warning("Push delivery retry notification=%s error_type=%s", row.id, type(error).__name__)
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
