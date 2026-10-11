import pytest
from pydantic import ValidationError

from app.shared.payment_destination import BANKS, E_WALLETS, PaymentDestination
from app.api.transaction.schema import TransactionCreate
from uuid import uuid4


@pytest.mark.parametrize("bank", BANKS)
def test_bank_selection(bank):
    extra = {"destination_bank_name": "Bank Jago"} if bank == "Other" else {}
    data = PaymentDestination(payment_type="BANK", destination_name=bank,
                              destination_account_number="000123", **extra)
    assert data.destination_account_number == "000123"
    assert data.payment_method == "BANK_TRANSFER"
    assert data.recipient_account.endswith(" 000123")


@pytest.mark.parametrize("wallet", E_WALLETS)
def test_wallet_selection(wallet):
    data = PaymentDestination(payment_type="E_WALLET", destination_name=wallet,
                              destination_account_number="008123456789")
    assert data.payment_method == "E_WALLET"


@pytest.mark.parametrize("changes", [
    {"payment_type": "QRIS"}, {"payment_type": "bank"},
    {"destination_name": "Arbitrary Bank"}, {"destination_name": "GoPay"},
    {"destination_name": ""}, {"destination_account_number": ""},
    {"destination_account_number": "12a"}, {"destination_account_number": "12 34"},
    {"destination_account_number": "１２３"}, {"destination_account_number": 123},
    {"destination_name": "Other"}, {"destination_bank_name": "Stale bank"},
    {"destination_name": "Other", "destination_bank_name": "   "},
    {"destination_name": "Other", "destination_bank_name": "123"},
])
def test_invalid_or_stale_selection(changes):
    values = dict(payment_type="BANK", destination_name="BCA", destination_account_number="001")
    with pytest.raises(ValidationError):
        PaymentDestination(**(values | changes))


@pytest.mark.parametrize("missing", ["payment_type", "destination_name", "destination_account_number"])
def test_missing_required_field(missing):
    values = dict(payment_type="BANK", destination_name="BCA", destination_account_number="001")
    del values[missing]
    with pytest.raises(ValidationError):
        PaymentDestination(**values)


@pytest.mark.parametrize("number", ["0", "000001", "0" * 100])
def test_no_fixed_account_length(number):
    assert PaymentDestination(payment_type="BANK", destination_name="BCA",
                              destination_account_number=number).destination_account_number == number


def test_combined_storage_capacity():
    with pytest.raises(ValidationError):
        PaymentDestination(payment_type="BANK", destination_name="BCA", destination_account_number="0" * 150)


def test_other_not_supported_for_wallet():
    with pytest.raises(ValidationError):
        PaymentDestination(payment_type="E_WALLET", destination_name="Other",
                           destination_bank_name="Bank Jago", destination_account_number="001")


def transaction_input(**extra):
    return TransactionCreate(card_id=uuid4(), amount=50, description="Supplies", **extra)


def test_structured_schema_derives_legacy_payment_details():
    payload = transaction_input(payment_type="BANK", destination_name="BCA", destination_account_number="000123")
    assert payload.payment_method == "BANK_TRANSFER"
    assert payload.recipient_account == "BCA 000123"
    assert payload.destination.destination_account_number == "000123"


@pytest.mark.parametrize("extra", [
    {"payment_type": "BANK"}, {"destination_name": "BCA"},
    {"destination_account_number": "000123"}, {"destination_bank_name": "Bank Jago"},
    {"payment_type": None}, {"destination_name": None},
    {"payment_type": "BANK", "destination_name": "BCA", "destination_account_number": "001", "payment_method": "E_WALLET"},
    {"payment_type": "BANK", "destination_name": "BCA", "destination_account_number": "001", "recipient_account": "Different"},
])
def test_partial_or_conflicting_structured_payload_rejected(extra):
    with pytest.raises(ValidationError):
        transaction_input(**extra)


@pytest.mark.parametrize("method,recipient", [("QRIS", None), ("BANK_TRANSFER", "Legacy Bank 001"), ("E_WALLET", "Legacy wallet 001")])
def test_legacy_payload_contract_preserved(method, recipient):
    payload = transaction_input(payment_method=method, recipient_account=recipient)
    assert payload.destination is None
    assert payload.payment_method == method
    assert payload.recipient_account == recipient
