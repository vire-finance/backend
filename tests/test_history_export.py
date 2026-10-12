from app.shared.enums import UserRole
import csv
import io
from uuid import uuid4

from app.api.history.service import HistoryService
from app.api.transaction.service import TransactionService
from test_main_funding import ctx, settings_card
from test_request_categories import employee_context
from test_e2e_authorization import client, headers


def test_owner_and_employee_csv_permissions_utf8_and_filters(client, ctx, settings_card):
    employee = employee_context(ctx, ctx.raw_card(settings_card).pocket_id)
    own = TransactionService(employee).execute(settings_card, 10, '=SUM(1,2) café', uuid4(), category='Marketing')
    TransactionService(ctx).execute(settings_card, 10, 'Owner private', uuid4(), category='Marketing')
    auth = headers(ctx.db, employee.user)
    result = client.get('/history/export.csv', headers=auth)
    assert result.status_code == 200 and result.content.startswith(b'\xef\xbb\xbf')
    assert result.headers['cache-control'] == 'private, no-store'
    rows = list(csv.DictReader(io.StringIO(result.content.decode('utf-8-sig'))))
    assert len(rows) == 1 and rows[0]['transaction_id'] == str(own['id'])
    assert rows[0]['description'] == "'=SUM(1,2) café"
    assert client.get('/dashboard/spending/export.csv', headers=auth).status_code == 403
    assert client.get('/history/export.csv').status_code == 401
    result = client.get('/history/export.csv?search=private', headers=headers(ctx.db, ctx.user))
    assert len(list(csv.DictReader(io.StringIO(result.content.decode('utf-8-sig'))))) == 1
    assert len(list(csv.DictReader(io.StringIO(HistoryService(ctx).export().decode('utf-8-sig'))))) == 2
    from app.api.auth.model import User
    outsider = User(email='export-outsider@example.com', username='Other', phone_number='0800000000', password_hash='test', role=UserRole.OWNER)
    ctx.db.add(outsider); ctx.db.commit()
    other_auth = headers(ctx.db, outsider)
    result = client.get('/history/export.csv', headers=other_auth)
    assert list(csv.DictReader(io.StringIO(result.content.decode('utf-8-sig')))) == []
    assert client.get(f'/history/export.csv?card_id={settings_card}', headers=other_auth).status_code == 404


def test_export_includes_more_than_one_page(ctx, settings_card):
    from app.api.transaction.model import Transaction
    for index in range(205):
        ctx.db.add(Transaction(pocket_id=ctx.raw_card(settings_card).pocket_id, card_id=settings_card,
            actor_id=ctx.user.id, idempotency_key=uuid4(), fingerprint=str(index), amount=1,
            transaction_type='CARD_PAYMENT', status='APPROVED', description=f'Export row {index}'))
    ctx.db.commit()
    output = HistoryService(ctx).export()
    assert len(list(csv.DictReader(io.StringIO(output.decode('utf-8-sig'))))) == 205
