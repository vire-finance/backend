from app.shared.schema import Input, Money, Name, Patch, Theme

class PocketCreate(Input):
    name: Name
    allocated_amount: Money
    monthly_limit: Money | None = None
    theme: Theme = "BLUE"


class PocketUpdate(Patch):
    name: Name | None = None
    allocated_amount: Money | None = None
    monthly_limit: Money | None = None
    theme: Theme | None = None