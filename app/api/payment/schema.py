from typing import Literal
from uuid import UUID

from app.shared.schema import Input, Money

class TopUpCreate(Input):
    pocket_id: UUID
    amount: Money
    method: Literal["QR_CODE", "BANK_TRANSFER"]