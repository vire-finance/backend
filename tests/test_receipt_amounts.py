from contextlib import nullcontext
from decimal import Decimal
from pathlib import Path

import pytest

from app.api.ocr.parser import extract_total_amount, parse_amount_str


@pytest.mark.parametrize('amount', ['33.900', '33,900', '33900', '33, 900', '33 . 900', '33.900,00', '33,900.00'])
def test_indonesian_receipt_total(amount):
    text = f'TOKO DEMO\nSUBTOTAL 30.000\nPPN 3.900\nTOTAL {amount}\nBAYAR 50.000\nKEMBALI 16.100'
    assert extract_total_amount(text) == Decimal('33900')


@pytest.mark.parametrize('text,expected', [
    ('TOTAL 30.000\nGRAND TOTAL 33.900\nBAYAR 50.000', 33900),
    ('JUMLAH 33.900', 33900), ('TOTAL BAYAR\nRp 33.900', 33900),
    ('TOTAL 33,900 BAYAR 50,000', 33900),
    ('TOTAL 33 900', None), ('TOTAL 33.900\nTOTAL 32.000', None),
    ('SUB TOTAL 30.000\nBAYAR Rp50.000\nKEMBALI Rp20.000', None),
    ('TOTAL ITEMS 2\nRp 10.000', None), ('TOTAL\nPAJAK 900', None),
    ('TOTAL -33.900', None), ('TOTAL 0', None),
])
def test_total_evidence_and_ambiguity(text, expected):
    assert extract_total_amount(text) == (Decimal(expected) if expected is not None else None)


def test_plain_amount_formats():
    for raw in ['33.900', '33,900', '33900']:
        assert parse_amount_str(raw) == Decimal('33900')


def test_ocr_worker_persists_and_prefills_total(ctx, monkeypatch):
    from sqlalchemy.orm import Session
    from app.api.ocr import service
    from app.api.ocr.model import OCRDocument
    from app.api.request.service import FundRequestService
    from app.shared.enums import OCRStatus, UserRole
    ctx.user.role = UserRole.EMPLOYEE
    doc = OCRDocument(user_id=ctx.user.id, original_filename='receipt.png', stored_filename='receipt.png',
                      storage_path='synthetic', mime_type='image/png', file_size=10, ocr_status=OCRStatus.PENDING)
    ctx.db.add(doc); ctx.db.commit()
    monkeypatch.setattr(service, 'SessionLocal', lambda: Session(bind=ctx.db.bind, join_transaction_mode='create_savepoint'))
    monkeypatch.setattr(service, 'materialize_document', lambda doc: nullcontext(Path('synthetic')))
    monkeypatch.setattr(service, 'run_ocr', lambda **kwargs: 'TOKO DEMO\nTOTAL 33, 900\nKEMBALI 900')
    service.process_ocr_document_task(doc.id)
    ctx.db.expire_all()
    result = FundRequestService(ctx).prefill(doc.id)
    assert result['total_amount'] == 33900
    assert result['requires_user_review'] is True


from test_main_funding import ctx
