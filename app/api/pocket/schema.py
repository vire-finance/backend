from app.shared.schema import Input, PositiveMoney, Name, Patch, Theme

class PocketCreate(Input):
    name: Name
    allocated_amount: PositiveMoney
    monthly_limit: PositiveMoney | None = None
    theme: Theme = "BLUE"


class PocketUpdate(Patch):
    name: Name | None = None
    allocated_amount: PositiveMoney | None = None
    monthly_limit: PositiveMoney | None = None
    theme: Theme | None = None