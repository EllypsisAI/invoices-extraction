"""Score extracted CSVs against the ground truth, field by field.

    python score.py output/method1_rules.csv [output/method2_....csv ...]

For each CSV it writes results/<name>_mistakes.csv (every wrong value, with the expected one)
and prints per-field accuracy. Given several CSVs it also writes results/comparison.md.

Matching rules, kept deliberately simple:
- amounts and rates are compared as numbers to the cent (0.2 == 0.20, "1,000" is not parsed here:
  a method must output plain numbers);
- text is compared after ignoring case, extra spaces and typographic dashes/quotes;
- line items are matched by position (line 1 to line 1); a missing or extra line counts as wrong.
"""
import csv
import re
import sys
import unicodedata
from decimal import Decimal, InvalidOperation
from pathlib import Path

from schema import INVOICE_FIELDS, LINE_FIELDS, flatten, load_truth, read_csv

RESULTS = Path(__file__).resolve().parent / "results"
NUMERIC = {"subtotal", "vat_rate", "vat_amount", "discount_value", "discount_amount", "total",
           "quantity", "unit_price", "line_vat_rate", "amount"}
SCORED_HEAD = [f for f in INVOICE_FIELDS if f != "invoice_id"]
SCORED_LINE = [f for f in LINE_FIELDS if f != "line_no"]


def norm_text(s):
    s = unicodedata.normalize("NFC", s or "")
    s = re.sub(r"[‐-―−]", "-", s)          # all dashes -> hyphen
    s = re.sub(r"[‘’“”]", "'", s).replace('"', "'")
    return re.sub(r"\s+", " ", s).strip().casefold()


def norm_num(s):
    s = (s or "").strip()
    if s == "":
        return None
    try:
        return Decimal(s).quantize(Decimal("0.0001"))
    except InvalidOperation:
        return ("unparseable", s)


def same(field, expected, got):
    if field in NUMERIC:
        return norm_num(expected) == norm_num(got)
    return norm_text(expected) == norm_text(got)


def score(csv_path, truth):
    got = read_csv(csv_path)
    mistakes = []
    head_ok = {f: 0 for f in SCORED_HEAD}
    line_ok = {f: 0 for f in SCORED_LINE}
    n_lines = 0
    perfect = 0
    for inv in truth:
        iid = inv["invoice_id"]
        rows = flatten(inv)
        exp_head, exp_lines = rows[0], rows
        g = got.get(iid, {"head": {}, "lines": []})
        wrong = 0
        for f in SCORED_HEAD:
            e, a = exp_head[f], g["head"].get(f, "")
            if same(f, e, a):
                head_ok[f] += 1
            else:
                wrong += 1
                printed = inv.get(f"rendered_{f}", "") if f in ("subtotal", "total") else ""
                mistakes.append([iid, "", f, e, a, printed, inv["variants"]["consistency"]])
        for i, e_line in enumerate(exp_lines):
            n_lines += 1
            a_line = g["lines"][i] if i < len(g["lines"]) else {}
            for f in SCORED_LINE:
                if same(f, e_line[f], a_line.get(f, "")):
                    line_ok[f] += 1
                else:
                    wrong += 1
                    mistakes.append([iid, i + 1, f, e_line[f], a_line.get(f, ""), "", ""])
        for j in range(len(exp_lines), len(g["lines"])):   # extra lines the truth does not have
            wrong += 1
            mistakes.append([iid, j + 1, "extra_line", "", g["lines"][j].get("description", ""), "", ""])
        perfect += wrong == 0

    name = Path(csv_path).stem
    RESULTS.mkdir(exist_ok=True)
    with open(RESULTS / f"{name}_mistakes.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["invoice_id", "line_no", "field", "expected", "got", "printed_on_invoice", "invoice_consistency"])
        w.writerows(mistakes)

    n = len(truth)
    acc = {f: head_ok[f] / n for f in SCORED_HEAD}
    acc.update({f"line_{f}" if not f.startswith("line_") else f: line_ok[f] / n_lines for f in SCORED_LINE})
    return {"name": name, "acc": acc, "perfect": perfect, "n": n, "n_lines": n_lines,
            "head_cells": sum(head_ok.values()) / (n * len(SCORED_HEAD)),
            "line_cells": sum(line_ok.values()) / (n_lines * len(SCORED_LINE)),
            "missing": n - len(set(got) & {i["invoice_id"] for i in truth})}


def main(paths):
    truth = load_truth()
    results = [score(p, truth) for p in paths]
    fields = list(results[0]["acc"])
    width = max(len(f) for f in fields) + 2
    print("field".ljust(width) + "".join(r["name"][:22].rjust(24) for r in results))
    for f in fields:
        print(f.ljust(width) + "".join(f"{r['acc'][f]:24.0%}" for r in results))
    for label, key in [("invoice fields correct", "head_cells"), ("line-item fields correct", "line_cells")]:
        print(label.ljust(width) + "".join(f"{r[key]:24.1%}" for r in results))
    print("invoices fully correct".ljust(width) + "".join(f"{r['perfect']:>21}/{r['n']}" for r in results))

    if len(results) > 1:
        lines = ["| Field | " + " | ".join(r["name"] for r in results) + " |",
                 "|---|" + "---|" * len(results)]
        for f in fields:
            lines.append(f"| {f} | " + " | ".join(f"{r['acc'][f]:.0%}" for r in results) + " |")
        lines.append("| **Invoices fully correct** | " + " | ".join(f"{r['perfect']}/{r['n']}" for r in results) + " |")
        (RESULTS / "comparison.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main(sys.argv[1:] or sys.exit(__doc__))
