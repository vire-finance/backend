from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from zoneinfo import ZoneInfo

from fastapi import HTTPException

WIB = ZoneInfo("Asia/Jakarta")

def ensure(condition, message, code=400):
    if not condition:
        raise HTTPException(status_code=code, detail=message)

def now_utc():
    return datetime.now(timezone.utc).replace(tzinfo=None)

def local_time(value):
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(WIB)

def iso(value):
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.isoformat()

def month_bounds(at=None):
    local = local_time(at or now_utc())
    start = local.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    if start.month == 12:
        end = start.replace(year=start.year + 1, month=1)
    else:
        end = start.replace(month=start.month + 1)
    return (
        start.astimezone(timezone.utc).replace(tzinfo=None),
        end.astimezone(timezone.utc).replace(tzinfo=None),
    )

def previous_month(value):
    if value.month == 1:
        return value.replace(year=value.year - 1, month=12)
    return value.replace(month=value.month - 1)

def money(value):
    value = Decimal(value)
    ensure(
        value == value.to_integral_value(),
        "Data nominal lama memiliki pecahan rupiah. Rekonsiliasi data terlebih dahulu.",
        409,
    )
    return int(value)

def fields(row, *names):
    result = {}

    for name in names:
        value = getattr(row, name)

        if isinstance(value, Decimal):
            value = money(value)
        elif isinstance(value, Enum):
            value = value.value
        elif isinstance(value, datetime):
            value = iso(value)

        result[name] = value

    return result

def limit_data(limit, spent):
    limit = money(limit)
    return {
        "monthly_limit": limit,
        "monthly_spent": spent,
        "monthly_remaining": max(0, limit - spent),
        "monthly_percentage": round(spent * 100 / limit, 2),
    }