"""
Email-driven invoice ingestion.

Workflow:
  1. Logic App watches the configured 365 mailbox, POSTs new emails (with
     attachments base64-encoded) to ``/api/email-ingest/invoice``.
  2. ``ingest_email`` filters attachments to PDF/XLSX/CSV, runs each
     through ``llm_invoice_extractor.extract_invoice``, applies the
     vendor alias table, and stores a ``pending_invoices`` row per file.
  3. A human reviews the row on the COGS Tracker → Pending tab, picks
     vendor + sale flag + optional alias save, and clicks Approve. That
     calls ``approve_pending`` which runs the existing ``preview_invoice``
     + ``confirm_invoice`` flow against the queued items, marks the row
     'approved', and links the new invoice id back.

Auto-import (later): once you trust the queue, flipping ``AUTO_IMPORT_ENABLED``
on will skip the human step for rows that satisfy ALL of:
  - vendor alias resolved (vendor_canonical not null)
  - 100% of items match exactly to existing Shopify SKUs
  - the LLM-reported total ≈ the recomputed total within 1%
That logic lives in ``maybe_auto_import`` and currently always returns False
until you set the env var.
"""
import base64
import json
import logging
import os
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from .cogs import preview_invoice, confirm_invoice
from .llm_invoice_extractor import extract_invoice as _extract_invoice

logger = logging.getLogger(__name__)


SUPPORTED_EXTS = {
    "pdf": "pdf",
    "xlsx": "xlsx",
    "xls": "xlsx",
    "csv": "txt",
    "tsv": "txt",
    "txt": "txt",
}

EMAIL_INGEST_SECRET = os.getenv("EMAIL_INGEST_SECRET", "")
AUTO_IMPORT_ENABLED = os.getenv("EMAIL_INGEST_AUTO_IMPORT", "").lower() in ("1", "true", "yes")


def _f(val):
    if val is None:
        return 0.0
    try:
        return float(val)
    except (TypeError, ValueError):
        return 0.0


def _kind_for(filename: str) -> Optional[str]:
    if not filename:
        return None
    name = filename.lower().strip()
    if "." not in name:
        return None
    ext = name.rsplit(".", 1)[-1]
    return SUPPORTED_EXTS.get(ext)


def _serialize_pending(d):
    out = dict(d)
    for k in ("created_at", "reviewed_at"):
        v = out.get(k)
        out[k] = v.isoformat() if v else None
    if out.get("invoice_date"):
        out["invoice_date"] = out["invoice_date"].isoformat()
    out["total_amount"] = _f(out.get("total_amount"))
    # Don't ship raw blobs over the wire by default — pending list/detail strip them.
    out.pop("file_blob", None)
    return out


# --- VENDOR ALIASES --------------------------------------------------------

def list_vendor_aliases(db):
    conn = db._get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id, alias_original, canonical_vendor, created_at
            FROM vendor_aliases ORDER BY canonical_vendor, alias_original
        """)
        return [
            {
                "id": r[0],
                "alias": r[1],
                "canonical_vendor": r[2],
                "created_at": r[3].isoformat() if r[3] else None,
            }
            for r in cursor.fetchall()
        ]
    finally:
        conn.close()


def add_vendor_alias(db, alias, canonical_vendor):
    if not alias or not canonical_vendor:
        raise ValueError("Both alias and canonical_vendor are required.")
    alias = str(alias).strip()
    canonical_vendor = str(canonical_vendor).strip()
    conn = db._get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("""
            MERGE vendor_aliases AS target
            USING (SELECT ? AS alias_lower, ? AS alias_original, ? AS canonical_vendor) AS source
            ON target.alias_lower = source.alias_lower
            WHEN MATCHED THEN UPDATE SET canonical_vendor = source.canonical_vendor,
                                          alias_original = source.alias_original
            WHEN NOT MATCHED THEN INSERT (alias_lower, alias_original, canonical_vendor)
                                  VALUES (source.alias_lower, source.alias_original, source.canonical_vendor);
        """, alias.lower(), alias, canonical_vendor)
        conn.commit()
        return {"status": "ok", "alias": alias, "canonical_vendor": canonical_vendor}
    finally:
        conn.close()


def delete_vendor_alias(db, alias_id):
    conn = db._get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM vendor_aliases WHERE id = ?", alias_id)
        conn.commit()
        return {"status": "ok", "deleted": alias_id}
    finally:
        conn.close()


def _resolve_vendor_alias(cursor, raw_name):
    if not raw_name:
        return None
    cursor.execute(
        "SELECT canonical_vendor FROM vendor_aliases WHERE alias_lower = ?",
        raw_name.lower().strip(),
    )
    row = cursor.fetchone()
    return row[0] if row else None


# --- INGESTION -------------------------------------------------------------

async def ingest_email(db, payload: dict) -> dict:
    """Process an email payload from the Logic App. Returns a summary of
    what was queued. Idempotent on (message_id, file_name)."""
    # Tolerate both lowercase (manual / curl) and Outlook V2/V3 PascalCase
    # field names so Logic App bodies work either way.
    message_id = (payload.get("message_id") or payload.get("Id") or "").strip() or None
    sender = (
        payload.get("from") or payload.get("sender")
        or payload.get("From") or payload.get("Sender") or ""
    ).strip() or None
    subject = (payload.get("subject") or payload.get("Subject") or "").strip() or None
    body_text = (
        payload.get("body") or payload.get("body_text")
        or payload.get("Body") or payload.get("BodyPreview") or ""
    )
    attachments = payload.get("attachments") or payload.get("Attachments") or []

    if not attachments:
        return {"status": "no_attachments", "message_id": message_id}

    # Dedup pre-check
    conn = db._get_connection()
    try:
        cursor = conn.cursor()
        existing_files = set()
        if message_id:
            cursor.execute(
                "SELECT file_name FROM pending_invoices WHERE message_id = ?",
                message_id,
            )
            existing_files = {r[0] for r in cursor.fetchall() if r[0]}

        results = []
        for att in attachments:
            # Accept both lowercase keys (manual posts) and Outlook trigger
            # PascalCase keys (Name / ContentBytes) so Logic Apps can hand
            # the raw attachments array straight in without remapping.
            name = (
                att.get("name") or att.get("filename")
                or att.get("Name") or att.get("FileName") or ""
            ).strip()
            if not name:
                continue
            kind = _kind_for(name)
            if not kind:
                results.append({"file_name": name, "status": "skipped", "reason": "unsupported_extension"})
                continue
            if name in existing_files:
                results.append({"file_name": name, "status": "skipped", "reason": "duplicate"})
                continue

            content_b64 = (
                att.get("content_base64") or att.get("content")
                or att.get("ContentBytes") or att.get("Content") or ""
            )
            if not content_b64:
                results.append({"file_name": name, "status": "skipped", "reason": "empty_content"})
                continue
            try:
                file_bytes = base64.b64decode(content_b64)
            except Exception as e:
                results.append({"file_name": name, "status": "skipped", "reason": "base64_decode_failed: " + str(e)})
                continue

            extraction = None
            error_message = None
            try:
                extraction = await _extract_invoice(file_bytes, kind)
            except Exception as e:
                logger.error("Email ingest extraction failed for %s: %s", name, e, exc_info=True)
                error_message = str(e)

            row = _build_pending_row(
                cursor=cursor,
                message_id=message_id, sender=sender, subject=subject,
                body_text=body_text, file_name=name, file_kind=kind,
                file_bytes=file_bytes, extraction=extraction,
                error_message=error_message,
            )
            cursor.execute(_INSERT_PENDING_SQL, *row["params"])
            inserted_id = cursor.fetchone()[0]
            conn.commit()

            outcome = {
                "file_name": name,
                "pending_id": inserted_id,
                "status": row["status"],
                "vendor_raw": row["vendor_raw"],
                "vendor_canonical": row["vendor_canonical"],
            }

            # Optional auto-import if all guards pass and feature flag is on.
            if AUTO_IMPORT_ENABLED and row["status"] == "pending":
                try:
                    auto = _try_auto_import(db, inserted_id)
                    if auto.get("status") == "ok":
                        outcome["auto_imported"] = True
                        outcome["invoice_id"] = auto.get("invoice_id")
                except Exception as e:
                    logger.warning("Auto-import attempt failed for #%s: %s", inserted_id, e)

            results.append(outcome)

        return {
            "status": "ok",
            "message_id": message_id,
            "queued": [r for r in results if "pending_id" in r],
            "skipped": [r for r in results if r.get("status") == "skipped"],
        }
    finally:
        conn.close()


_INSERT_PENDING_SQL = """
    INSERT INTO pending_invoices
    (status, source, message_id, sender_email, subject, body_text,
     file_name, file_kind, file_blob,
     vendor_raw, vendor_canonical, invoice_number, invoice_date, currency,
     item_count, total_amount, items_json,
     llm_model, tokens_used, error_message)
    OUTPUT INSERTED.id
    VALUES (?, ?, ?, ?, ?, ?,  ?, ?, ?,  ?, ?, ?, ?, ?,  ?, ?, ?,  ?, ?, ?)
"""


def _build_pending_row(*, cursor, message_id, sender, subject, body_text,
                       file_name, file_kind, file_bytes, extraction,
                       error_message):
    """Compose the parameters for inserting a pending row, including alias
    resolution and a status decision."""
    if extraction is None:
        return {
            "status": "extraction_failed",
            "vendor_raw": None,
            "vendor_canonical": None,
            "params": (
                "extraction_failed", "email", message_id, sender, subject, body_text,
                file_name, file_kind, file_bytes,
                None, None, None, None, None,
                0, None, None,
                None, None, error_message,
            ),
        }

    items = extraction.get("items") or []
    item_count = sum(1 for it in items if it.get("vendor_sku") and not it.get("is_skip") and not it.get("is_shipping"))
    total = round(sum(_f(it.get("total")) for it in items if not it.get("is_skip") and not it.get("is_shipping")), 2)
    vendor_raw = (extraction.get("vendor") or "").strip() or None
    vendor_canonical = _resolve_vendor_alias(cursor, vendor_raw) if vendor_raw else None
    invoice_date = extraction.get("invoice_date") or None
    invoice_number = (extraction.get("invoice_number") or "").strip() or None
    currency = (extraction.get("currency") or "").strip().upper() or None

    return {
        "status": "pending",
        "vendor_raw": vendor_raw,
        "vendor_canonical": vendor_canonical,
        "params": (
            "pending", "email", message_id, sender, subject, body_text,
            file_name, file_kind, file_bytes,
            vendor_raw, vendor_canonical, invoice_number, invoice_date, currency,
            item_count, total, json.dumps(items),
            extraction.get("model_used"), extraction.get("tokens_used"), None,
        ),
    }


# --- LIST / DETAIL ---------------------------------------------------------

_PENDING_COLUMNS = """
    id, status, source, message_id, sender_email, subject, body_text,
    file_name, file_kind,
    vendor_raw, vendor_canonical, invoice_number, invoice_date, currency,
    item_count, total_amount, items_json,
    llm_model, tokens_used, error_message, notes,
    created_at, reviewed_at, imported_invoice_id
"""


def list_pending_invoices(db, status_filter=None):
    conn = db._get_connection()
    try:
        cursor = conn.cursor()
        sql = "SELECT " + _PENDING_COLUMNS + " FROM pending_invoices"
        params = []
        if status_filter:
            sql += " WHERE status = ?"
            params.append(status_filter)
        sql += " ORDER BY created_at DESC"
        cursor.execute(sql, *params)
        cols = [d[0] for d in cursor.description]
        rows = []
        for row in cursor.fetchall():
            d = dict(zip(cols, row))
            d["items_json"] = None  # full items omitted from the list view
            rows.append(_serialize_pending(d))
        return rows
    finally:
        conn.close()


def get_pending_invoice(db, pending_id, include_items=True):
    conn = db._get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT " + _PENDING_COLUMNS + " FROM pending_invoices WHERE id = ?", pending_id)
        row = cursor.fetchone()
        if not row:
            return None
        cols = [d[0] for d in cursor.description]
        d = dict(zip(cols, row))
        if include_items and d.get("items_json"):
            try:
                d["items"] = json.loads(d["items_json"])
            except Exception:
                d["items"] = []
        else:
            d["items"] = []
        d.pop("items_json", None)
        return _serialize_pending(d)
    finally:
        conn.close()


# --- APPROVE / REJECT ------------------------------------------------------

def _items_to_csv(items):
    """Render extracted items into the CSV the existing ``preview_invoice``
    parser expects. Skips shipping and is_skip rows."""
    import csv as _csv
    import io as _io
    buf = _io.StringIO()
    w = _csv.writer(buf)
    w.writerow(["vendor_sku", "description", "quantity", "unit_cost"])
    for it in items or []:
        if it.get("is_skip") or it.get("is_shipping"):
            continue
        sku = (it.get("vendor_sku") or "").strip()
        qty = it.get("quantity") or 0
        cost = it.get("unit_cost") or 0
        if not sku or not qty or not cost:
            continue
        w.writerow([sku, it.get("description", ""), qty, cost])
    return buf.getvalue()


def preview_pending_match(db, pending_id, vendor_override=None):
    """Run ``preview_invoice`` against the LLM-extracted items so the UI
    can show match counts before the user approves."""
    pending = get_pending_invoice(db, pending_id, include_items=True)
    if not pending:
        return {"status": "error", "message": "Pending invoice not found."}
    items = pending.get("items") or []
    if not items:
        return {"status": "error", "message": "No items to match."}
    vendor = (vendor_override or pending.get("vendor_canonical") or pending.get("vendor_raw") or "").strip()
    if not vendor:
        return {"status": "error", "message": "Vendor is required for matching."}
    currency = pending.get("currency") or "USD"
    csv_content = _items_to_csv(items)
    return preview_invoice(
        db, vendor, csv_content,
        sku_column="vendor_sku",
        qty_column="quantity",
        cost_column="unit_cost",
        currency=currency,
        description_column="description",
    )


def approve_pending(db, pending_id, *, vendor_override=None,
                    invoice_number_override=None, invoice_date_override=None,
                    currency_override=None, is_vendor_sale=False,
                    save_alias=False, items_override=None):
    """Approve a pending invoice — runs preview + confirm in one shot,
    optionally saves a vendor alias for next time, and marks the pending
    row 'approved' with a back-reference to the new invoice."""
    pending = get_pending_invoice(db, pending_id, include_items=True)
    if not pending:
        return {"status": "error", "message": "Pending invoice not found."}
    if pending["status"] not in ("pending",):
        return {"status": "error", "message": "Pending row is not in 'pending' state (current: " + str(pending["status"]) + ")."}

    items = items_override if items_override is not None else (pending.get("items") or [])
    if not items:
        return {"status": "error", "message": "No items to import."}

    vendor = (vendor_override or pending.get("vendor_canonical") or pending.get("vendor_raw") or "").strip()
    if not vendor:
        return {"status": "error", "message": "Vendor is required to approve."}
    invoice_number = (invoice_number_override or pending.get("invoice_number") or "").strip()
    if not invoice_number:
        return {"status": "error", "message": "Invoice number is required."}
    invoice_date = invoice_date_override or pending.get("invoice_date")
    if not invoice_date:
        return {"status": "error", "message": "Invoice date is required."}
    currency = (currency_override or pending.get("currency") or "USD").upper()

    # Build CSV from the (possibly user-edited) items, then run the same
    # preview the manual flow uses so SKU matching + FX + cost conversion
    # all happen identically.
    csv_content = _items_to_csv(items)
    matched = preview_invoice(
        db, vendor, csv_content,
        sku_column="vendor_sku",
        qty_column="quantity",
        cost_column="unit_cost",
        currency=currency,
        description_column="description",
    )
    if matched.get("status") != "ok":
        return matched

    confirmed = confirm_invoice(
        db, vendor, invoice_number, invoice_date,
        matched.get("currency", currency), matched.get("fx_rate", 1.0),
        matched.get("items") or [], is_vendor_sale=is_vendor_sale,
    )
    if confirmed.get("status") != "ok":
        return confirmed

    invoice_id = confirmed.get("invoice_id")

    conn = db._get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE pending_invoices
            SET status = 'approved', reviewed_at = GETUTCDATE(),
                imported_invoice_id = ?, vendor_canonical = ?
            WHERE id = ?
        """, invoice_id, vendor, pending_id)

        if save_alias:
            raw = (pending.get("vendor_raw") or "").strip()
            if raw and raw.lower() != vendor.lower():
                cursor.execute("""
                    MERGE vendor_aliases AS target
                    USING (SELECT ? AS alias_lower, ? AS alias_original, ? AS canonical_vendor) AS source
                    ON target.alias_lower = source.alias_lower
                    WHEN MATCHED THEN UPDATE SET canonical_vendor = source.canonical_vendor,
                                                  alias_original = source.alias_original
                    WHEN NOT MATCHED THEN INSERT (alias_lower, alias_original, canonical_vendor)
                                          VALUES (source.alias_lower, source.alias_original, source.canonical_vendor);
                """, raw.lower(), raw, vendor)

        conn.commit()
    finally:
        conn.close()

    return {
        "status": "ok",
        "pending_id": pending_id,
        "invoice_id": invoice_id,
        "matched_count": confirmed.get("line_items"),
        "skipped_count": confirmed.get("skipped_items"),
        "total_cost_cad": confirmed.get("total_cost_cad"),
        "alias_saved": save_alias,
    }


def reject_pending(db, pending_id, reason=None):
    conn = db._get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE pending_invoices
            SET status = 'rejected', reviewed_at = GETUTCDATE(),
                error_message = COALESCE(?, error_message)
            WHERE id = ?
        """, reason, pending_id)
        conn.commit()
        return {"status": "ok", "rejected": pending_id}
    finally:
        conn.close()


def delete_pending(db, pending_id):
    conn = db._get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM pending_invoices WHERE id = ?", pending_id)
        conn.commit()
        return {"status": "ok", "deleted": pending_id}
    finally:
        conn.close()


# --- AUTO-IMPORT (off by default) ------------------------------------------

def _try_auto_import(db, pending_id):
    """Auto-import only if (a) vendor alias resolved, (b) every item matched
    exactly, (c) totals reconcile within 1%. Anything else stays 'pending'."""
    pending = get_pending_invoice(db, pending_id, include_items=True)
    if not pending or pending["status"] != "pending":
        return {"status": "skip", "reason": "not_pending"}
    if not pending.get("vendor_canonical"):
        return {"status": "skip", "reason": "vendor_unrecognized"}
    if not pending.get("invoice_number") or not pending.get("invoice_date"):
        return {"status": "skip", "reason": "missing_header_fields"}

    matched = preview_pending_match(db, pending_id)
    if matched.get("status") != "ok":
        return {"status": "skip", "reason": "preview_failed"}

    items = matched.get("items") or []
    if not items:
        return {"status": "skip", "reason": "no_items"}
    not_exact = sum(1 for i in items if i.get("match_type") != "exact" and i.get("match_type") != "mapped")
    if not_exact > 0:
        return {"status": "skip", "reason": "non_exact_matches", "non_exact_count": not_exact}

    # Totals reconciliation: LLM-reported total vs sum of qty*unit_cost in foreign currency.
    llm_total = _f(pending.get("total_amount"))
    recomputed = round(sum(_f(i.get("unit_cost_foreign")) * _f(i.get("quantity")) for i in items), 2)
    if llm_total > 0:
        diff_pct = abs(recomputed - llm_total) / llm_total * 100
        if diff_pct > 1.0:
            return {"status": "skip", "reason": "totals_mismatch", "llm_total": llm_total, "recomputed": recomputed}

    result = approve_pending(
        db, pending_id,
        is_vendor_sale=False, save_alias=False,
    )
    if result.get("status") == "ok":
        # Re-mark as auto_imported (approve_pending sets 'approved').
        conn = db._get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("UPDATE pending_invoices SET status = 'auto_imported' WHERE id = ?", pending_id)
            conn.commit()
        finally:
            conn.close()
        result["auto_imported"] = True
    return result
