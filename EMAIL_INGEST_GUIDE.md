# Email Invoice Ingestion — Setup Guide

How to wire up Microsoft 365 → Azure Logic App → TC Planner so that
invoices forwarded to (or received in) a designated mailbox flow into
the **COGS Tracker → 📥 Pending Invoices** review queue automatically.

---

## 1. One-time setup

### 1a. Generate an ingest secret

The webhook is protected with a shared secret in the `X-Ingest-Secret`
header. Pick a long random string (1Password / `openssl rand -base64 32`).

In the Azure Portal:
1. Open the **tc-planner-app** App Service → **Settings → Environment variables**.
2. Add a new App Setting:
   - Name: `EMAIL_INGEST_SECRET`
   - Value: *(your random string)*
3. Save and **restart** the App Service.

The webhook will return **503** until this secret is set (this is
intentional — better to fail loud than to accept unsigned mail).

### 1b. Pick the mailbox

Decide where invoices will land. Two options:

- **Use an existing mailbox** (e.g. `accounting@telescopescanada.ca`)
  and have the supplier emails come there directly, or set up an
  Outlook forwarding rule from your main inbox.
- **Create a dedicated shared mailbox** (e.g. `invoices@telescopescanada.ca`)
  in the M365 admin center and grant yourself access. Easier to keep
  clean.

Forward example: in Outlook, **Rules → Create rule** matching `from
known supplier domains` or `subject contains "invoice"`, action
`forward to invoices@telescopescanada.ca`.

---

## 2. Build the Logic App

### 2a. Create the app

1. In Azure Portal: **Create a resource → Logic App (Consumption)**.
2. Name: `tc-invoice-ingest`. Resource group: same as `tc-planner-app`
   (e.g. `shopify-automation-rg`). Region: same as the App Service.
3. **Review + create**, then open the resource.
4. Click **Logic app designer**.

### 2b. Pick the trigger

Choose **Office 365 Outlook → "When a new email arrives in a shared
mailbox (V2)"** (or "**When a new email arrives (V3)**" if you're using
your personal mailbox).

Sign in with the M365 account that has access to the mailbox.

Set:
- **Original mailbox address**: `invoices@telescopescanada.ca` (or whichever you picked)
- **Folder**: `Inbox`
- **Has Attachments**: `Yes`
- **Include Attachments**: `Yes`
- **Importance**: leave as Any
- **Subject Filter** (optional): blank, or e.g. `invoice` if your forwarding rule prefixes the subject

Save. The trigger now polls every ~3 minutes by default.

### 2c. Add the HTTP action

Click **+ New step** → **HTTP**.

- **Method**: `POST`
- **URI**: `https://tc-planner-app.azurewebsites.net/api/email-ingest/invoice`
- **Headers**:
  - `Content-Type` → `application/json`
  - `X-Ingest-Secret` → *(paste the secret from step 1a)*
- **Body**: paste the JSON below, then use the dynamic-content picker
  to insert the right fields (the `{{...}}` placeholders show what to
  click in the picker — Outlook trigger fields, not literal text):

```json
{
  "message_id": "@{triggerBody()?['Id']}",
  "sender": "@{triggerBody()?['From']}",
  "subject": "@{triggerBody()?['Subject']}",
  "body": "@{triggerBody()?['Body']}",
  "attachments": @{triggerBody()?['Attachments']}
}
```

> **Important — Attachments shape.** The Outlook V2/V3 trigger returns
> `Attachments` as an array where each item has `Name`,
> `ContentBytes` (base64), and `ContentType`. Our endpoint accepts
> exactly that shape via `att.name` / `att.content_base64` *or*
> Outlook-style `att.Name` / `att.ContentBytes`. The Python ingest
> tries both, so the raw array works as-is. If it doesn't, use a
> **Select** action to remap to:
> ```json
> [
>   { "name": "@{item()?['Name']}",
>     "content_base64": "@{item()?['ContentBytes']}" }
> ]
> ```
> and pass the Select output to the body's `attachments` field.

### 2d. Save and test

1. **Save** the Logic App.
2. Click **Run Trigger → Run** to fire once manually with a recent
   matching email, or just send a test email with a PDF attached to
   the watched mailbox.
3. In TC Planner: open **COGS Tracker → 📥 Pending Invoices**. The new
   row should appear within a couple minutes (Logic App polling cadence + LLM extraction time).

---

## 3. Day-to-day flow

1. **Supplier emails an invoice** to your watched mailbox (or you
   forward one in).
2. **Logic App** picks it up within a few minutes.
3. **TC Planner** runs the LLM extractor on each PDF/XLSX/CSV
   attachment, looks up the vendor name in the alias table, and writes
   one **Pending Invoice** row per file.
4. You open **COGS Tracker → 📥 Pending Invoices**, click the row to
   expand:
   - **First time from a new supplier**: the vendor will show with a red
     "no alias" badge. Type your canonical short name (e.g. `ZWO`),
     leave the **Save alias** checkbox checked, and Approve. Future
     emails from that supplier auto-resolve.
   - Edit invoice #, date, currency, and the **Vendor sale invoice**
     flag if it applies.
   - Review the SKU match table. Pick from suggestion dropdowns for
     fuzzy matches or leave unmatched lines (they'll be skipped).
   - **Approve & Import** runs the same `confirm_invoice` flow as
     manual upload.
5. **Reject** removes from queue without importing.

---

## 4. Going to auto-import (later)

Once you've watched the queue for a couple of weeks and trust the
extraction, flip auto-import on:

1. App Service → Environment variables → add `EMAIL_INGEST_AUTO_IMPORT` = `true`.
2. Restart.

The webhook will then auto-import any pending row that satisfies
**all three** guards:

- **Vendor alias resolved** — the printed name maps to a known
  canonical vendor.
- **All items match exactly** — every line is `Exact` or `Saved`
  (no fuzzy or unmatched).
- **Totals reconcile** — the LLM-reported total agrees with the sum
  of `qty × unit_cost` within 1%.

Anything that fails any of those still lands in the queue for manual
review. So you get auto-import on the routine stuff (recurring vendors
sending the same template each month) and a safety net on edge cases
(new vendor, new format, unusual line item).

The Pending list shows `auto-imported` as a separate status filter so
you can audit what the system did unsupervised.

---

## 5. Troubleshooting

### Logic App runs succeed but nothing shows in Pending Invoices

- Open the Logic App → **Run history** → click the latest run → expand
  the HTTP action → look at the response body. A 401 means the secret
  doesn't match; 503 means it's not set on the App Service.
- Check **App Service → Log stream** while triggering the Logic App
  manually — you'll see the FastAPI log line and any exception.

### "Vendor unrecognized" appears for every invoice

You haven't built up the alias table yet. Approve a few invoices with
**Save alias** checked — by the third or fourth email from each supplier
the queue will resolve them automatically.

### LLM returns garbage for a particular vendor

In the Pending detail view there's a model picker on the manual upload
flow — try a different model on a fresh upload of the same file to
see which one handles that supplier's format best. Anthropic's
Claude Sonnet handles structured invoices well; OpenAI's GPT-5 is a
good fallback.

### Attachment with no content

Logic App sometimes hands the body as a stub when an attachment is
> 25 MB. Our ingest skips those with `reason: empty_content`. For
huge invoices, download manually and use the upload modal.

---

## 6. Manual webhook test (no Logic App)

To smoke-test without setting up Logic Apps:

```bash
# Encode a PDF as base64 and POST it
B64=$(base64 -w0 PI_TC20260423.pdf)
curl -X POST https://tc-planner-app.azurewebsites.net/api/email-ingest/invoice \
  -H "Content-Type: application/json" \
  -H "X-Ingest-Secret: <your-secret>" \
  -d "{
    \"message_id\": \"manual-test-1\",
    \"sender\": \"test@example.com\",
    \"subject\": \"manual ingest test\",
    \"body\": \"\",
    \"attachments\": [
      {\"name\": \"PI_TC20260423.pdf\", \"content_base64\": \"$B64\"}
    ]
  }"
```

Then refresh the Pending Invoices tab.

---

## 7. What's stored where

| Where | What |
|---|---|
| `pending_invoices` table | One row per attachment: file blob, LLM extraction output, status, sender, subject, body |
| `vendor_aliases` table | The `printed name → canonical name` map you build up over time |
| `purchase_invoices` / `purchase_lots` | Where rows land *after* you approve — same tables manual uploads use |
| Azure Logic App run history | 30 days of trigger fires + HTTP responses for debugging |

There is no automatic deletion of old pending rows. Once approved or
rejected, they stay around for audit (you can filter them out of the
default view).
