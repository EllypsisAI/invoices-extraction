"""Method 4: the language model reads, Python calculates and checks, a person reviews the flags.

    python method4_hybrid.py      -> output/method4_hybrid.csv

Built from the mistakes of Methods 1-3 (see results/mistake_types.md):

- All three copied subtotals and totals that the invoice itself prints wrongly, so here the
  model does no arithmetic at all. It reports every number exactly as printed, and Python
  recomputes the net amounts, subtotal, VAT, discount and total. Where the invoice disagrees
  with its own arithmetic, the computed value is used and the invoice is flagged for review.
- The cheap model misread deducted discounts as conditional, took quantities from descriptions
  ("50 seats") and kept labels ("Bank: ..."); the prompt now addresses each of these directly.
- The expensive model wrote a VAT rate of 0 where none was printed; here a missing rate stays
  empty and is flagged, never guessed.
- With two VAT rates the invoice does not say which line has which rate; Python finds the set of
  lines whose total matches each printed VAT amount.

The reader is the cheap model (Gemini 3.5 Flash-Lite): the reading mistakes are dealt with by the
prompt and the checks, so there is no need to pay for the larger model.
"""
import json
import re
import sys
from decimal import ROUND_HALF_UP, Decimal
from itertools import combinations

from google import genai

from llm_extract import RAW_ROOT, api_key, as_decimal, call
from schema import PDF_DIR, write_csv

MODEL = "gemini-3.5-flash-lite"
OUT = "output/method4_hybrid.csv"
CENT = Decimal("0.01")

PROMPT = """You are reading a supplier invoice for an accounts-payable system. Your only job is to
READ: report what the invoice says. Do not calculate, correct or complete any number;
the system does all arithmetic itself and needs the numbers exactly as printed.

Values:
- Give values without their labels or surrounding words. bank_details is "HSBC UK | IBAN: ... | BIC: ...",
  not "Bank: HSBC UK ...". A line described as "For X, we invoice 100 EUR." has description "X".
- Numbers as plain numbers (no thousands separators, no currency). Credit notes keep their minus signs.
- Addresses split into street, postal_code and city; countries as ISO 3166 two-letter codes; dates as YYYY-MM-DD.

Line items:
- quantity is the number in the quantity column or before "×". A line that states only an amount
  has quantity 1. Numbers inside a description ("50 seats", "2 days", "Phase 2 of 7") are part of
  the description, never the quantity.
- unit_price and amount exactly as printed, even if the prices include VAT.
- vat_rate only if the invoice shows a rate for that specific line, otherwise null.

VAT:
- prices_include_vat: true if the invoice says its prices or subtotal include VAT.
- vat_rate_printed: the VAT rate as a decimal (0.20 for 20%) only if a single rate is printed
  somewhere on the invoice; null if no rate is printed. Never guess a rate.
- vat_amount_printed: the total VAT amount as printed, e.g. from "VAT: 31'070.14 EUR" or
  "Plus statutory VAT of 1.218,20 EUR"; null only if no VAT amount appears anywhere.
- vat_lines: if VAT is shown in several lines with different rates, each line's rate and amount;
  otherwise the single VAT line. Empty if no VAT amount is printed.
- reverse_charge: true if the invoice says the reverse-charge mechanism applies.

Discount (null if the invoice has none):
- type "percentage", "amount" or "trade_terms". trade_terms means an early-payment offer in the
  payment terms such as "2/10 net 30" (2% off if paid within 10 days).
- conditional: true ONLY for trade_terms. A discount, rebate or adjustment that is deducted on this
  invoice is NOT conditional, even if it is called an "early payment discount".
- value: the percentage as a decimal (0.075 for -7.5%) or the fixed amount, as a positive number.
- description: the discount's name only, without the percentage, the amount or words like "Less".
- amount_printed: the deducted amount as a positive number, or null if none is printed.

Totals: subtotal_printed and total_printed exactly as printed (null if not printed).
"""

ADDRESS = {"type": "object", "properties": {k: {"type": "string"} for k in ("street", "postal_code", "city")},
           "required": ["street", "postal_code", "city"]}
NUM, OPT = {"type": "number"}, {"type": ["number", "null"]}
SCHEMA = {
    "type": "object",
    "properties": {
        "invoice_id": {"type": "string"}, "is_credit_note": {"type": "boolean"},
        "vendor": {"type": "string"}, "vendor_country": {"type": "string"}, "vendor_address": ADDRESS,
        "recipient": {"type": "string"}, "recipient_country": {"type": "string"}, "recipient_address": ADDRESS,
        "date": {"type": "string"}, "due_date": {"type": "string"}, "currency": {"type": "string"},
        "line_items": {"type": "array", "items": {
            "type": "object",
            "properties": {"description": {"type": "string"}, "quantity": NUM, "unit_price": NUM,
                           "amount": NUM, "vat_rate": OPT},
            "required": ["description", "quantity", "unit_price", "amount", "vat_rate"]}},
        "prices_include_vat": {"type": "boolean"}, "vat_rate_printed": OPT, "vat_amount_printed": OPT,
        "vat_lines": {"type": "array", "items": {"type": "object", "properties": {"rate": OPT, "amount": NUM},
                                                 "required": ["rate", "amount"]}},
        "reverse_charge": {"type": "boolean"},
        "discount": {"type": ["object", "null"], "properties": {
            "type": {"type": "string", "enum": ["percentage", "amount", "trade_terms"]},
            "value": NUM, "description": {"type": "string"}, "conditional": {"type": "boolean"},
            "amount_printed": OPT},
            "required": ["type", "value", "description", "conditional", "amount_printed"]},
        "subtotal_printed": OPT, "total_printed": OPT,
        "payment_terms": {"type": "string"}, "bank_details": {"type": "string"},
    },
    "required": ["invoice_id", "is_credit_note", "vendor", "vendor_country", "vendor_address", "recipient",
                 "recipient_country", "recipient_address", "date", "due_date", "currency", "line_items",
                 "prices_include_vat", "vat_rate_printed", "vat_amount_printed", "vat_lines", "reverse_charge", "discount",
                 "subtotal_printed", "total_printed", "payment_terms", "bank_details"],
}


def r2(x):
    return x.quantize(CENT, ROUND_HALF_UP)


def eur(x):
    return f"{x:,.2f}"


def rate_text(r):
    return f"{(r * 100).normalize():f}%"


def assign_mixed_rates(lines, vat_lines):
    """Two VAT rates, lines without rates: find the lines whose total gives each printed VAT amount."""
    rated = [(v["rate"], v["amount"]) for v in vat_lines if v["rate"] is not None]
    if len(rated) != 2:
        return False
    (ra, va), (rb, _) = rated
    idx = range(len(lines))
    for k in range(1, len(lines)):
        for subset in combinations(idx, k):
            base = sum(lines[i]["amount"] for i in subset)
            if abs(r2(base * ra) - va) <= CENT:
                for i in idx:
                    lines[i]["vat_rate"] = ra if i in subset else rb
                return True
    return False


def reconcile(d):
    """Model reading -> invoice in ground-truth shape, plus review notes."""
    notes = []
    lines = d.get("line_items") or []
    for li in lines:
        for k in ("unit_price", "amount", "vat_rate"):
            li[k] = as_decimal(li.get(k))
        q = li.get("quantity")
        li["quantity"] = int(q) if isinstance(q, float) and q.is_integer() else q
    vat_lines = [{"rate": as_decimal(v.get("rate")), "amount": as_decimal(v.get("amount"))}
                 for v in d.get("vat_lines") or []]
    printed_sub, printed_total = as_decimal(d.get("subtotal_printed")), as_decimal(d.get("total_printed"))
    printed_rate = as_decimal(d.get("vat_rate_printed"))
    printed_vat = as_decimal(d.get("vat_amount_printed"))
    if vat_lines and printed_vat is None:
        printed_vat = sum((v["amount"] for v in vat_lines), Decimal(0))

    # 1. every line: quantity x unit price = amount
    for n, li in enumerate(lines, 1):
        if li["unit_price"] is not None and li["amount"] is not None and \
                abs(r2(Decimal(li["quantity"]) * li["unit_price"]) - li["amount"]) > CENT:
            notes.append(f"Line {n}: {li['quantity']} x {eur(li['unit_price'])} is not {eur(li['amount'])}.")
    gross_sum = sum((li["amount"] for li in lines), Decimal(0))

    # 2. VAT rate: printed, reverse charge, or derived from printed amounts. Never guessed.
    rates = {v["rate"] for v in vat_lines if v["rate"] is not None}
    mixed = len(rates) > 1
    if d.get("reverse_charge"):
        rate = Decimal(0)
    elif mixed:
        rate = None
    elif printed_rate is not None or rates:
        rate = printed_rate if printed_rate is not None else rates.pop()
    elif printed_vat is not None:
        net_base = gross_sum - printed_vat if d.get("prices_include_vat") else gross_sum
        rate = (printed_vat / net_base).quantize(Decimal("0.001")) if net_base else None
        notes.append(f"VAT rate not printed; derived as {rate_text(rate)} from the printed VAT amount.")
    else:
        rate = None
        notes.append("No VAT rate or VAT amount printed: VAT cannot be determined. Confirm the rate.")

    # 3. net line amounts (prices that include VAT are converted with the rate)
    if d.get("prices_include_vat"):
        if rate is None:
            notes.append("Prices include VAT but no rate is stated: amounts below are GROSS, not net.")
        else:
            f = 1 + rate
            for li in lines:
                li["unit_price"], li["amount"] = r2(li["unit_price"] / f), r2(li["amount"] / f)
    if mixed and not assign_mixed_rates(lines, vat_lines):
        notes.append("Several VAT rates and the lines could not be matched to them.")
    for li in lines:
        if li["vat_rate"] is None:
            li["vat_rate"] = rate

    subtotal = sum((li["amount"] for li in lines), Decimal(0))
    sub_check = gross_sum if d.get("prices_include_vat") else subtotal
    if printed_sub is not None and abs(printed_sub - sub_check) > CENT:
        notes.append(f"Printed subtotal {eur(printed_sub)} differs from the sum of the lines "
                     f"{eur(sub_check)} by {eur(printed_sub - sub_check)}; the sum is used.")

    # 4. VAT amount, recomputed per rate
    if mixed:
        vat = sum((r2(sum((li["amount"] for li in lines if li["vat_rate"] == r), Decimal(0)) * r) for r in rates),
                  Decimal(0)) if all(li["vat_rate"] is not None for li in lines) else printed_vat
    elif rate is not None:
        vat = r2(subtotal * rate)
    else:
        vat = None
    if vat is not None and printed_vat is not None and abs(printed_vat - vat) > CENT:
        notes.append(f"Printed VAT {eur(printed_vat)} differs from the computed {eur(vat)}; computed is used.")

    # 5. discount
    disc = d.get("discount")
    disc_amount = Decimal("0.00")
    if disc:
        disc["value"] = abs(as_decimal(disc["value"]))
        printed_disc = as_decimal(disc.pop("amount_printed", None))
        # check the model's label: "trade terms" only if the payment terms really are "2/10 net 30"-style
        terms_offer = bool(re.search(r"\d+(?:\.\d+)?\s*/\s*\d+\s+net\s+\d+", d.get("payment_terms") or "", re.I))
        if disc["type"] == "trade_terms" and not terms_offer:
            disc["type"] = "percentage" if disc["value"] < 1 else "amount"
            notes.append("Model called the discount a payment-terms offer, but the payment terms are not; "
                         "treated as deducted.")
        if disc["type"] == "trade_terms":
            disc["conditional"] = True
            notes.append(f"Conditional discount ({disc['description']}): not deducted; "
                         "applies only if paid within the discount period.")
        else:
            disc["conditional"] = False
            disc_amount = r2(subtotal * disc["value"]) if disc["type"] == "percentage" else r2(disc["value"])
            if subtotal < 0:
                disc_amount = -disc_amount
            if printed_disc is not None and abs(abs(printed_disc) - abs(disc_amount)) > CENT:
                notes.append(f"Printed discount {eur(printed_disc)} differs from the computed "
                             f"{eur(disc_amount)}; computed is used.")

    # 6. total
    total = subtotal + vat - disc_amount if vat is not None else None
    if total is not None and printed_total is not None and abs(printed_total - total) > CENT:
        notes.append(f"Printed total {eur(printed_total)} differs from subtotal + VAT - discount "
                     f"= {eur(total)} by {eur(printed_total - total)}; computed is used.")
    if total is None:
        total = printed_total

    inv = {k: d.get(k) for k in ("invoice_id", "is_credit_note", "vendor", "vendor_country", "vendor_address",
                                 "recipient", "recipient_country", "recipient_address", "date", "due_date",
                                 "currency", "payment_terms", "bank_details")}
    inv.update(line_items=lines, subtotal=subtotal, vat_rate=rate, vat_amount=vat, discount=disc,
               discount_amount=disc_amount, total=total)
    # a note that only explains a derivation does not need a person; a disagreement or gap does
    needs_review = [n for n in notes if not n.startswith(("VAT rate not printed; derived", "Conditional discount", "Model called the discount"))]
    inv["review_flag"] = bool(needs_review)
    inv["review_notes"] = " | ".join(notes)
    return inv


def main(only=None):
    raw_dir = RAW_ROOT / "method4_raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    client = genai.Client(api_key=api_key())
    invoices = []
    for pdf in sorted(PDF_DIR.glob("*.pdf")):
        if only and pdf.stem not in only:
            continue
        raw = raw_dir / f"{pdf.stem}.json"
        record = json.loads(raw.read_text(encoding="utf-8")) if raw.exists() else \
            call(client, MODEL, pdf, raw, prompt=PROMPT, schema=SCHEMA)
        inv = reconcile(json.loads(record["text"]))
        invoices.append(inv)
        print(f"{pdf.stem}: {'REVIEW' if inv['review_flag'] else 'ok    '} {inv['review_notes'][:150]}", flush=True)
    if only:
        return
    write_csv(invoices, OUT, extra=["review_flag", "review_notes"])
    recs = [json.loads(p.read_text(encoding="utf-8")) for p in sorted(raw_dir.glob("*.json"))]
    usage = {k: sum(r[k] for r in recs) for k in ("input_tokens", "output_tokens", "thinking_tokens", "total_tokens")}
    usage.update(model=MODEL, invoices=len(recs), seconds=round(sum(r["seconds"] for r in recs), 1),
                 flagged=sum(i["review_flag"] for i in invoices))
    (RAW_ROOT / "method4_usage.json").write_text(json.dumps(usage, indent=1), encoding="utf-8")
    print(f"wrote {OUT}\n{usage}")


if __name__ == "__main__":
    main(sys.argv[1:])
