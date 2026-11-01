| Mistake (invoices affected) | method1_rules | method2_gemini-3.5-flash-lite | method3_gemini-3.1-pro-preview |
|---|---|---|---|
| Copied a subtotal or total that the invoice itself prints wrongly | 21 | 16 | 16 |
| Prices include VAT but no rate is printed: left gross, VAT blank or 0 | 3 | 3 | 3 |
| Deducted early-payment discount treated as conditional (not deducted) | 0 | 9 | 2 |
| VAT rate left blank although it can be inferred | 0 | 3 | 0 |
| Quantity or unit price invented from the description ("50 seats" -> qty 50) | 0 | 2 | 0 |
| Two VAT rates on one invoice: per-line rate not stated | 1 | 1 | 0 |
| PDF text layer turns "↔" into "«" | 6 | 0 | 0 |
| Other | 0 | 1 | 0 |
| Kept a label or extra words ("Bank: ", "For ...", "2% early payment discount") | 0 | 26 | 50 |
| Discount described in the invoice's words, not the answer key's | 10 | 10 | 10 |
| **Invoices correct apart from labels and wording** | 23/50 | 20/50 | 29/50 |
