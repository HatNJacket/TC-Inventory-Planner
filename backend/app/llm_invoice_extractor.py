"""
LLM-driven invoice extraction.

Takes a vendor invoice (PDF or XLSX) and returns structured JSON ready to
feed straight into the existing CSV-based ``preview_invoice`` flow:

    {
      "vendor": str,
      "invoice_number": str,
      "invoice_date": "YYYY-MM-DD",
      "currency": "USD" | "CAD" | "EUR" | …,
      "items": [
        {
          "vendor_sku": str,
          "description": str,
          "quantity": int,
          "unit_cost": float,
          "total": float,
          "is_shipping": bool,        # true if line is freight/shipping
          "is_skip": bool,            # true if line should be skipped (e.g. discontinued, qty 0)
        },
        …
      ],
      "model_used": str,
      "tokens_used": int,
      "raw_response": str             # for debugging
    }

Routes through OpenRouter so the same code can call Anthropic Claude or
OpenAI GPT (or any other supported model) — the front-end picks the model
in the upload modal so you can A/B them on the same invoice.
"""
import base64
import csv as _csv
import io
import json
import logging
import os
import re
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

# Model presets. The frontend can pass a raw model id or one of these aliases.
MODEL_PRESETS = {
    "claude": "anthropic/claude-sonnet-4.6",
    "claude-opus": "anthropic/claude-opus-4.6",
    "gpt": "openai/gpt-5",
    "gpt-mini": "openai/gpt-5-mini",
}

DEFAULT_MODEL = MODEL_PRESETS["claude"]


SYSTEM_PROMPT = """You extract structured data from supplier invoices for a telescope retailer.
You will be shown one invoice (PDF page or extracted spreadsheet text) and must return ONLY a JSON object — no prose, no markdown fences.

The schema:
{
  "vendor": "<seller company name as printed>",
  "invoice_number": "<the invoice / proforma / PI number>",
  "invoice_date": "YYYY-MM-DD",
  "currency": "<3-letter ISO code: USD, CAD, EUR, …>",
  "items": [
    {
      "vendor_sku": "<SKU / model / part number>",
      "description": "<product description>",
      "quantity": <integer>,
      "unit_cost": <number, no currency symbol>,
      "total": <number>,
      "is_shipping": <true|false>,
      "is_skip": <true|false>
    }
  ]
}

Rules:
1. The currency comes from the invoice header (e.g. 'USD', '$', 'EUR'). If only a $ sign is shown and the seller is in the US/HK/China, default to USD.
2. invoice_date must be ISO YYYY-MM-DD. Convert formats like '10th, April 2026' or '2026/4/23' or '4/23/2026' accordingly.
3. vendor_sku is the SKU column. If the cell shows multiple aliases on separate lines (e.g. 'F9172A\\n(W9149A)'), use the FIRST one (the primary SKU).
4. quantity is an integer. unit_cost and total are numbers (strip $, commas).
5. Set is_shipping=true for freight, shipping, delivery, handling, or insurance lines. Otherwise false.
6. Set is_skip=true ONLY if the row is explicitly marked Discontinued, Cancelled, or has quantity 0. Otherwise false.
7. Skip blank rows, page totals, subtotals, tax lines (those don't belong in items at all — just omit them).
8. Use raw numeric values; do not round.
9. Output the JSON object and nothing else."""


def _strip_code_fence(text: str) -> str:
    text = (text or "").strip()
    if not text.startswith("```"):
        return text
    lines = text.split("\n")
    if lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines).strip()


def _parse_json_or_raise(raw: str) -> dict:
    """Parse the LLM response JSON. Some models occasionally wrap or prepend
    chatter — fall back to extracting the first {...} block."""
    cleaned = _strip_code_fence(raw)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", cleaned, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(0))
            except json.JSONDecodeError as e:
                raise ValueError("LLM returned invalid JSON: " + str(e))
        raise ValueError("LLM returned no JSON object.")


def _xlsx_to_text(xlsx_bytes: bytes) -> str:
    """Render an XLSX file as a pipe-separated, line-numbered text grid the
    LLM can read. Multi-line cells are preserved verbatim."""
    import openpyxl
    wb = openpyxl.load_workbook(io.BytesIO(xlsx_bytes), data_only=True, read_only=True)
    out = io.StringIO()
    for sheet_name in wb.sheetnames:
        ws = wb[sheet_name]
        out.write("=== Sheet: " + sheet_name + " ===\n")
        for i, row in enumerate(ws.iter_rows(values_only=True), start=1):
            cells = []
            for cell in row:
                if cell is None:
                    cells.append("")
                else:
                    cells.append(str(cell).replace("|", "/"))
            out.write(str(i) + ": " + " | ".join(cells) + "\n")
        out.write("\n")
    wb.close()
    return out.getvalue()


def _build_messages(file_bytes: bytes, file_kind: str):
    """Compose the OpenRouter chat messages depending on file type."""
    user_intro = "Extract this invoice into the JSON schema described in the system prompt."
    if file_kind == "pdf":
        b64 = base64.standard_b64encode(file_bytes).decode("ascii")
        return [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": user_intro},
                    {
                        "type": "file",
                        "file": {
                            "filename": "invoice.pdf",
                            "file_data": "data:application/pdf;base64," + b64,
                        },
                    },
                ],
            },
        ]
    # XLSX / TXT — flatten to text
    grid = _xlsx_to_text(file_bytes) if file_kind == "xlsx" else file_bytes.decode("utf-8-sig", errors="replace")
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_intro + "\n\nDocument contents:\n\n" + grid},
    ]


async def extract_invoice(file_bytes: bytes, file_kind: str,
                          model: Optional[str] = None) -> dict:
    """Extract invoice JSON from a PDF or XLSX. ``file_kind`` is 'pdf' or 'xlsx'.
    ``model`` accepts a preset key ('claude', 'gpt', …) or a raw OpenRouter
    model id."""
    if not OPENROUTER_API_KEY:
        raise ValueError("OPENROUTER_API_KEY environment variable not set. Add it in Azure App Settings.")
    if file_kind not in ("pdf", "xlsx", "txt"):
        raise ValueError("file_kind must be 'pdf', 'xlsx', or 'txt'")

    resolved_model = MODEL_PRESETS.get(model or "", model or DEFAULT_MODEL)
    messages = _build_messages(file_bytes, file_kind)
    payload = {
        "model": resolved_model,
        "messages": messages,
        "max_tokens": 16000,
        "temperature": 0,
    }

    async with httpx.AsyncClient(timeout=180) as client:
        resp = await client.post(
            OPENROUTER_URL,
            json=payload,
            headers={
                "Authorization": "Bearer " + OPENROUTER_API_KEY,
                "Content-Type": "application/json",
                "HTTP-Referer": "https://tc-planner-app.azurewebsites.net",
                "X-Title": "TC Inventory Planner — Invoice Extract",
            },
        )
        if resp.status_code != 200:
            error_text = resp.text[:500]
            logger.error("OpenRouter API error %d: %s", resp.status_code, error_text)
            raise ValueError("OpenRouter API error %d: %s" % (resp.status_code, error_text))
        data = resp.json()

    raw = ""
    choices = data.get("choices", [])
    if choices:
        raw = choices[0].get("message", {}).get("content", "")
    if not raw:
        raise ValueError("LLM returned empty response.")

    parsed = _parse_json_or_raise(raw)
    items = parsed.get("items") or []

    # Defensive numeric coercion — models occasionally return strings.
    cleaned_items = []
    for it in items:
        try:
            qty = int(float(str(it.get("quantity") or 0).replace(",", "")))
            unit_cost = float(str(it.get("unit_cost") or 0).replace("$", "").replace(",", "").strip())
            total = float(str(it.get("total") or 0).replace("$", "").replace(",", "").strip())
        except (TypeError, ValueError):
            qty, unit_cost, total = 0, 0.0, 0.0
        cleaned_items.append({
            "vendor_sku": (it.get("vendor_sku") or "").strip(),
            "description": (it.get("description") or "").strip(),
            "quantity": qty,
            "unit_cost": unit_cost,
            "total": round(total, 2),
            "is_shipping": bool(it.get("is_shipping", False)),
            "is_skip": bool(it.get("is_skip", False)),
        })

    usage = data.get("usage", {})
    return {
        "status": "ok",
        "vendor": (parsed.get("vendor") or "").strip(),
        "invoice_number": (parsed.get("invoice_number") or "").strip(),
        "invoice_date": (parsed.get("invoice_date") or "").strip(),
        "currency": (parsed.get("currency") or "").strip().upper(),
        "items": cleaned_items,
        "model_used": resolved_model,
        "tokens_used": usage.get("total_tokens", 0),
        "prompt_tokens": usage.get("prompt_tokens", 0),
        "completion_tokens": usage.get("completion_tokens", 0),
    }


def items_to_csv(items) -> str:
    """Convert extracted items into the CSV shape the existing
    ``preview_invoice`` parser already understands. Shipping and skip rows
    are dropped here so the import flow only sees real SKU lines."""
    out = io.StringIO()
    writer = _csv.writer(out)
    writer.writerow(["vendor_sku", "description", "quantity", "unit_cost"])
    for it in items:
        if it.get("is_skip") or it.get("is_shipping"):
            continue
        if not it.get("vendor_sku") or not it.get("quantity"):
            continue
        writer.writerow([
            it["vendor_sku"], it.get("description", ""),
            it["quantity"], it["unit_cost"],
        ])
    return out.getvalue()
