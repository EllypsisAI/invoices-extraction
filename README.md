# Invoice extraction: four methods compared

Extracting 50 supplier invoices (PDF) into CSV with four methods, each scored field by field
against the provided ground truth.

Course: AI in Accounting and Business (B150316101), SDU, autumn 2026. Assignment 1, invoice case.

## The four methods

| # | Method | Code | Output |
|---|---|---|---|
| 1 | Rules, no LLM: `pdfplumber` text + regular expressions | [`method1_rules.py`](method1_rules.py) | [`output/method1_rules.csv`](output/method1_rules.csv) |
| 2 | Low-cost LLM: Gemini 3.5 Flash-Lite reads the PDF | [`llm_extract.py`](llm_extract.py) | [`output/method2_gemini-3.5-flash-lite.csv`](output/method2_gemini-3.5-flash-lite.csv) |
| 3 | More expensive LLM: Gemini 3.1 Pro (preview), same prompt | [`llm_extract.py`](llm_extract.py) | [`output/method3_gemini-3.1-pro-preview.csv`](output/method3_gemini-3.1-pro-preview.csv) |
| 4 | Hybrid: Flash-Lite only reads, Python recalculates and flags | [`method4_hybrid.py`](method4_hybrid.py) | [`output/method4_hybrid.csv`](output/method4_hybrid.csv) |

Method 4 was designed from the mistakes of Methods 1-3. All three copied subtotals and totals
that the invoices print wrongly (22 of the 50 invoices contain such an error), so in Method 4 the
model does no arithmetic: it reports the numbers as printed, and Python recomputes line amounts,
subtotal, VAT, discount and total. Where the invoice disagrees with its own arithmetic, the
computed value is used and the invoice is flagged for a person to review (`review_flag`,
`review_notes`). The prompt was also tightened against the reading mistakes seen in Method 2
(deducted discounts read as conditional, quantities taken from descriptions, labels kept).

## Results

| | Method 1 | Method 2 | Method 3 | Method 4 |
|---|---|---|---|---|
| Invoices fully correct | 18/50 | 7/50 | 0/50 | 35/50 |
| Correct apart from labels and wording | 23/50 | 20/50 | 29/50 | 45/50 |
| Totals correct | 78% | 78% | 78% | 100% |
| Cost for 50 invoices | $0 | $0 (free tier) | $2.25 | $0.11 |

Method 4 flagged 24 invoices for review, including all 22 with a printed arithmetic error.
Mistakes by type: [`results/mistake_types.md`](results/mistake_types.md). Every wrong value, with
the expected value and what the invoice printed: `results/*_mistakes.csv`.

## CSV layout

One row per line item; the invoice fields (vendor, dates, totals ...) are repeated on each row.
Nothing is lost, each row reads like a booked transaction line, and line items can be scored one
by one. Columns are defined in [`schema.py`](schema.py).

## Scoring

[`score.py`](score.py) compares a CSV with the ground truth: amounts and rates as numbers to the
cent, text ignoring case, extra spaces and dash style, line items by position. Scoring the ground
truth against itself gives 100%.

## Running it

The course data is expected in a sibling folder `../Invoices/` (`pdf/` and `ground_truth/`); it
is not part of this repository.

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python method1_rules.py
.venv/bin/python llm_extract.py method2 gemini-3.5-flash-lite
.venv/bin/python llm_extract.py method3 gemini-3.1-pro-preview
.venv/bin/python method4_hybrid.py
.venv/bin/python score.py output/*.csv
.venv/bin/python mistake_types.py results/*_mistakes.csv
```

The LLM methods need `GEMINI_API_KEY` in the environment or in a `.env` file outside the
repository. No key is stored here. Raw model responses, with token counts and timings, were kept
locally and are summarised in the cost figures above.

## Limits

- Only Google models were tested, so the results say nothing about other providers.
- The rules (Method 1) and the improved prompt (Method 4) were written using the same 50 invoices
  they were scored on. There is no separate test set, so the scores are probably optimistic.
- The checks in Method 4 catch errors that break the arithmetic. A misreading that still adds up
  (a 2% discount recorded as a fixed amount) is not caught.

The code was written with an AI coding assistant (Claude Code), directed and reviewed by the
author; the method choices and the conclusions were decided in discussion and checked against the
data.
