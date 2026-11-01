"""Method 1: rule-based extraction, no language model.

    python method1_rules.py        -> output/method1_rules.csv

pdfplumber pulls the text layer out of each PDF; regular expressions then pick out the fields.
The rules were written by reading the invoice texts. The method reports what the invoice
*prints*. It only derives a value where the invoice does not print it at all (a VAT rate from
VAT / subtotal, a net price from a VAT-inclusive one); it does not check the arithmetic.
"""
import re
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal

import pdfplumber

from schema import PDF_DIR, write_csv

OUT = "output/method1_rules.csv"
CENT = Decimal("0.01")
NUM = r"-?(?:EUR\s*)?-?\d[\d.,']*"          # a printed amount, any of the three formats
COUNTRIES = {"United Kingdom": "GB", "Austria": "AT", "France": "FR", "Germany": "DE",
             "Switzerland": "CH", "Netherlands": "NL", "Estonia": "EE", "Spain": "ES",
             "Norway": "NO", "Belgium": "BE", "Italy": "IT", "Luxembourg": "LU", "Ireland": "IE"}
STOP = re.compile(r"^(Subtotal|The subtotal|VAT|Statutory VAT|Plus|Net prices|All prices|TOTAL|"
                  r"The total|Discount|Less|adjustment|-{5,}|Payment terms|Bank:|Thank you|Bill to:)")


def amount(token):
    """'1.234,56' / "1'234.56" / '1,234.56' / 'EUR -12.00' -> Decimal."""
    t = token.replace("EUR", "").replace(" ", "")
    neg = t.count("-") % 2 == 1
    t = t.replace("-", "").rstrip(".")
    if "'" in t:                                       # Swiss
        t = t.replace("'", "")
    elif re.search(r",\d{2}$", t):                     # German: comma decimals
        t = t.replace(".", "").replace(",", ".")
    else:                                              # English
        t = t.replace(",", "")
    d = Decimal(t)
    return -d if neg else d


def first(pattern, text, group=1):
    m = re.search(pattern, text, re.M)
    return m.group(group).strip() if m else None


def iso(date_text):
    return datetime.strptime(date_text, "%d %B %Y").strftime("%Y-%m-%d") if date_text else None


def split_place(line):
    """'EC1M 3JB London' / '1017 BP Amsterdam' / '80331 München' -> (postal_code, city)."""
    for pat in (r"^([A-Z]{1,2}\d[A-Z\d]? \d[A-Z]{2}) (.+)$",   # UK
                r"^(\d{4} [A-Z]{2}) (.+)$",                     # Netherlands
                r"^(\d{4,5}) (.+)$"):
        m = re.match(pat, line)
        if m:
            return m.group(1), m.group(2)
    return None, line


def parse_items(lines):
    """Line items from the table rows and/or the 'we invoice' sentences."""
    items, buffer, in_table = [], "", False
    row = re.compile(rf"^(.+?) (-?\d+) ({NUM}) ({NUM})$")
    calc = re.compile(rf"^(.+): (-?\d+) × ({NUM})(?: EUR)? = ({NUM})(?: EUR)?\.$")
    flat = re.compile(rf"^For (.+), we invoice ({NUM})(?: EUR)?\.$")
    for line in lines:
        if line.startswith("Description Qty Unit Price Amount"):
            in_table = True
            continue
        if STOP.match(line) or line.startswith("For the services rendered"):
            in_table, buffer = False, ""
            continue
        if in_table:
            m = row.match(line)
            if m:
                items.append({"description": m.group(1), "quantity": int(m.group(2)),
                              "unit_price": amount(m.group(3)), "amount": amount(m.group(4))})
            elif items:                                # wrapped description
                items[-1]["description"] += " " + line
            continue
        # sentences can wrap over several lines: collect until one ends with a full stop
        if buffer or re.search(r" × |we invoice", line):
            buffer = f"{buffer} {line}".strip()
            if not buffer.endswith("."):
                continue
            m, f = calc.match(buffer), flat.match(buffer)
            if m:
                items.append({"description": m.group(1), "quantity": int(m.group(2)),
                              "unit_price": amount(m.group(3)), "amount": amount(m.group(4))})
            elif f:
                a = amount(f.group(2))
                items.append({"description": f.group(1), "quantity": 1, "unit_price": a, "amount": a})
            buffer = ""
    return items


def parse_discount(text, terms):
    """-> (type, value, description, conditional, amount)"""
    if terms and re.match(r"\d+/\d+ net \d+", terms):          # 2/10 net 30
        pct = Decimal(terms.split("/")[0]) / 100
        return "trade_terms", pct, terms, True, Decimal("0.00")
    m = re.search(rf"^Discount \((\d+(?:\.\d+)?)% ([^)]+)\): ({NUM})", text, re.M)
    if m:
        return "percentage", Decimal(m.group(1)) / 100, m.group(2), False, abs(amount(m.group(3)))
    m = re.search(rf"^Less (\d+(?:\.\d+)?)% ([^(]+) \(({NUM})(?: EUR)?\)", text, re.M)
    if m:
        return "percentage", Decimal(m.group(1)) / 100, m.group(2).strip(), False, abs(amount(m.group(3)))
    m = re.search(rf"^(adjustment [^(:]+?)(?: \((-?\d+(?:\.\d+)?)%\))?: (?:(-?\d+(?:\.\d+)?)% \()?({NUM})(?: EUR)?\)?\.?$", text, re.M)
    if m:
        pct = Decimal((m.group(2) or m.group(3)).lstrip("-")) / 100
        return "percentage", pct, m.group(1).strip(), False, abs(amount(m.group(4)))
    m = re.search(rf"^Less: ([^:]+): ({NUM})", text, re.M)
    if m:
        a = abs(amount(m.group(2)))
        return "amount", a, m.group(1).strip(), False, a
    m = re.search(rf"^Less an? (.+?) of ({NUM})(?: EUR)?\.", text, re.M)
    if m:
        a = abs(amount(m.group(2)))
        return "amount", a, m.group(1).strip(), False, a
    return None, None, None, None, Decimal("0.00")


def parse(pdf_path):
    with pdfplumber.open(pdf_path) as pdf:
        text = "\n".join(p.extract_text() or "" for p in pdf.pages)
    lines = [l.strip() for l in text.splitlines() if l.strip()]

    # header block: "<vendor> Invoice No: X" / "<street> Date: d" / "<postal city> Due: d" / "<country>"
    head = next(i for i, l in enumerate(lines) if re.search(r"(Invoice|Credit Note) No:", l))
    vendor, invoice_id = re.match(r"(.+) (?:Invoice|Credit Note) No: (\S+)", lines[head]).groups()
    v_street, date = re.match(r"(.+) Date: (.+)", lines[head + 1]).groups()
    v_place, due = re.match(r"(.+) Due: (.+)", lines[head + 2]).groups()
    v_postal, v_city = split_place(v_place)
    v_country = COUNTRIES.get(lines[head + 3])

    b = lines.index("Bill to:")
    recipient, r_street, r_place = lines[b + 1], lines[b + 2], lines[b + 3]
    r_place, _, r_country_name = r_place.rpartition(", ")
    r_postal, r_city = split_place(r_place)

    terms = first(r"^Payment terms: (.+)$", text)
    items = parse_items(lines)

    # amounts as printed
    subtotal = first(rf"^(?:Subtotal(?: \(incl\. VAT\))?: |The subtotal (?:including VAT )?is )({NUM})", text)
    total = first(rf"^(?:TOTAL: |The total amount due is )({NUM})", text)
    subtotal = amount(subtotal) if subtotal else None
    total = amount(total) if total else None

    vat_lines = re.findall(rf"^(?:Statutory )?VAT(?: \((\d+(?:\.\d+)?)%\))?(?: applies)?: ({NUM})", text, re.M)
    vat_lines += [(r, a) for r, a in re.findall(rf"^Plus (?:(\d+)% VAT on the subtotal, amounting to|statutory VAT of) ({NUM})", text, re.M)]
    stated_rate = first(r"(\d+(?:\.\d+)?)% VAT", text) or first(r"VAT \((\d+(?:\.\d+)?)%\)", text)
    included = bool(re.search(r"include \d+% VAT|incl\. VAT|including VAT", text))
    reverse = "reverse charge" in text

    vat_amount, vat_rate = None, None
    if reverse:
        vat_amount, vat_rate = Decimal("0.00"), Decimal("0")
    elif len(vat_lines) > 1:                                     # several VAT rates: no single rate
        vat_amount = sum(amount(a) for _, a in vat_lines)
    else:
        if vat_lines:
            vat_amount = amount(vat_lines[0][1])
        if stated_rate:
            vat_rate = Decimal(stated_rate) / 100
        elif vat_amount is not None and subtotal:                  # rate not printed: derive it
            vat_rate = (vat_amount / subtotal).quantize(CENT)

    if included and vat_rate is not None and subtotal is not None:
        # prices are gross: convert to net, which is what the CSV records
        f = 1 + vat_rate
        gross = subtotal
        subtotal = (gross / f).quantize(CENT, ROUND_HALF_UP)
        vat_amount = gross - subtotal
        for it in items:
            it["unit_price"] = (it["unit_price"] / f).quantize(CENT, ROUND_HALF_UP)
            it["amount"] = (it["amount"] / f).quantize(CENT, ROUND_HALF_UP)
    elif vat_amount is None and vat_rate is not None and subtotal is not None:
        vat_amount = (subtotal * vat_rate).quantize(CENT, ROUND_HALF_UP)   # rate printed, amount not

    d_type, d_value, d_desc, d_cond, d_amount = parse_discount(text, terms)

    for it in items:
        it["vat_rate"] = vat_rate
    return {
        "invoice_id": invoice_id,
        "is_credit_note": "CREDIT NOTE" in lines[:3],
        "vendor": vendor, "vendor_country": v_country,
        "vendor_address": {"street": v_street, "postal_code": v_postal, "city": v_city},
        "recipient": recipient, "recipient_country": COUNTRIES.get(r_country_name),
        "recipient_address": {"street": r_street, "postal_code": r_postal, "city": r_city},
        "date": iso(date), "due_date": iso(due),
        "line_items": items,
        "subtotal": subtotal, "vat_rate": vat_rate, "vat_amount": vat_amount,
        "discount": d_type and {"type": d_type, "value": d_value, "description": d_desc, "conditional": d_cond},
        "discount_amount": d_amount, "total": total,
        "currency": "EUR" if "EUR" in text else None,
        "payment_terms": terms,
        "bank_details": first(r"^Bank: (.+)$", text),
    }


if __name__ == "__main__":
    invoices = []
    for pdf in sorted(PDF_DIR.glob("*.pdf")):
        try:
            invoices.append(parse(pdf))
        except Exception as e:                       # a failed invoice stays visible as a blank row
            print(f"{pdf.name}: {type(e).__name__}: {e}")
            invoices.append({"invoice_id": pdf.stem})
    write_csv(invoices, OUT)
    print(f"wrote {OUT}: {len(invoices)} invoices")
