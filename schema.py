"""The CSV layout every method writes, and conversion between it and the ground-truth JSON.

One row per line item. The invoice fields (vendor, dates, totals ...) are repeated on every
row of the same invoice, so each row can be read on its own, like a booked transaction line.
"""
import csv
import json
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / "Invoices"   # course data stays outside the repo
PDF_DIR = DATA / "pdf"
TRUTH_DIR = DATA / "ground_truth"

INVOICE_FIELDS = [
    "invoice_id", "is_credit_note",
    "vendor", "vendor_street", "vendor_postal_code", "vendor_city", "vendor_country",
    "recipient", "recipient_street", "recipient_postal_code", "recipient_city", "recipient_country",
    "date", "due_date", "currency", "payment_terms", "bank_details",
    "subtotal", "vat_rate", "vat_amount",
    "discount_type", "discount_value", "discount_description", "discount_conditional",
    "discount_amount", "total",
]
LINE_FIELDS = ["line_no", "description", "quantity", "unit_price", "line_vat_rate", "amount"]
COLUMNS = INVOICE_FIELDS + LINE_FIELDS


def _s(v):
    if v is None:
        return ""
    if isinstance(v, bool):
        return "true" if v else "false"
    return str(v)


def flatten(inv):
    """Invoice dict in ground-truth shape -> list of CSV rows (dicts)."""
    head = {
        "invoice_id": inv.get("invoice_id"),
        "is_credit_note": inv.get("is_credit_note"),
        "vendor": inv.get("vendor"),
        "recipient": inv.get("recipient"),
        "vendor_country": inv.get("vendor_country"),
        "recipient_country": inv.get("recipient_country"),
        "date": inv.get("date"),
        "due_date": inv.get("due_date"),
        "currency": inv.get("currency"),
        "payment_terms": inv.get("payment_terms"),
        "bank_details": inv.get("bank_details"),
        "subtotal": inv.get("subtotal"),
        "vat_rate": inv.get("vat_rate"),
        "vat_amount": inv.get("vat_amount"),
        "discount_amount": inv.get("discount_amount"),
        "total": inv.get("total"),
    }
    for k in ("review_flag", "review_notes"):      # Method 4 only
        if k in inv:
            head[k] = inv[k]
    for who in ("vendor", "recipient"):
        addr = inv.get(f"{who}_address") or {}
        for part in ("street", "postal_code", "city"):
            head[f"{who}_{part}"] = addr.get(part)
    disc = inv.get("discount") or {}
    head["discount_type"] = disc.get("type")
    head["discount_value"] = disc.get("value")
    head["discount_description"] = disc.get("description")
    head["discount_conditional"] = disc.get("conditional")

    rows = []
    for i, li in enumerate(inv.get("line_items") or [{}], start=1):
        row = {k: _s(v) for k, v in head.items()}
        row.update({
            "line_no": str(i) if li else "",
            "description": _s(li.get("description")),
            "quantity": _s(li.get("quantity")),
            "unit_price": _s(li.get("unit_price")),
            "line_vat_rate": _s(li.get("vat_rate")),
            "amount": _s(li.get("amount")),
        })
        rows.append(row)
    return rows


def write_csv(invoices, path, extra=()):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS + list(extra))
        w.writeheader()
        for inv in invoices:
            w.writerows(flatten(inv))


def read_csv(path):
    """CSV -> {invoice_id: {"head": {...}, "lines": [{...}, ...]}}"""
    out = {}
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            inv = out.setdefault(row["invoice_id"], {"head": {k: row.get(k, "") for k in INVOICE_FIELDS}, "lines": []})
            if row.get("line_no"):
                inv["lines"].append({k: row.get(k, "") for k in LINE_FIELDS})
    return out


def load_truth():
    return [json.loads(p.read_text(encoding="utf-8")) for p in sorted(TRUTH_DIR.glob("*.json"))]
