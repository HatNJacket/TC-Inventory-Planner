"""
AI-powered document extraction via OpenRouter API.
Sends PDFs/images to an LLM to extract structured table data as CSV.
"""
import base64
import io
import json
import logging
import os
import httpx

logger = logging.getLogger(__name__)

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
DEFAULT_MODEL = os.getenv("OPENROUTER_MODEL", "moonshotai/kimi-k2.5")


async def extract_table_from_pdf(pdf_bytes: bytes, model: str = None) -> dict:
    """
    Send a PDF to OpenRouter and extract table data as CSV.
    Returns {'status': 'ok', 'csv_content': '...', 'columns': [...]}
    """
    if not OPENROUTER_API_KEY:
        raise ValueError("OPENROUTER_API_KEY environment variable not set. Add it in Azure App Settings.")

    model = model or DEFAULT_MODEL
    pdf_b64 = base64.standard_b64encode(pdf_bytes).decode("ascii")

    prompt = """Extract ALL tabular data from this document into CSV format.

Rules:
- Output ONLY the CSV data, no explanation or markdown fences
- Use comma as delimiter
- Include a header row with column names
- Include ALL rows from ALL pages
- For multi-line cell content, collapse into a single line
- Remove currency symbols ($) from numbers
- Keep numbers as plain numbers (no commas in numbers)
- If a cell is empty, leave it empty between commas
- Preserve the original column order from the document"""

    payload = {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {
                        "type": "document",
                        "source": {
                            "type": "base64",
                            "media_type": "application/pdf",
                            "data": pdf_b64,
                        },
                    },
                ],
            }
        ],
        "max_tokens": 16000,
        "temperature": 0,
    }

    async with httpx.AsyncClient(timeout=120) as client:
        resp = await client.post(
            OPENROUTER_URL,
            json=payload,
            headers={
                "Authorization": "Bearer " + OPENROUTER_API_KEY,
                "Content-Type": "application/json",
                "HTTP-Referer": "https://tc-planner-app.azurewebsites.net",
                "X-Title": "TC Inventory Planner",
            },
        )

        if resp.status_code != 200:
            error_text = resp.text[:500]
            logger.error("OpenRouter API error %d: %s", resp.status_code, error_text)
            raise ValueError("OpenRouter API error %d: %s" % (resp.status_code, error_text))

        data = resp.json()

    # Extract the text response
    csv_text = ""
    choices = data.get("choices", [])
    if choices:
        message = choices[0].get("message", {})
        csv_text = message.get("content", "")

    # Clean up: remove markdown fences if present
    csv_text = csv_text.strip()
    if csv_text.startswith("```"):
        lines = csv_text.split("\n")
        # Remove first and last fence lines
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        csv_text = "\n".join(lines)

    if not csv_text:
        raise ValueError("AI returned empty response. The PDF may not contain extractable tables.")

    # Parse first line for column headers
    import csv
    reader = csv.reader(io.StringIO(csv_text))
    headers = next(reader, [])

    # Get usage info for cost tracking
    usage = data.get("usage", {})

    return {
        "status": "ok",
        "csv_content": csv_text,
        "columns": [h.strip() for h in headers if h.strip()],
        "model": model,
        "tokens_used": usage.get("total_tokens", 0),
    }
