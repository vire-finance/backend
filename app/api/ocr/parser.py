import re
from datetime import date
from decimal import Decimal, InvalidOperation

from app.api.ocr.schema import ExtractedData


MONTH_MAP = {
    "januari": 1,
    "january": 1,
    "jan": 1,
    "februari": 2,
    "february": 2,
    "pebruari": 2,
    "feb": 2,
    "maret": 3,
    "march": 3,
    "mar": 3,
    "april": 4,
    "apr": 4,
    "mei": 5,
    "may": 5,
    "juni": 6,
    "june": 6,
    "jun": 6,
    "juli": 7,
    "july": 7,
    "jul": 7,
    "agustus": 8,
    "august": 8,
    "agu": 8,
    "ags": 8,
    "aug": 8,
    "september": 9,
    "sept": 9,
    "sep": 9,
    "oktober": 10,
    "october": 10,
    "okt": 10,
    "oct": 10,
    "november": 11,
    "nopember": 11,
    "nov": 11,
    "desember": 12,
    "december": 12,
    "des": 12,
    "dec": 12,
}

IGNORE_MERCHANT_KEYWORDS = {
    "pembayaran",
    "faktur",
    "kwitansi",
    "receipt",
    "date",
    "cashier",
    "customer",
    "struk",
    "kepada",
    "bill",
    "tanggal",
    "struk pembayaran",
    "grand total",
    "total",
    "invoice",
    "yth",
    "pelanggan",
    "kasir",
    "nota",
    "subtotal",
    "tax invoice",
    "bukti pembayaran",
}

MERCHANT_PREFIX_RE = re.compile(
    r"\b(PT\.?|CV\.?|UD\.?|TB\.?|TOKO|WARUNG|RESTO|RESTAURANT|CAFE|KEDAI|APOTEK|KLINIK|BENGKEL)\s+([A-Za-z0-9\s&.,'-]+)",
    re.IGNORECASE,
)

MERCHANT_SUFFIX_RE = re.compile(
    r"\b([A-Za-z0-9\s&.,'-]+)\s+(STORE|SHOP|SUPPLIES|MART|STATIONERY|MARKET|BAKERY|ELECTRONICS|BOUTIQUE)\b",
    re.IGNORECASE,
)

KNOWN_CHAINS_RE = re.compile(
    r"\b(INDOMARET|ALFAMART|SUPERINDO|HYPERMART|GRAMEDIA|ACE HARDWARE|STARBUCKS|MCDONALD'S|KFC)\b",
    re.IGNORECASE,
)


def extract_other_party_name(text: str) -> str | None:
    if not text:
        return None

    lines = [line.strip() for line in text.splitlines() if line.strip()]

    for line in lines:
        cleaned = line.lower()

        if any(keyword == cleaned for keyword in IGNORE_MERCHANT_KEYWORDS):
            continue

        match = MERCHANT_PREFIX_RE.search(line)
        if match:
            candidate = match.group(0).strip()
            candidate = re.sub(r"[:;,\-_]+$", "", candidate).strip()

            if len(candidate) >= 3:
                return candidate

    for line in lines:
        match = KNOWN_CHAINS_RE.search(line)
        if match:
            return match.group(0).strip()

    for line in lines:
        cleaned = line.lower()

        if any(keyword == cleaned for keyword in IGNORE_MERCHANT_KEYWORDS):
            continue

        match = MERCHANT_SUFFIX_RE.search(line)

        if match:
            candidate = match.group(0).strip()
            candidate = re.sub(r"[:;,\-_]+$", "", candidate).strip()

            if len(candidate) >= 3:
                if any(
                    keyword in candidate.lower()
                    for keyword in IGNORE_MERCHANT_KEYWORDS
                ):
                    continue

                return candidate

    return None


def parse_amount_str(raw: str) -> Decimal | None:
    cleaned = re.sub(r"(?i)\b(?:rp|idr)\.?", "", raw)
    cleaned = re.sub(r"[^\d.,]", "", cleaned).strip("., ")

    if not cleaned:
        return None

    if "." in cleaned and "," in cleaned:
        last_dot = cleaned.rfind(".")
        last_comma = cleaned.rfind(",")

        if last_dot > last_comma:
            cleaned = cleaned.replace(",", "")
        else:
            cleaned = cleaned.replace(".", "")
            cleaned = cleaned.replace(",", ".")

    elif "." in cleaned:
        parts = cleaned.split(".")

        if len(parts) > 2:
            cleaned = cleaned.replace(".", "")
        elif len(parts) == 2:
            if len(parts[1]) == 3:
                cleaned = cleaned.replace(".", "")
            elif len(parts[1]) == 2:
                pass
            else:
                cleaned = cleaned.replace(".", "")

    elif "," in cleaned:
        parts = cleaned.split(",")

        if len(parts) > 2:
            cleaned = cleaned.replace(",", "")
        elif len(parts) == 2:
            if len(parts[1]) == 2:
                cleaned = parts[0] + "." + parts[1]
            else:
                cleaned = cleaned.replace(",", "")

    try:
        dec = Decimal(cleaned)

        if dec < 0:
            return None

        return dec.quantize(Decimal("0.01"))

    except (InvalidOperation, ValueError):
        return None


def extract_total_amount(text: str) -> Decimal | None:
    """Select a labelled total, never the largest/last currency amount.

    OCR may insert spaces around a thousands separator. Preserve those digits;
    conflicting totals at the same priority require manual correction.
    """
    lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
    labels = re.compile(
        r"(?P<final>grand\s+total|total\s+(?:bayar|tagihan|akhir)|jumlah\s+total)"
        r"|(?P<total>\btotal\b)|(?P<other>\b(?:jumlah|tagihan|netto|amount\s+due)\b)", re.I)
    excluded = re.compile(r"\b(?:sub\s*total|tax|pajak|ppn|kembali\w*|change|tunai|cash|bayar|payment|diskon|discount)\b", re.I)
    amount_pattern = r"(?:rp\.?|idr)?\s*(?:[0-9]{1,3}(?:[.,][0-9]{3})+(?:[.,][0-9]{2})?|[0-9]+(?:[.,][0-9]{2})?)"
    amount_re = re.compile(amount_pattern, re.I)
    candidates = []
    for index, line in enumerate(lines):
        match = labels.search(line)
        if not match or excluded.search(line[:match.start()]):
            continue
        if re.search(r"\bsub\s*total\b", line[:match.end()], re.I):
            continue
        tail = line[match.end():]
        # 'Total items/qty' is a count, not a financial total.
        if re.match(r"\s*(?:items?|qty|barang|produk)\b", tail, re.I):
            continue
        tail = excluded.split(tail, maxsplit=1)[0].strip(" :\t=")
        if not tail and index + 1 < len(lines):
            tail = lines[index + 1]
        tail = re.sub(r"(?<=\d)\s*([.,])\s*(?=\d)", r"\1", tail)
        # Full-line evidence avoids returning the trailing 900 in '33 900'.
        if not amount_re.fullmatch(tail):
            continue
        amount = parse_amount_str(tail)
        if amount is not None and amount > 0:
            rank = 3 if match.lastgroup == "final" else 2 if match.lastgroup == "total" else 1
            candidates.append((rank, amount))
    if not candidates:
        return None
    rank = max(item[0] for item in candidates)
    totals = {amount for priority, amount in candidates if priority == rank}
    return totals.pop() if len(totals) == 1 else None


def extract_date(text: str) -> date | None:
    if not text:
        return None

    dmy_re = re.compile(
        r"\b(\d{1,2})[\/\-\.](\d{1,2})[\/\-\.](\d{4})\b"
    )

    for match in dmy_re.finditer(text):
        day = int(match.group(1))
        month = int(match.group(2))
        year = int(match.group(3))

        try:
            return date(year, month, day)
        except (ValueError, OverflowError):
            continue

    dmy_short_re = re.compile(
        r"\b(\d{1,2})[\/\-\.](\d{1,2})[\/\-\.](\d{2})\b"
    )

    for match in dmy_short_re.finditer(text):
        day = int(match.group(1))
        month = int(match.group(2))
        year = int(match.group(3))

        # Assume 00-69 = 2000-2069, 70-99 = 1970-1999
        year += 2000 if year <= 69 else 1900

        try:
            return date(year, month, day)
        except (ValueError, OverflowError):
            continue

    ymd_re = re.compile(
        r"\b(\d{4})[\/\-](\d{1,2})[\/\-](\d{1,2})\b"
    )

    for match in ymd_re.finditer(text):
        year = int(match.group(1))
        month = int(match.group(2))
        day = int(match.group(3))

        try:
            return date(year, month, day)
        except (ValueError, OverflowError):
            continue

    d_month_y_re = re.compile(
        r"\b(\d{1,2})\s+([A-Za-z]+)\s+(\d{4})\b"
    )

    for match in d_month_y_re.finditer(text):
        day = int(match.group(1))
        month_name = match.group(2).lower()
        year = int(match.group(3))

        if month_name not in MONTH_MAP:
            continue

        try:
            return date(year, MONTH_MAP[month_name], day)
        except (ValueError, OverflowError):
            continue

    month_d_y_re = re.compile(
        r"\b([A-Za-z]+)\s+(\d{1,2}),?\s+(\d{4})\b"
    )

    for match in month_d_y_re.finditer(text):
        month_name = match.group(1).lower()
        day = int(match.group(2))
        year = int(match.group(3))

        if month_name not in MONTH_MAP:
            continue

        try:
            return date(year, MONTH_MAP[month_name], day)
        except (ValueError, OverflowError):
            continue

    return None


def parse_ocr_text(raw_text: str | None) -> ExtractedData:
    if not raw_text:
        return ExtractedData(
            other_party_name=None,
            total_amount=None,
            date=None,
        )

    merchant = extract_other_party_name(raw_text)
    total = extract_total_amount(raw_text)
    doc_date = extract_date(raw_text)

    return ExtractedData(
        other_party_name=merchant,
        total_amount=total,
        date=doc_date,
    )
