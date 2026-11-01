"""Methods 2 and 3: extraction with a language model (Google Gemini).

    python llm_extract.py method2 gemini-3.5-flash-lite
    python llm_extract.py method3 <more expensive model>

Both methods use the same prompt, the same output schema and the same settings; only the
model differs. Each PDF is sent to the model as a file, so the model reads the document
itself rather than our text extraction. The model must answer with JSON matching SCHEMA.

Every raw response is saved with its token counts and timing (RAW_DIR), and a model is only
called again for invoices that do not have a saved response yet, so a run that hits a rate
limit can simply be restarted.

The API key is read from the environment or from a .env file outside this repository.
"""
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from pathlib import Path

from google import genai
from google.genai import types

from schema import DATA, PDF_DIR, write_csv

WORKERS = int(os.environ.get("WORKERS", "4"))
RAW_ROOT = DATA / "runs"          # raw responses stay in the local audit trail, not in the repo

PROMPT = """You are extracting data from a supplier invoice for an accounts-payable system.
Read the attached invoice and return its data as JSON matching the schema.

Field meanings:
- All amounts are plain numbers in the invoice currency (no thousands separators, no currency sign).
  Credit notes have negative amounts.
- Addresses are split into street, postal_code and city. Countries are ISO 3166 two-letter codes.
- Dates are YYYY-MM-DD.
- line_items: one entry per invoiced line, in the order shown. unit_price and amount are net of VAT.
  vat_rate is that line's VAT rate as a decimal (0.20 for 20%).
- subtotal: the net total of the line items, before VAT and before any discount.
- vat_rate: the invoice's VAT rate as a decimal; null if the lines carry different rates.
- vat_amount: the total VAT.
- discount: null if there is none. type is "percentage", "amount" or "trade_terms" (an early-payment
  term such as "2/10 net 30"); value is the percentage as a decimal or the fixed amount; description
  is the invoice's own wording; conditional is true if the discount only applies under a condition.
- discount_amount: the amount actually deducted on this invoice (0 if none or if conditional).
- total: the total amount due.
- payment_terms and bank_details: as written on the invoice.
"""

ADDRESS = {"type": "object", "properties": {k: {"type": "string"} for k in ("street", "postal_code", "city")},
           "required": ["street", "postal_code", "city"]}
NUMBER = {"type": "number"}
NULLABLE_NUMBER = {"type": ["number", "null"]}
SCHEMA = {
    "type": "object",
    "properties": {
        "invoice_id": {"type": "string"},
        "is_credit_note": {"type": "boolean"},
        "vendor": {"type": "string"}, "vendor_country": {"type": "string"}, "vendor_address": ADDRESS,
        "recipient": {"type": "string"}, "recipient_country": {"type": "string"}, "recipient_address": ADDRESS,
        "date": {"type": "string"}, "due_date": {"type": "string"},
        "currency": {"type": "string"},
        "line_items": {"type": "array", "items": {
            "type": "object",
            "properties": {"description": {"type": "string"}, "quantity": NUMBER, "unit_price": NUMBER,
                           "vat_rate": NULLABLE_NUMBER, "amount": NUMBER},
            "required": ["description", "quantity", "unit_price", "vat_rate", "amount"]}},
        "subtotal": NUMBER, "vat_rate": NULLABLE_NUMBER, "vat_amount": NUMBER,
        "discount": {"type": ["object", "null"], "properties": {
            "type": {"type": "string", "enum": ["percentage", "amount", "trade_terms"]},
            "value": NUMBER, "description": {"type": "string"}, "conditional": {"type": "boolean"}},
            "required": ["type", "value", "description", "conditional"]},
        "discount_amount": NUMBER, "total": NUMBER,
        "payment_terms": {"type": "string"}, "bank_details": {"type": "string"},
    },
    "required": ["invoice_id", "is_credit_note", "vendor", "vendor_country", "vendor_address", "recipient",
                 "recipient_country", "recipient_address", "date", "due_date", "currency", "line_items",
                 "subtotal", "vat_rate", "vat_amount", "discount", "discount_amount", "total",
                 "payment_terms", "bank_details"],
}


def api_key():
    if os.environ.get("GEMINI_API_KEY"):
        return os.environ["GEMINI_API_KEY"]
    for folder in Path(__file__).resolve().parents:
        env = folder / ".env"
        if env.is_file():
            for line in env.read_text().splitlines():
                if line.startswith("GEMINI_API_KEY="):
                    return line.split("=", 1)[1].strip().strip("'\"")
    sys.exit("GEMINI_API_KEY not found")


def as_decimal(x):
    """JSON numbers arrive as floats; go through str() so 0.2 stays 0.2, not 0.2000000000000000111."""
    return None if x is None else Decimal(str(x))


def to_invoice(data):
    """Model JSON -> the ground-truth shape write_csv expects (numbers as Decimal)."""
    for k in ("subtotal", "vat_rate", "vat_amount", "discount_amount", "total"):
        data[k] = as_decimal(data.get(k))
    for li in data.get("line_items") or []:
        for k in ("unit_price", "vat_rate", "amount"):
            li[k] = as_decimal(li.get(k))
        q = li.get("quantity")
        li["quantity"] = int(q) if isinstance(q, float) and q.is_integer() else q
    if data.get("discount"):
        data["discount"]["value"] = as_decimal(data["discount"].get("value"))
    return data


def call(client, model, pdf_path, raw_path, prompt=PROMPT, schema=SCHEMA):
    config = types.GenerateContentConfig(
        temperature=0,
        response_mime_type="application/json",
        response_json_schema=schema,
    )
    part = types.Part.from_bytes(data=pdf_path.read_bytes(), mime_type="application/pdf")
    for attempt in range(6):
        t0 = time.time()
        try:
            resp = client.models.generate_content(model=model, contents=[part, prompt], config=config)
        except Exception as e:                       # rate limit or server hiccup: wait and retry
            msg = str(e)
            if attempt == 5 or not any(c in msg for c in ("429", "500", "503", "RESOURCE_EXHAUSTED", "UNAVAILABLE")):
                raise
            wait = 20 * (attempt + 1)
            print(f"  {pdf_path.stem}: {msg[:90]}... retry in {wait}s", flush=True)
            time.sleep(wait)
            continue
        u = resp.usage_metadata
        record = {
            "invoice": pdf_path.stem, "model": model, "model_version": resp.model_version,
            "seconds": round(time.time() - t0, 2),
            "input_tokens": u.prompt_token_count, "output_tokens": u.candidates_token_count,
            "thinking_tokens": u.thoughts_token_count or 0, "total_tokens": u.total_token_count,
            "text": resp.text,
        }
        raw_path.write_text(json.dumps(record, indent=1, ensure_ascii=False), encoding="utf-8")
        return record


def main(method, model):
    raw_dir = RAW_ROOT / f"{method}_raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    client = genai.Client(api_key=api_key())
    pdfs = sorted(PDF_DIR.glob("*.pdf"))
    todo = [p for p in pdfs if not (raw_dir / f"{p.stem}.json").exists()]
    # a slow (thinking) model takes minutes per invoice, so a few requests run side by side
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        for rec in pool.map(lambda p: call(client, model, p, raw_dir / f"{p.stem}.json"), todo):
            print(f"{rec['invoice']}: {rec['seconds']}s, {rec['total_tokens']} tokens", flush=True)

    invoices, failed = [], []
    for pdf in pdfs:
        record = json.loads((raw_dir / f"{pdf.stem}.json").read_text(encoding="utf-8"))
        try:
            invoices.append(to_invoice(json.loads(record["text"])))
        except (json.JSONDecodeError, TypeError, ValueError) as e:
            failed.append(pdf.stem)
            print(f"  {pdf.stem}: unusable response ({e})")
            invoices.append({"invoice_id": pdf.stem})
    out = f"output/{method}_{model}.csv"
    write_csv(invoices, out)

    recs = [json.loads(p.read_text(encoding="utf-8")) for p in sorted(raw_dir.glob("*.json"))]
    usage = {k: sum(r[k] for r in recs) for k in ("input_tokens", "output_tokens", "thinking_tokens", "total_tokens")}
    usage.update(model=model, invoices=len(recs), seconds=round(sum(r["seconds"] for r in recs), 1), failed=failed)
    (RAW_ROOT / f"{method}_usage.json").write_text(json.dumps(usage, indent=1), encoding="utf-8")
    print(f"wrote {out}\n{usage}")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    main(sys.argv[1], sys.argv[2])
