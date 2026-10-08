"""Calendar/date ranges use WIB boundaries and UTC database timestamps."""
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import re

from app.api.ai.schema import PeriodWindow
from app.shared.utils import WIB, ensure, local_time, now_utc


def shift_month(value, offset):
    index = value.year * 12 + value.month - 1 + offset
    return value.replace(year=index // 12, month=index % 12 + 1, day=1)


def utc_naive(value):
    return value.astimezone(timezone.utc).replace(tzinfo=None)


@dataclass(frozen=True)
class AnalyticsPeriod:
    start: datetime
    end: datetime
    previous_start: datetime
    previous_end: datetime
    label: str
    previous_label: str

    def window(self, previous=False):
        start, end, label = ((self.previous_start, self.previous_end, self.previous_label)
                             if previous else (self.start, self.end, self.label))
        return PeriodWindow(start=start.replace(tzinfo=timezone.utc),
                            end=end.replace(tzinfo=timezone.utc), label=label)


def resolve_period(period=None, start_date=None, end_date=None, *, now=None):
    ensure((start_date is None) == (end_date is None), "Both start_date and end_date are required.", 422)
    ensure(not (period is not None and start_date is not None), "Choose period or custom dates, not both.", 422)
    current = local_time(now or now_utc()).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    if start_date is not None:
        ensure(2000 <= start_date.year <= 2100 and 2000 <= end_date.year <= 2100,
               "Supported dates are in years 2000 through 2100.", 422)
        ensure(start_date <= end_date, "start_date must be on or before end_date.", 422)
        duration = (end_date - start_date).days + 1
        ensure(duration <= 366, "Custom ranges cannot exceed 366 days.", 422)
        start = datetime.combine(start_date, datetime.min.time(), tzinfo=WIB)
        end = datetime.combine(end_date + timedelta(days=1), datetime.min.time(), tzinfo=WIB)
        previous_start, previous_end = start - timedelta(days=duration), start
        label = f"{start_date.isoformat()} / {end_date.isoformat()}"
        previous_label = f"{previous_start.date().isoformat()} / {(start.date() - timedelta(days=1)).isoformat()}"
    else:
        period = period or "month"
        if re.fullmatch(r"[0-9]{4}-[0-9]{2}", period):
            year, month = map(int, period.split("-"))
            ensure(2000 <= year <= 2100 and 1 <= month <= 12, "Invalid calendar month.", 422)
            start = current.replace(year=year, month=month)
            end = shift_month(start, 1)
            previous_start, previous_end = shift_month(start, -1), start
        else:
            ensure(period in {"month", "previous_month", "last_3_months", "last_6_months"},
                   "Unsupported period.", 422)
            months = {"last_3_months": 3, "last_6_months": 6}.get(period, 1)
            end = current if period == "previous_month" else shift_month(current, 1)
            start = shift_month(end, -months)
            previous_start, previous_end = shift_month(start, -months), start
        label = start.strftime("%Y-%m") if shift_month(start, 1) == end else f"{start:%Y-%m} / {shift_month(end, -1):%Y-%m}"
        previous_label = (previous_start.strftime("%Y-%m") if shift_month(previous_start, 1) == previous_end
                          else f"{previous_start:%Y-%m} / {shift_month(previous_end, -1):%Y-%m}")
    return AnalyticsPeriod(*(utc_naive(v) for v in (start, end, previous_start, previous_end)), label, previous_label)
