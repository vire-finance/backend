from typing import Literal
from uuid import UUID

from app.shared.schema import Input, PositiveMoney

class TopUpCreate(Input):
    pocket_id: UUID
    amount: PositiveMoney
    method: Literal["QR_CODE", "BANK_TRANSFER"]