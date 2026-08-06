// ─── BARCODE LABEL PRINTER ─────────────────────────────────────────
// Queue-based architecture (chosen because Chrome's Local Network
// Access enforcement blocks direct browser → localhost calls from any
// public HTTPS site, regardless of certs / address-space hints).
//
// Flow:
//   browser → POST /api/labels  (TC-Planner backend, HTTPS)
//                ↓ stores in pending_label_jobs
//   local agent on receiving PC → polls GET /api/labels/pending
//                                → generates ZPL
//                                → POSTs to local Browser Print
//                                → POSTs /api/labels/{id}/printed
//
// The agent isn't a webpage so Chrome's LNA never applies to it. ZPL
// generation moved to the agent because it's DPI-aware and depends on
// the LOCAL printer config; the cloud has no idea what's plugged in
// where. The browser side is now just "queue this job; toast result."
//
// All ZPL/Browser Print details live in `agent/tc_label_agent.py`.

import * as api from './api';

// Queue one or more label jobs server-side. Each job is one
// (sku, barcode, qty) tuple — qty=N means print N labels.
//
// Returns { ok: bool, queued: number, error?: string }.
export async function queueLabelJobs(jobs) {
  // Filter and shape input. Each job needs a non-empty SKU + barcode
  // and a positive qty — anything else is silently skipped (caller
  // already toasts about missing-barcode SKUs separately).
  const cleaned = (jobs || [])
    .filter(j => j && j.sku && j.barcode && (j.qty || 0) > 0)
    .map(j => ({
      sku: String(j.sku),
      barcode: String(j.barcode),
      qty: Math.max(1, Math.floor(j.qty)),
      stock_order_id: j.stock_order_id || null,
    }));

  if (!cleaned.length) {
    return { ok: false, queued: 0, error: 'No printable jobs (missing SKU/barcode/qty)' };
  }

  try {
    const res = await api.queueLabelPrintJobs(cleaned);
    return { ok: true, queued: res.created || cleaned.length };
  } catch (err) {
    return { ok: false, queued: 0, error: (err && err.message) || String(err) };
  }
}

// Convenience wrapper for the receive-stock flow. Builds a jobs array
// from {item_id, received_qty} pairs by looking up each item in the
// supplied order. Skips items without a barcode and returns the list
// of skipped SKUs separately so the UI can toast about them.
export function buildLabelJobsFromReceive({ order, receiveEntries }) {
  const jobs = [];
  const missing = [];
  for (const { item_id, received_qty } of receiveEntries) {
    const it = (order.items || []).find(x => x.id === item_id);
    if (!it || !it.sku || (received_qty || 0) <= 0) continue;
    if (!it.barcode) {
      missing.push(it.sku);
      continue;
    }
    jobs.push({
      sku: it.sku,
      barcode: it.barcode,
      qty: received_qty,
      stock_order_id: order.id,
    });
  }
  return { jobs, missing };
}

// Test-print: queue a single fixed-content job. The agent prints it
// the same way as a real receive. Used by the Settings → Test Print
// button to verify the full pipeline (cloud queue → agent → printer)
// is working.
export async function queueTestLabel() {
  return queueLabelJobs([{
    sku: 'TEST-SKU',
    barcode: '0123456789012',
    qty: 1,
    stock_order_id: null,
  }]);
}
