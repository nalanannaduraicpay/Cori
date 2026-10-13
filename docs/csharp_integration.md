# Cori API: integration guide for the IAS C# app

This guide covers what the IAS front end needs in order to wire up the **Send to Cori** button and show Cori's results. Every payload below was captured from the running service against Oracle, and trimmed only where marked.

* Base URL (dev): `http://localhost:8000`
* Machine-readable spec: [`docs/openapi.json`](openapi.json). Live Swagger UI is at `/docs`. You can generate a typed client with NSwag or `dotnet-openapi` from the spec.
* All bodies are JSON (UTF-8), except the upload, which is `multipart/form-data`.
* No authentication in the demo. Put the service behind the same auth as IAS before production.

## The flow

```
User selects invoices, clicks "Send to Cori"
  POST /batches {invoice_ids, submitted_by}      -> 202 {batch_id}
  loop every 1-2 s:
    GET /batches/{batch_id}                       -> status, processed/invoice_count, per-invoice decision
  until status in (COMPLETED, FAILED, INTERRUPTED)
  for each invoice the user opens:
    GET /invoices/{invoice_id}/decision           -> decision, Cori's summary, reasons, detail
  reviewer corrects data or clears flags:
    POST /invoices/{invoice_id}/feedback
```

**Cori moves the invoice itself.** When it finishes, Cori sets the invoice status to `ADVANCED_TO_APPROVAL` (the invoice then appears in the approval section) or `NEEDS_REVIEW`. The C# app does not need to write the status. Cori never approves payment.

**Which invoices can be sent:** only those whose status is `PENDING` or `NEEDS_REVIEW` (a re-review after a fix), and which have a document attached. When a batch is accepted, its invoices move to `CORI_PROCESSING`, so the same invoice cannot be queued twice.

## Money

Every amount is a **decimal string** plus an ISO 4217 code. Amounts are never JSON numbers, so nothing passes through a floating point value:

```json
{ "amount": "27790.31", "currency": "EUR", "display": "27,790.31 EUR" }
```

Parse with `decimal.Parse(m.Amount, CultureInfo.InvariantCulture)`. `display` is ready to show. `amount` and `currency` can be `null`; for example, `currency` is null when the invoice only shows `$` and Cori could not tell USD from CAD. Respect each currency's minor units: JPY amounts have no decimals, and KWD amounts have 3.

---

## POST /batches: "Send to Cori"

Request:
```json
{ "invoice_ids": [900001, 900002, 900007], "submitted_by": "jdoe" }
```

`202 Accepted`. Processing runs in the background:
```json
{ "batch_id": "44b72880-aff0-4f58-bee1-585e775b20b3", "status": "QUEUED", "invoice_count": 3 }
```

`422` when an invoice can't be sent. Nothing is queued, and every problem is listed:
```json
{ "detail": { "message": "Some invoices cannot be submitted",
              "invoices": { "900001": "status ADVANCED_TO_APPROVAL cannot be sent to Cori",
                            "123456789": "not found" } } }
```

`422` with a list-shaped `detail` means the request body itself was malformed. That is FastAPI's standard validation error; for example, an empty `invoice_ids`:
```json
{ "detail": [ { "type": "too_short", "loc": ["body", "invoice_ids"],
               "msg": "List should have at least 1 item after validation, not 0", "input": [],
               "ctx": { "field_type": "List", "min_length": 1, "actual_length": 0 } } ] }
```

## GET /batches/{batch_id}: progress

Real response (items trimmed to two):
```json
{
  "batch_id": "44b72880-aff0-4f58-bee1-585e775b20b3",
  "submitted_by": "run_demo",
  "submitted_at": "2026-10-13T19:17:26",
  "finished_at": "2026-10-13T19:17:27",
  "status": "COMPLETED",
  "invoice_count": 14,
  "processed": 14,
  "decision_counts": { "ADVANCED_TO_APPROVAL": 6, "NEEDS_REVIEW": 8 },
  "items": [
    { "invoice_id": 900007, "file_name": "07_bank_details_mismatch.pdf", "status": "DONE",
      "current_step": "decided", "updated_at": "2026-10-13T19:17:27", "decision": "NEEDS_REVIEW",
      "summary": "Needs review (1 issue): Beneficiary account LV10...6789 (Baltic Offshore Credit Bank, BOCBLV22) does not match any active account on file (Rheinland Handelsbank AG DE89...3000). Possible payment fraud - verify with the vendor through a known contact." },
    { "invoice_id": 900008, "file_name": "08_line_items_do_not_sum.pdf", "status": "DONE",
      "current_step": "decided", "updated_at": "2026-10-13T19:17:27", "decision": "NEEDS_REVIEW",
      "summary": "Needs review (1 issue): Line items sum to 17,749.68 EUR but the subtotal is 18,249.68 EUR (difference 500.00 EUR)." }
  ]
}
```

| Field | Values |
|---|---|
| batch `status` | `QUEUED` → `RUNNING` → `COMPLETED`; or `FAILED` / `INTERRUPTED` (server stopped; unfinished invoices go back to `PENDING` and can be resent) |
| item `status` | `QUEUED`, `RUNNING`, `DONE`, `ERROR` |
| item `current_step` | `document_loader`, `extractor_a`/`_b`/`_c`, `content_guardrail`, `consensus_engine`, `validation_agent`, `anomaly_agent`, `decided` (use it for a progress indicator) |
| item `decision` | present once `status` is `DONE` |

Timestamps are UTC without an offset (`2026-10-13T19:17:26`).

## GET /invoices/{invoice_id}/decision: result detail

Real response for an advanced EUR invoice. Arrays are trimmed to one or two elements, and `consensus.field_status` to four fields:
```json
{
  "invoice_id": 900002,
  "file_name": "02_clean_eur_kessler.pdf",
  "status": "ADVANCED_TO_APPROVAL",
  "decision": "ADVANCED_TO_APPROVAL",
  "batch_id": "44b72880-aff0-4f58-bee1-585e775b20b3",
  "summary": "Advanced: 2 extractors agreed on all critical fields, validation passed, total of 27,790.31 EUR is within the normal range for matter 2024-0301 (median 30,665.08 USD). The closest 5 invoices for this matter averaged 31,187.30 USD.",
  "decided_at": "2026-10-13T19:17:26",
  "reasons": [],
  "invoice": {
    "vendor_name": "Kessler & Partner GmbH", "vendor_id": "V003",
    "invoice_number": "KP-2026-5002", "invoice_date": "2026-10-10",
    "client_number": "C1003", "matter_number": "2024-0301", "currency": "EUR",
    "currency_resolution": { "currency": "EUR", "method": "explicit_iso", "ambiguous": false,
                             "candidates": ["EUR"], "message": "Currency EUR stated on the invoice." },
    "subtotal":   { "amount": "23353.20", "currency": "EUR", "display": "23,353.20 EUR" },
    "tax":        { "amount": "4437.11",  "currency": "EUR", "display": "4,437.11 EUR" },
    "total":      { "amount": "27790.31", "currency": "EUR", "display": "27,790.31 EUR" },
    "total_base": { "amount": "29981.87", "currency": "USD", "display": "29,981.87 USD" },
    "line_items": [
      { "description": "Review data room documents", "timekeeper": "K. Kessler", "quantity": "18.70",
        "unit_price": { "amount": "561.60", "currency": "EUR", "display": "561.60 EUR" },
        "amount":     { "amount": "10501.92", "currency": "EUR", "display": "10,501.92 EUR" } }
    ]
  },
  "fx": { "rate": "1.078860731", "rate_date": "2026-10-10", "pair": "EUR->USD" },
  "consensus": {
    "passed": true, "successful_extractors": ["extractor_a", "extractor_b"], "failed_extractors": [],
    "field_status": { "vendor_name": "unanimous", "vendor_id": "missing", "vendor_country": "unanimous",
                      "invoice_number": "unanimous" },
    "disagreements": [], "notes": []
  },
  "extractions": [
    { "name": "extractor_a", "method": "vision", "provider": "mock", "model": "mock-parser-A",
      "prompt_version": "extractor_vision.v1#2a8c646c", "status": "ok", "error": null, "normalized": { "...": "..." } },
    { "name": "extractor_b", "method": "text", "provider": "mock", "model": "mock-parser-B",
      "prompt_version": "extractor_text.v1#e5faed94", "status": "ok", "error": null, "normalized": { "...": "..." } }
  ],
  "validation_checks": [
    { "name": "content_safety", "passed": true, "code": null, "severity": "INFO",
      "message": "No instruction-like text aimed at AI systems.", "details": {} },
    { "name": "vendor_master", "passed": true, "code": null, "severity": "INFO",
      "message": "Vendor matched: Kessler & Partner GmbH (V003).", "details": { "vendor_id": "V003", "match_score": 100.0 } }
  ],
  "anomaly": {
    "flags": [],
    "baseline": { "level": "matter", "level_key": "7", "count": 56, "levels_tried": ["matter:56"], "base_currency": "USD",
                  "median": { "amount": "30665.08", "currency": "USD", "display": "30,665.08 USD" },
                  "p95":    { "amount": "43597.40", "currency": "USD", "display": "43,597.40 USD" },
                  "native_currency": "EUR",
                  "native_median": { "amount": "28485.51", "currency": "EUR", "display": "28,485.51 EUR" } },
    "z_score": -0.29,
    "similar_avg": { "amount": "31187.30", "currency": "USD", "display": "31,187.30 USD" },
    "similar_invoices": [
      { "invoice_id": 1249, "invoice_number": "DA-2026-1046", "vendor_name": "Dubois Avocats S.A.",
        "invoice_date": "2026-04-18", "currency": "EUR",
        "total":      { "amount": "28435.68", "currency": "EUR", "display": "28,435.68 EUR" },
        "total_base": { "amount": "30619.65", "currency": "USD", "display": "30,619.65 USD" },
        "distance": 0.1659 }
    ]
  },
  "feedback": []
}
```

For a `NEEDS_REVIEW` invoice, `reasons` holds every issue, most severe first:
```json
"reasons": [
  { "code": "CURRENCY_AMBIGUOUS", "source": "validation", "severity": "HIGH", "blocking": true,
    "message": "Ambiguous currency symbol: vendor bills in USD, CAD and the invoice does not state an ISO code." }
]
```

What to show, and what to key on:

* **`summary`**: one plain-language paragraph for the AP user.
* **`reasons[]`**: switch on the stable `code` values (the full list is in the README, under "Reason codes"). `blocking: false` marks an informational note, such as `COLD_START_BASELINE`, on an invoice that still advanced.
* **`consensus.disagreements[]`**: each entry has `field`, `critical`, a `values` map of extractor name to value, and `resolution`. This is what a side-by-side "the extractors disagree" view needs.
* **`decision` is `null`**: the invoice has not been processed yet.

## POST /invoices/{invoice_id}/feedback: reviewer corrections

Request:
```json
{
  "reviewer": "ap.reviewer",
  "corrections": [ { "field_name": "bank_account", "agent_value": "LV10...", "corrected_value": "DE89..." } ],
  "cleared_flags": ["BANK_DETAILS_MISMATCH"]
}
```

`201 Created`:
```json
{ "invoice_id": 900007, "feedback_ids": ["5a0c...", "e41b..."] }
```

Feedback is stored in `REVIEW_FEEDBACK` for audit and tuning. It does not change the invoice status; moving a reviewed invoice on is still done in IAS. To have Cori re-check an invoice after a fix, send it in a new batch (`NEEDS_REVIEW` invoices are accepted).

## Other endpoints

| Method + path | Purpose |
|---|---|
| `GET /health` | `{"status": "ok", "database": "ok", "db_backend": "oracle", "llm_provider": "...", "extractors": [...]}`. `status` is `degraded` when the database is unreachable. |
| `POST /invoices/upload` | Demo only: multipart `file` (PDF, max 20 MB). Registers a `PENDING` invoice and returns `201 {"invoice_id", "file_name", "status"}`. Returns `415` if the file isn't a PDF and `413` if it's too large. In IAS, invoices already exist; Cori reads `INVOICES.FILE_PATH`. |
| `GET /invoices?status=PENDING` | Uploaded invoices with their status and latest decision |
| `GET /batches?limit=20` | Recent batches |
| `GET /invoices/{id}/audit` | Every agent step (`AGENT_RUNS`): model, prompt version, raw output, timings |
| `GET /invoices/{id}/pdf` | The stored PDF |

## C# example (.NET Framework 4.8 or .NET 8)

Uses `HttpClient` and the System.Text.Json NuGet package. These are plain classes, so they don't need C# 9 records.

```csharp
using System;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using System.Net.Http;
using System.Text;
using System.Text.Json;
using System.Text.Json.Serialization;
using System.Threading.Tasks;

public class CoriClient
{
    private static readonly JsonSerializerOptions Json = new JsonSerializerOptions { PropertyNameCaseInsensitive = true };
    private readonly HttpClient _http;

    public CoriClient(string baseUrl) =>
        _http = new HttpClient { BaseAddress = new Uri(baseUrl), Timeout = TimeSpan.FromSeconds(30) };

    // The "Send to Cori" button.
    public async Task<string> SendToCoriAsync(IEnumerable<long> invoiceIds, string userName)
    {
        var body = JsonSerializer.Serialize(new { invoice_ids = invoiceIds.ToArray(), submitted_by = userName });
        var resp = await _http.PostAsync("batches", new StringContent(body, Encoding.UTF8, "application/json"));
        var text = await resp.Content.ReadAsStringAsync();
        if ((int)resp.StatusCode == 422)
            throw new InvalidOperationException("Cori rejected the batch: " + text);  // detail.invoices lists each problem
        resp.EnsureSuccessStatusCode();
        return JsonSerializer.Deserialize<BatchAccepted>(text, Json).BatchId;
    }

    public async Task<BatchStatus> WaitForBatchAsync(string batchId, IProgress<BatchStatus> progress = null)
    {
        while (true)
        {
            var s = JsonSerializer.Deserialize<BatchStatus>(await _http.GetStringAsync("batches/" + batchId), Json);
            progress?.Report(s);
            if (s.Status == "COMPLETED" || s.Status == "FAILED" || s.Status == "INTERRUPTED") return s;
            await Task.Delay(1500);
        }
    }

    public async Task<Decision> GetDecisionAsync(long invoiceId) =>
        JsonSerializer.Deserialize<Decision>(await _http.GetStringAsync($"invoices/{invoiceId}/decision"), Json);
}

public class BatchAccepted { [JsonPropertyName("batch_id")] public string BatchId { get; set; } }

public class BatchStatus
{
    [JsonPropertyName("batch_id")] public string BatchId { get; set; }
    public string Status { get; set; }
    [JsonPropertyName("invoice_count")] public int InvoiceCount { get; set; }
    public int Processed { get; set; }
    public List<BatchItem> Items { get; set; }
}

public class BatchItem
{
    [JsonPropertyName("invoice_id")] public long InvoiceId { get; set; }
    public string Status { get; set; }
    [JsonPropertyName("current_step")] public string CurrentStep { get; set; }
    public string Decision { get; set; }   // ADVANCED_TO_APPROVAL | NEEDS_REVIEW | null
    public string Summary { get; set; }
}

public class Money
{
    public string Amount { get; set; }
    public string Currency { get; set; }
    public string Display { get; set; }
    public decimal? Value => Amount == null ? (decimal?)null : decimal.Parse(Amount, CultureInfo.InvariantCulture);
}

public class Reason
{
    public string Code { get; set; }
    public string Source { get; set; }
    public string Severity { get; set; }
    public string Message { get; set; }
    public bool Blocking { get; set; }
}

public class InvoiceFields
{
    [JsonPropertyName("vendor_name")] public string VendorName { get; set; }
    [JsonPropertyName("invoice_number")] public string InvoiceNumber { get; set; }
    [JsonPropertyName("matter_number")] public string MatterNumber { get; set; }
    public string Currency { get; set; }
    public Money Total { get; set; }
    [JsonPropertyName("total_base")] public Money TotalBase { get; set; }
}

public class Decision
{
    [JsonPropertyName("invoice_id")] public long InvoiceId { get; set; }
    [JsonPropertyName("decision")] public string Outcome { get; set; }
    public string Summary { get; set; }
    public List<Reason> Reasons { get; set; }
    public InvoiceFields Invoice { get; set; }
}
```

Usage from the button handler:
```csharp
var cori = new CoriClient("http://localhost:8000/");
var batchId = await cori.SendToCoriAsync(selectedInvoiceIds, Environment.UserName);
var result = await cori.WaitForBatchAsync(batchId, new Progress<BatchStatus>(s => statusLabel.Text = $"{s.Processed}/{s.InvoiceCount}"));
foreach (var item in result.Items.Where(i => i.Decision == "NEEDS_REVIEW"))
    reviewQueue.Add(item.InvoiceId, item.Summary);
```

## Operational notes

* Keep polling at 1–2 seconds; a batch status call is a handful of indexed queries. With the mock extractors, a 14-invoice batch finishes in about 2 seconds. With real LLM extractors, expect several seconds per invoice. `max_concurrent_invoices` in `config.yaml` (default 4) controls parallelism.
* Batches run inside the API process. If the service restarts mid-batch, the batch becomes `INTERRUPTED` and its unfinished invoices return to `PENDING`. Resubmit them.
* All Cori writes go through the `CORI_AGENT` database user, which can't change vendor, bank, FX, or history data (see the README, under Guardrails).
