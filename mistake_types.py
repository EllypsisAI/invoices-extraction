"""Sort every scored mistake into a cause, so the methods can be compared on substance.

    python mistake_types.py results/<method>_mistakes.csv [...]   -> results/mistake_types.md

The scorer stays strict; this script only explains its output. The rules below look at the
field, the expected and extracted value, what the invoice printed, and the answer key's notes
on which traps the invoice contains. Counts are invoices affected, not cells.
"""
import csv
import json
import sys
from collections import defaultdict
from decimal import Decimal, InvalidOperation
from pathlib import Path

from schema import TRUTH_DIR
from score import norm_text

TYPES = [  # (key, label, counts as a formatting difference only)
    ("printed_error", "Copied a subtotal or total that the invoice itself prints wrongly", False),
    ("vat_incl_no_rate", "Prices include VAT but no rate is printed: left gross, VAT blank or 0", False),
    ("discount_conditional", "Deducted early-payment discount treated as conditional (not deducted)", False),
    ("discount_kind", "Discount recorded as a fixed amount instead of a percentage (amount still right)", False),
    ("vat_rate_missing", "VAT rate left blank although it can be inferred", False),
    ("invented_quantity", "Quantity or unit price invented from the description (\"50 seats\" -> qty 50)", False),
    ("mixed_vat", "Two VAT rates on one invoice: per-line rate not stated", False),
    ("glyph", "PDF text layer turns \"↔\" into \"«\"", False),
    ("other", "Other", False),
    ("label_kept", "Kept a label or extra words (\"Bank: \", \"For ...\", \"2% early payment discount\")", True),
    ("wording", "Discount described in the invoice's words, not the answer key's", True),
]
SUBSTANTIVE = [k for k, _, fmt in TYPES if not fmt]


def num(s):
    try:
        return Decimal(s)
    except (InvalidOperation, TypeError):
        return None


def classify(m, inv, invoice_mistakes):
    f, exp, got, printed = m["field"], m["expected"], m["got"], m["printed_on_invoice"]
    v = inv["variants"]
    e, g = norm_text(exp), norm_text(got)
    if f == "bank_details" and g.startswith("bank:"):
        return "label_kept"
    if f == "description" and g == "for " + e:
        return "label_kept"
    if f == "description" and "«" in got:
        return "glyph"
    if f == "discount_description":
        return "label_kept" if e and e in g else "wording"
    if v["vat_variant"] == "explicit_included" and f in ("subtotal", "vat_rate", "vat_amount", "unit_price",
                                                         "amount", "line_vat_rate") and inv["invoice_id"] in NO_RATE:
        return "vat_incl_no_rate"
    if f in ("subtotal", "total", "vat_amount") and (
            (printed and num(got) == num(printed)) or v["consistency"] != "correct"):
        if f == "total" and "discount_conditional" in invoice_mistakes:
            return "discount_conditional"            # total left without the discount
        return "printed_error"
    if f in ("discount_type", "discount_value") and "discount_conditional" not in invoice_mistakes:
        return "discount_kind"
    if f in ("discount_conditional", "discount_amount", "discount_type", "discount_value"):
        return "discount_conditional"
    if f in ("line_vat_rate", "vat_rate"):
        return "mixed_vat" if v["edge_case"] == "mixed_vat" else "vat_rate_missing"
    if f in ("quantity", "unit_price", "amount") and "quantity" in invoice_mistakes:
        return "invented_quantity"
    if f in ("quantity", "unit_price", "amount"):
        return "printed_error"
    return "other"


# invoices whose prices include VAT without printing the rate (found while reading Method 1's mistakes)
NO_RATE = {"INV-2026-0015", "INV-2026-0029", "INV-2026-0034"}


def summarise(path, truth):
    rows = list(csv.DictReader(open(path, encoding="utf-8")))
    fields_by_inv = defaultdict(set)
    for m in rows:
        fields_by_inv[m["invoice_id"]].add(m["field"])
    hit = defaultdict(set)
    for m in rows:
        k = classify(m, truth[m["invoice_id"]], fields_by_inv[m["invoice_id"]])
        hit[k].add(m["invoice_id"])
    substantive = set().union(*(hit[k] for k in SUBSTANTIVE))
    return hit, len(truth) - len(substantive), rows


def main(paths):
    truth = {json.loads(p.read_text())["invoice_id"]: json.loads(p.read_text()) for p in TRUTH_DIR.glob("*.json")}
    names = [Path(p).stem.replace("_mistakes", "") for p in paths]
    results = [summarise(p, truth) for p in paths]
    lines = ["| Mistake (invoices affected) | " + " | ".join(names) + " |", "|---|" + "---|" * len(names)]
    for key, label, _ in TYPES:
        counts = [len(r[0].get(key, ())) for r in results]
        if any(counts):
            lines.append(f"| {label} | " + " | ".join(map(str, counts)) + " |")
    lines.append("| **Invoices correct apart from labels and wording** | "
                 + " | ".join(f"{r[1]}/50" for r in results) + " |")
    out = Path(__file__).resolve().parent / "results" / "mistake_types.md"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    for name, (hit, _, _) in zip(names, results):
        if hit.get("other"):
            print(name, "other:", sorted(hit["other"]))


if __name__ == "__main__":
    main(sys.argv[1:] or sys.exit(__doc__))
