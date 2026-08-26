import { useState, useEffect, useMemo, useCallback } from 'react';
import * as api from './api';
import { queueLabelJobs, buildLabelJobsFromReceive } from './labelPrinter';

const fmt = (n) => new Intl.NumberFormat('en-CA', { style: 'currency', currency: 'CAD', minimumFractionDigits: 2 }).format(n);
const fmtDate = (d) => { if (!d) return '\u2014'; try { return new Date(d).toLocaleDateString('en-CA', { year: 'numeric', month: 'short', day: 'numeric' }); } catch { return d; } };
const fmtDateTime = (d) => { if (!d) return '\u2014'; try { return new Date(d).toLocaleString('en-CA', { year: 'numeric', month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' }); } catch { return d; } };

// Hover text for the Received column: one line per receive event showing
// quantity, who recorded it, and when (local time). Older receipts recorded
// before user tracking existed have no rows and show a short note instead.
const receiptTooltip = (item) => {
  const receipts = item?.receipts || [];
  if (receipts.length === 0) {
    return (item?.received_qty || 0) > 0
      ? 'Received before receipt logging was enabled \u2014 no user/date recorded'
      : 'Nothing received yet';
  }
  return receipts
    .map(r => `+${r.received_qty} by ${r.received_by || 'Unknown'} \u2014 ${fmtDateTime(r.received_at)}`)
    .join('\n');
};

const STATUSES = {
  open: { label: 'Open', color: '#1a7e5a', bg: '#e8f5f0' },
  ordered: { label: 'Ordered, waiting to ship', color: '#1a7e5a', bg: '#e8f5f0' },
  ordered_invoice: { label: 'Ordered, waiting for invoice', color: '#e67e22', bg: '#fef3e2' },
  paid: { label: 'Paid, waiting to ship', color: '#3498db', bg: '#eaf4fd' },
  shipped: { label: 'Shipped', color: '#3498db', bg: '#eaf4fd' },
  partial_received: { label: 'Partially received', color: '#e67e22', bg: '#fef3e2' },
  partial_shipped: { label: 'Partially shipped', color: '#3498db', bg: '#eaf4fd' },
  closed: { label: 'Closed', color: '#7f8c8d', bg: '#f0f0f0' },
};
const STATUS_OPTIONS = [
  { value: 'open', label: 'Open' },{ value: 'ordered', label: 'Ordered' },
  { value: 'ordered_invoice', label: 'Ordered, waiting for invoice' },
  { value: 'paid', label: 'Paid, waiting to ship' },{ value: 'shipped', label: 'Shipped' },
  { value: 'partial_received', label: 'Partially received' },
  { value: 'partial_shipped', label: 'Partially shipped' },{ value: 'closed', label: 'Closed' },
];
const StatusBadge = ({ status }) => {
  const s = STATUSES[status] || STATUSES.open;
  return <span style={{ padding: '3px 10px', borderRadius: 4, fontSize: 12, fontWeight: 600, color: s.color, backgroundColor: s.bg, whiteSpace: 'nowrap' }}>{s.label}</span>;
};

// Stock Update Modal - matches Inventory Planner's "Update stock in Telescopes Canada Warehouse"
function StockUpdateModal({ reviewData, onApply, onCancel, applying }) {
  const [selected, setSelected] = useState(new Set());
  useEffect(() => { if (reviewData?.items) setSelected(new Set(reviewData.items.map(i => i.item_id))); }, [reviewData]);
  if (!reviewData) return null;
  const toggleAll = () => { selected.size === reviewData.items.length ? setSelected(new Set()) : setSelected(new Set(reviewData.items.map(i => i.item_id))); };
  return (
    <div style={{ position: 'fixed', inset: 0, backgroundColor: 'rgba(0,0,0,0.5)', display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 1000 }}>
      <div style={{ backgroundColor: '#fff', borderRadius: 12, width: '90%', maxWidth: 850, maxHeight: '80vh', display: 'flex', flexDirection: 'column', boxShadow: '0 8px 32px rgba(0,0,0,0.2)' }}>
        <div style={{ padding: '20px 24px', borderBottom: '1px solid var(--border)', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <h2 style={{ margin: 0, fontSize: 18, fontWeight: 700 }}>Update stock in Telescopes Canada Warehouse</h2>
          <button onClick={onCancel} style={{ fontSize: 20, color: 'var(--text-light)', background: 'none', border: 'none', cursor: 'pointer', padding: '4px 8px' }}>{'\u2715'}</button>
        </div>
        <div style={{ flex: 1, overflow: 'auto' }}>
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
            <thead><tr style={{ borderBottom: '2px solid var(--border)' }}>
              <th style={{ padding: '10px 12px', width: 36 }}><input type="checkbox" checked={selected.size === reviewData.items.length} onChange={toggleAll} /></th>
              <th style={{ padding: '10px 12px', textAlign: 'left', color: 'var(--text-light)', fontWeight: 500 }}>Variant</th>
              <th style={{ padding: '10px 12px', textAlign: 'right', color: 'var(--text-light)', fontWeight: 500 }}>Current stock</th>
              <th style={{ padding: '10px 12px', textAlign: 'right', color: 'var(--text-light)', fontWeight: 500 }}>Update</th>
              <th style={{ padding: '10px 12px', textAlign: 'right', color: 'var(--text-light)', fontWeight: 500 }}>After update</th>
            </tr></thead>
            <tbody>
              {reviewData.items.map(item => (
                <tr key={item.item_id} style={{ borderBottom: '1px solid var(--border)' }}>
                  <td style={{ padding: '12px' }}><input type="checkbox" checked={selected.has(item.item_id)} onChange={() => { const n = new Set(selected); n.has(item.item_id) ? n.delete(item.item_id) : n.add(item.item_id); setSelected(n); }} /></td>
                  <td style={{ padding: '12px' }}><div style={{ fontWeight: 500 }}>{item.product_title}</div><div style={{ fontSize: 11, color: 'var(--text-muted)' }}>{item.sku}</div></td>
                  <td style={{ padding: '12px', textAlign: 'right', fontWeight: 500 }}>{item.found_in_shopify ? item.current_shopify_stock : 'N/A'}</td>
                  <td style={{ padding: '12px', textAlign: 'right', fontWeight: 700, color: '#1a7e5a', fontSize: 14 }}>+{item.adjustment}</td>
                  <td style={{ padding: '12px', textAlign: 'right', fontWeight: 500 }}>{item.found_in_shopify ? item.resulting_stock : 'N/A'}</td>
                </tr>
              ))}
            </tbody>
            <tfoot><tr style={{ borderTop: '1px solid var(--border)', backgroundColor: '#fafbfc' }}>
              <td style={{ padding: '10px 12px' }}><input type="checkbox" checked={selected.size === reviewData.items.length} onChange={toggleAll} /></td>
              <td style={{ padding: '10px 12px', fontSize: 12, color: 'var(--text-light)', fontWeight: 600 }}>all {selected.size} updates selected</td>
              <td colSpan={3} />
            </tr></tfoot>
          </table>
        </div>
        <div style={{ padding: '16px 24px', borderTop: '1px solid var(--border)', display: 'flex', gap: 12 }}>
          <button onClick={() => onApply(reviewData.items.filter(i => selected.has(i.item_id)))} disabled={applying || selected.size === 0}
            style={{ padding: '10px 24px', borderRadius: 8, border: 'none', backgroundColor: '#1a7e5a', color: '#fff', fontWeight: 700, fontSize: 14, cursor: 'pointer', opacity: applying ? 0.7 : 1 }}>
            {applying ? 'Updating...' : 'Update stock'}
          </button>
          <button onClick={onCancel} disabled={applying} style={{ padding: '10px 20px', borderRadius: 8, border: '1px solid var(--border)', backgroundColor: 'transparent', fontSize: 14, cursor: 'pointer', fontWeight: 500 }}>Cancel</button>
        </div>
      </div>
    </div>
  );
}

// Order Detail View
function StockOrderDetail({ orderId, onBack, onToast }) {
  const [order, setOrder] = useState(null);
  const [loading, setLoading] = useState(true);
  const [editMode, setEditMode] = useState(false);
  const [editValues, setEditValues] = useState({});
  const [savingEdits, setSavingEdits] = useState(false);
  const [toReceive, setToReceive] = useState({});
  const [hasPendingReceives, setHasPendingReceives] = useState(false);
  const [lastReceived, setLastReceived] = useState(null);  // items from last Save batch
  const [saving, setSaving] = useState(false);
  const [showStockModal, setShowStockModal] = useState(false);
  const [reviewData, setReviewData] = useState(null);
  const [reviewLoading, setReviewLoading] = useState(false);
  const [applying, setApplying] = useState(false);
  const [detailSort, setDetailSort] = useState({ field: null, dir: 'asc' });
  const [showAddItem, setShowAddItem] = useState(false);

  // Comments — local draft state that flushes to server on blur. Avoids
  // hammering the API on every keystroke.
  const [commentsDraft, setCommentsDraft] = useState('');
  const [commentsDirty, setCommentsDirty] = useState(false);
  // Bin (stock.bin metafield) inline edits, keyed by line-item id.
  // binEdits holds the in-progress text; binSaving tracks which items
  // are mid-write to Shopify so we can show a subtle saving state.
  const [binEdits, setBinEdits] = useState({});
  const [binSaving, setBinSaving] = useState(new Set());
  // Barcode (native variant field) inline edits, keyed by line-item id —
  // same pattern as bin: barcodeEdits holds in-progress text, barcodeSaving
  // tracks which items are mid-write to Shopify.
  const [barcodeEdits, setBarcodeEdits] = useState({});
  const [barcodeSaving, setBarcodeSaving] = useState(new Set());
  // Replenishment-suggestion toggle (non_replenishable_skus) — tracks lines
  // with an in-flight toggle request.
  const [replToggling, setReplToggling] = useState(new Set());
  useEffect(() => {
    // Sync draft when order loads or refreshes from server (only if not
    // dirty, to avoid clobbering in-progress edits)
    if (order && !commentsDirty) setCommentsDraft(order.comments || '');
  }, [order, commentsDirty]);

  const saveComments = async () => {
    if (!commentsDirty) return;
    try {
      await api.updateStockOrderHeader(orderId, { comments: commentsDraft });
      setCommentsDirty(false);
      onToast('Comments saved');
      // No fetchOrder() — draft is already in sync with what we just sent
    } catch (err) {
      onToast('Failed to save comments: ' + err.message, 'error');
    }
  };

  const fetchOrder = useCallback(async () => {
    setLoading(true);
    try { const data = await api.getStockOrder(orderId); setOrder(data); setToReceive({}); setHasPendingReceives(false); }
    catch (err) { onToast('Failed to load order: ' + err.message, 'error'); }
    setLoading(false);
  }, [orderId, onToast]);
  useEffect(() => { fetchOrder(); }, [fetchOrder]);

  // Auto top-up waiter coverage on initial PO mount. If a newer waitlist
  // snapshot is now larger than this PO's existing coverage, the backend
  // bumps ordered_qty on each affected line to close the gap. We run it
  // ONCE per orderId — subsequent fetches don't repeat it, so receiving
  // items / saving comments doesn't re-trigger the mutation. Closed POs
  // and items on retired SKUs are skipped server-side.
  useEffect(() => {
    if (!orderId) return;
    let cancelled = false;
    (async () => {
      try {
        const res = await api.topUpWaiterCoverage(orderId);
        if (cancelled) return;
        if (res.bumped_count > 0) {
          onToast(
            `Auto-bumped ${res.bumped_count} item${res.bumped_count === 1 ? '' : 's'} ` +
            `(+${res.total_units_added} unit${res.total_units_added === 1 ? '' : 's'}) ` +
            `to cover the current waitlist`,
            'success'
          );
          fetchOrder();
        }
      } catch (_) {
        // Non-fatal — the PO still loads with its existing qtys.
      }
    })();
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [orderId]);

  // Receive
  const fillToReceive = (id, remaining) => { setToReceive(p => ({ ...p, [id]: remaining })); setHasPendingReceives(true); setLastReceived(null); };
  const updateToReceive = (id, val) => { const q = Math.max(0, parseInt(val) || 0); setToReceive(p => ({ ...p, [id]: q })); setHasPendingReceives(Object.values({ ...toReceive, [id]: q }).some(v => v > 0)); setLastReceived(null); };
  const fillAllRemaining = () => { const r = {}; (order?.items || []).forEach(i => { const rem = (i.ordered_qty||0) - (i.received_qty||0); if (rem > 0) r[i.id] = rem; }); setToReceive(r); setHasPendingReceives(Object.values(r).some(v => v > 0)); setLastReceived(null); };
  const discardReceives = () => { setToReceive({}); setHasPendingReceives(false); setLastReceived(null); };

  const handleSave = async () => {
    const items = Object.entries(toReceive).filter(([,q]) => q > 0).map(([id, q]) => ({ item_id: parseInt(id), received_qty: q }));
    if (!items.length) return;
    setSaving(true);
    try {
      await api.receiveStockOrderItems(orderId, items);
      onToast('Saved ' + items.length + ' items as received');
      setLastReceived(items);

      // Auto-queue barcode label print jobs for vendors with the flag
      // enabled. The actual printing happens on the local agent (see
      // agent/tc_label_agent.py) which polls /api/labels/pending and
      // talks to Browser Print on its own localhost. We just toast
      // "queued" here — the labels appear on paper a few seconds later.
      // We picked a queue + agent over direct-from-browser printing
      // because Chrome's Local Network Access enforcement blocks all
      // public-HTTPS-to-localhost requests in current Chrome versions.
      if (order?.requires_barcode_labels) {
        const { jobs, missing } = buildLabelJobsFromReceive({
          order, receiveEntries: items,
        });
        if (jobs.length) {
          const total = jobs.reduce((s, j) => s + j.qty, 0);
          const res = await queueLabelJobs(jobs);
          if (res.ok) {
            onToast(`Queued ${total} barcode label${total === 1 ? '' : 's'} — will print shortly`);
          } else {
            onToast('Label queue failed: ' + (res.error || 'unknown error'), 'error');
          }
        }
        if (missing.length) {
          onToast(
            `${missing.length} item${missing.length === 1 ? '' : 's'} received without a barcode on file (no label printed): ${missing.slice(0, 3).join(', ')}${missing.length > 3 ? '...' : ''}`,
            'error',
          );
        }
      }

      const data = await api.getStockOrder(orderId);
      setOrder(data);
      setToReceive({});
      setHasPendingReceives(false);
    }
    catch (err) { onToast('Failed to save: ' + err.message, 'error'); }
    setSaving(false);
  };

  // Reverse a receive on one line. Units already pushed to Shopify are taken
  // back out of Shopify too, so the confirmation says so plainly — this is
  // not a local-only correction.
  const [undoing, setUndoing] = useState(null);
  const handleUndoReceive = async (item) => {
    const received = item.received_qty || 0;
    if (received <= 0) return;
    const pushed = received - (item.unpushed_qty || 0);

    let msg = `Undo receiving ${received} \u00d7 ${item.sku}?`;
    if (pushed > 0) {
      msg += `\n\n${pushed} unit${pushed === 1 ? ' has' : 's have'} already been `
           + `pushed to Shopify, so Shopify stock will be reduced by ${pushed}.`;
    } else {
      msg += '\n\nNothing was pushed to Shopify for this line, so only the '
           + 'purchase order is affected.';
    }
    if (!window.confirm(msg)) return;

    setUndoing(item.id);
    try {
      const res = await api.undoReceive(orderId, item.id);
      if (res.warning) onToast(res.warning, 'error');
      else onToast(`Undid ${res.undone} \u00d7 ${res.sku}`
        + (res.shopify_adjusted ? ` (Shopify \u2212${res.shopify_adjusted})` : ''));
      if (res.order) setOrder(res.order); else fetchOrder();
    } catch (err) {
      onToast('Undo failed: ' + err.message, 'error');
    }
    setUndoing(null);
  };

  // RFID labels - sends the just-saved receive to the RFID Stickers
  // app's print queue (server-to-server; the labels come out on the
  // warehouse Zebra with RFID encoding, one per received unit, each
  // printed with the product's home bin). Separate from the barcode
  // label agent above and from the Shopify stock update.
  const [printingRfid, setPrintingRfid] = useState(false);
  const handlePrintRfidLabels = async () => {
    if (!lastReceived || !lastReceived.length) return;
    setPrintingRfid(true);
    try {
      const res = await api.sendRfidLabels(orderId, lastReceived);
      onToast(res.message || `${res.queued} RFID label(s) queued`);
      const problems = [
        ...(res.skipped_unknown || []),
        ...(res.skipped_no_sku || []),
      ];
      if (problems.length) {
        onToast(
          `Not printed (unknown to the RFID system): ${problems.slice(0, 3).join(', ')}${problems.length > 3 ? '...' : ''}`,
          'error',
        );
      }
      if ((res.skipped_no_bin || []).length) {
        onToast(
          `Held for a bin (assign one in the RFID app, then print again): ${res.skipped_no_bin.slice(0, 3).join(', ')}${res.skipped_no_bin.length > 3 ? '...' : ''}`,
          'error',
        );
      }
    } catch (err) {
      onToast('RFID labels failed: ' + err.message, 'error');
    }
    setPrintingRfid(false);
  };

  // Stock update - passes the specific items that were just saved
  const handleIncreaseStock = async (useOutstanding = false) => {
    setReviewLoading(true);
    // Passing null makes the server offer everything received but never
    // pushed — the recovery path after a refresh or a new batch wiped
    // lastReceived out of memory.
    try {
      const data = await api.prepareStockUpdate(orderId, useOutstanding ? null : lastReceived);
      setReviewData(data);
      setShowStockModal(true);
    }
    catch (err) { onToast('Failed: ' + err.message, 'error'); }
    setReviewLoading(false);
  };
  const handleApplyStockUpdate = async (selectedItems) => {
    setApplying(true);
    try {
      const result = await api.applyStockUpdate(orderId, reviewData.location_id, selectedItems.map(i => ({ item_id: i.item_id, sku: i.sku, adjustment: i.adjustment, inventory_item_id: i.inventory_item_id })));
      onToast(result.message); setShowStockModal(false); setReviewData(null); setLastReceived(null); fetchOrder();
    } catch (err) { onToast('Failed: ' + err.message, 'error'); }
    setApplying(false);
  };

  // Edit
  const enterEditMode = () => { const v = {}; (order?.items||[]).forEach(i => { v[i.id] = { ordered_qty: i.ordered_qty, received_qty: i.received_qty, unit_cost: i.unit_cost }; }); setEditValues(v); setEditMode(true); };

  const handleDeleteItem = async (item) => {
    try {
      await api.deleteStockOrderItem(orderId, item.id);
      // Remove this item from edit values too, if we're in edit mode
      setEditValues(prev => { const n = {...prev}; delete n[item.id]; return n; });
      onToast(`Removed ${item.sku}`);
      fetchOrder();
    } catch (err) {
      onToast('Delete failed: ' + err.message, 'error');
    }
  };

  // Save the edited stock.bin value for a line item straight to Shopify.
  // Fires on blur. No-op if the value is unchanged from what's loaded.
  const handleBinSave = async (item) => {
    const draft = binEdits[item.id];
    if (draft === undefined) return;                  // never touched
    const next = (draft || '').trim();
    if (next === (item.bin || '').trim()) return;     // unchanged
    if (!item.sku) { onToast('Cannot set bin — item has no SKU', 'error'); return; }
    setBinSaving(prev => new Set(prev).add(item.id));
    try {
      await api.updateVariantBin(item.sku, next);
      // Reflect the saved value locally so the input stays in sync
      // without a full re-fetch.
      setOrder(prev => prev ? {
        ...prev,
        items: prev.items.map(i => i.id === item.id ? { ...i, bin: next } : i),
      } : prev);
      setBinEdits(prev => { const n = { ...prev }; delete n[item.id]; return n; });
      onToast(next ? `Bin set: ${item.sku} -> ${next}` : `Bin cleared: ${item.sku}`);
    } catch (err) {
      onToast('Bin update failed: ' + err.message, 'error');
    }
    setBinSaving(prev => { const n = new Set(prev); n.delete(item.id); return n; });
  };

  // Save the edited barcode straight to Shopify's native variant barcode
  // field. Fires on blur. No-op if unchanged from what's loaded.
  const handleBarcodeSave = async (item) => {
    const draft = barcodeEdits[item.id];
    if (draft === undefined) return;                     // never touched
    const next = (draft || '').trim();
    if (next === (item.barcode || '').trim()) return;    // unchanged
    if (!item.sku) { onToast('Cannot set barcode — item has no SKU', 'error'); return; }
    setBarcodeSaving(prev => new Set(prev).add(item.id));
    try {
      await api.updateVariantBarcode(item.sku, next);
      setOrder(prev => prev ? {
        ...prev,
        items: prev.items.map(i => i.id === item.id ? { ...i, barcode: next } : i),
      } : prev);
      setBarcodeEdits(prev => { const n = { ...prev }; delete n[item.id]; return n; });
      onToast(next ? `Barcode set: ${item.sku} -> ${next}` : `Barcode cleared: ${item.sku}`);
    } catch (err) {
      onToast('Barcode update failed: ' + err.message, 'error');
    }
    setBarcodeSaving(prev => { const n = new Set(prev); n.delete(item.id); return n; });
  };

  // Toggle whether this SKU is ever suggested by the Replenishment screen.
  // Writes to non_replenishable_skus via the same endpoint the Replenishment
  // screen's bulk action uses. Fully reversible — click again to re-enable.
  const handleToggleReplenishable = async (item) => {
    if (!item.sku) { onToast('Cannot update — item has no SKU', 'error'); return; }
    const makeReplenishable = !!item.is_non_replenishable;  // currently excluded → re-enable
    setReplToggling(prev => new Set(prev).add(item.id));
    try {
      await api.toggleReplenishable([item.sku], makeReplenishable);
      setOrder(prev => prev ? {
        ...prev,
        items: prev.items.map(i => i.id === item.id ? { ...i, is_non_replenishable: !makeReplenishable } : i),
      } : prev);
      onToast(makeReplenishable
        ? `${item.sku} will be suggested for replenishment again`
        : `${item.sku} will no longer be suggested for replenishment`);
    } catch (err) {
      onToast('Update failed: ' + err.message, 'error');
    }
    setReplToggling(prev => { const n = new Set(prev); n.delete(item.id); return n; });
  };
  const saveEdits = async () => {
    setSavingEdits(true);
    try {
      for (const [id, vals] of Object.entries(editValues)) {
        const orig = order.items.find(i => i.id === parseInt(id)); if (!orig) continue;
        const u = {};
        if (vals.ordered_qty !== orig.ordered_qty) u.ordered_qty = vals.ordered_qty;
        if (vals.received_qty !== orig.received_qty) u.received_qty = vals.received_qty;
        if (vals.unit_cost !== orig.unit_cost) u.unit_cost = vals.unit_cost;
        if (Object.keys(u).length > 0) await api.editStockOrderItem(orderId, parseInt(id), u);
      }
      onToast('Changes saved'); setEditMode(false); fetchOrder();
    } catch (err) { onToast('Failed: ' + err.message, 'error'); }
    setSavingEdits(false);
  };

  const handleStatusChange = async (s) => { try { await api.updateStockOrderStatus(orderId, s); onToast('Status updated'); fetchOrder(); } catch (err) { onToast('Failed: ' + err.message, 'error'); } };

  const handleCloseWithUnreceived = async () => {
    const remaining = (order?.items || []).reduce((sum, i) => sum + Math.max(0, (i.ordered_qty || 0) - (i.received_qty || 0)), 0);
    const msg = remaining > 0
      ? `Close this order and skip ${remaining} unreceived unit${remaining === 1 ? '' : 's'}?\n\nYou can still view the order later, but it will no longer appear in the "open" list or count toward inventory on-order.`
      : `Close this order?`;
    if (!window.confirm(msg)) return;
    try {
      await api.updateStockOrderStatus(orderId, 'closed');
      onToast('Order closed');
      fetchOrder();
    } catch (err) { onToast('Close failed: ' + err.message, 'error'); }
  };

  const handleDeleteOrder = async () => {
    const ok = window.confirm(
      `Permanently delete purchase order #${order?.reference_number}?\n\nThis removes the order and all its line items. This cannot be undone.`
    );
    if (!ok) return;
    try {
      await api.deleteStockOrder(orderId);
      onToast(`Deleted PO #${order?.reference_number}`);
      onBack?.();
    } catch (err) { onToast('Delete failed: ' + err.message, 'error'); }
  };

  const [emailing, setEmailing] = useState(false);
  const handleEmail = async () => {
    setEmailing(true);
    try { const result = await api.emailStockOrder(orderId); onToast(result.message); }
    catch (err) { onToast('Email failed: ' + err.message, 'error'); }
    setEmailing(false);
  };

  const items = useMemo(() => {
    const base = order?.items || [];
    if (!detailSort.field) return base;
    const { field, dir } = detailSort;
    // Effective values — in edit mode, sort by the LIVE edited values so the
    // order matches the numbers shown in the inputs. The Unit cost and Total
    // cost columns both reflect the edited unit_cost / ordered_qty while editing.
    // We deliberately read editValues but DON'T list it in the deps array, so
    // the sort snapshots at sort-click / mode-change time and rows don't jump
    // around while you're typing. Click the header again to re-sort.
    const effOrdered = (it) => editMode ? (editValues[it.id]?.ordered_qty ?? it.ordered_qty ?? 0) : (it.ordered_qty ?? 0);
    const effReceived = (it) => editMode ? (editValues[it.id]?.received_qty ?? it.received_qty ?? 0) : (it.received_qty ?? 0);
    const effUnitCost = (it) => editMode ? (editValues[it.id]?.unit_cost ?? it.unit_cost ?? 0) : (it.unit_cost ?? 0);
    const arr = [...base];
    arr.sort((a, b) => {
      let av, bv;
      if (field === 'remaining') {
        av = effOrdered(a) - effReceived(a);
        bv = effOrdered(b) - effReceived(b);
      } else if (field === 'waiter_count') {
        av = a.waiter_count || 0;
        bv = b.waiter_count || 0;
      } else if (field === 'ordered_qty') {
        av = effOrdered(a); bv = effOrdered(b);
      } else if (field === 'received_qty') {
        av = effReceived(a); bv = effReceived(b);
      } else if (field === 'unit_cost') {
        av = effUnitCost(a); bv = effUnitCost(b);
      } else if (field === 'line_total') {
        // Total cost = unit cost × ordered qty (live edited values in edit mode).
        av = effUnitCost(a) * effOrdered(a);
        bv = effUnitCost(b) * effOrdered(b);
      } else {
        av = a[field]; bv = b[field];
        if (typeof av === 'string') av = av.toLowerCase();
        if (typeof bv === 'string') bv = bv.toLowerCase();
      }
      if (av == null && bv == null) return 0;
      if (av == null) return 1;
      if (bv == null) return -1;
      if (av === bv) return 0;
      return dir === 'desc' ? (av > bv ? -1 : 1) : (av < bv ? -1 : 1);
    });
    return arr;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [order, detailSort, editMode]);

  const handleDetailSort = (f) => setDetailSort(prev =>
    prev.field === f
      ? { field: f, dir: prev.dir === 'asc' ? 'desc' : 'asc' }
      : { field: f, dir: 'asc' }
  );
  const detailSortArrow = (f) => detailSort.field === f ? (detailSort.dir === 'asc' ? ' \u2191' : ' \u2193') : '';
  const detailHeaderStyle = (align = 'left') => ({
    padding: '10px 12px',
    textAlign: align,
    color: 'var(--text-light)',
    fontWeight: 500,
    cursor: 'pointer',
    userSelect: 'none',
    whiteSpace: 'nowrap',
  });

  if (loading) return <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: '100vh', gap: 12 }}><div className="spinner" /><span style={{ color: 'var(--text-light)' }}>Loading order...</span></div>;
  if (!order) return <div style={{ padding: 40, textAlign: 'center' }}><p>Order not found</p><button onClick={onBack} style={{ marginTop: 16, padding: '8px 20px', borderRadius: 6, border: '1px solid var(--border)', backgroundColor: 'transparent', cursor: 'pointer' }}>Back</button></div>;

  const totalInStock = items.reduce((s, i) => s + (i.current_stock != null ? i.current_stock : 0), 0);
  const totalOrdered = items.reduce((s, i) => s + (i.ordered_qty||0), 0);
  const totalReceived = items.reduce((s, i) => s + (i.received_qty||0), 0);
  const totalOnOrderOther = items.reduce((s, i) => s + (i.on_order_other||0), 0);
  const totalCost = items.reduce((s, i) => s + (i.unit_cost||0) * (i.ordered_qty||0), 0);
  const isPastDue = order.expected_date && new Date(order.expected_date) < new Date() && order.status !== 'closed';
  const hasUnreceived = items.some(i => (i.ordered_qty||0) > (i.received_qty||0));

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100vh' }}>
      {/* Header */}
      <div style={{ padding: '14px 24px', borderBottom: '1px solid var(--border)', backgroundColor: 'var(--white)', display: 'flex', alignItems: 'center', gap: 12 }}>
        <h1 style={{ fontSize: 20, fontWeight: 700, margin: 0 }}>Purchase order {'\u2192'} <span style={{ color: 'var(--green)' }}>#{order.reference_number}</span></h1>
        <StatusBadge status={order.status} />
        {isPastDue && <span style={{ padding: '3px 8px', borderRadius: 4, fontSize: 11, fontWeight: 600, color: '#e74c3c', backgroundColor: '#fde8e5' }}>Past due</span>}
        <div style={{ flex: 1 }} />
        <select value={order.status} onChange={e => handleStatusChange(e.target.value)} style={{ padding: '6px 12px', borderRadius: 6, border: '1px solid var(--border)', fontSize: 13, cursor: 'pointer' }}>
          {STATUS_OPTIONS.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
        </select>
        <button onClick={handleEmail} disabled={emailing} style={{ padding: '8px 16px', borderRadius: 6, border: '1px solid var(--green)', backgroundColor: 'transparent', color: 'var(--green)', fontWeight: 600, fontSize: 13, cursor: 'pointer' }}>{emailing ? 'Sending...' : '\u2709\uFE0F Email'}</button>
        <button onClick={enterEditMode} disabled={editMode} style={{ padding: '8px 16px', borderRadius: 6, border: '1px solid var(--green)', backgroundColor: 'transparent', color: 'var(--green)', fontWeight: 600, fontSize: 13, cursor: 'pointer' }}>{'\u270F\uFE0F'} Edit</button>
        {order.status !== 'closed' && (
          <button onClick={handleCloseWithUnreceived} disabled={editMode}
            title={hasUnreceived ? 'Close this order and skip any remaining unreceived items' : 'Close this order'}
            style={{ padding: '8px 16px', borderRadius: 6, border: '1px solid #6366f1', backgroundColor: 'transparent', color: '#6366f1', fontWeight: 600, fontSize: 13, cursor: 'pointer' }}>
            {hasUnreceived ? '\uD83D\uDD12 Close (skip remaining)' : '\uD83D\uDD12 Close'}
          </button>
        )}
        <button onClick={handleDeleteOrder} disabled={editMode}
          title="Permanently delete this order and all its line items"
          style={{ padding: '8px 12px', borderRadius: 6, border: '1px solid #e74c3c', backgroundColor: 'transparent', color: '#e74c3c', fontWeight: 600, fontSize: 13, cursor: 'pointer' }}>
          {'\uD83D\uDDD1\uFE0F'} Delete
        </button>
      </div>
      {/* Info bar — vendor/destination/dates/received on the left,
          comments textarea inline on the right so it doesn't add a
          second header row. The textarea is 1-row tall by default;
          on focus it expands so the full note is visible while editing. */}
      <div style={{ padding: '10px 24px', borderBottom: '1px solid var(--border)', backgroundColor: '#fafbfc', display: 'flex', gap: 24, fontSize: 13, alignItems: 'center', flexWrap: 'wrap' }}>
        <div><span style={{ color: 'var(--text-light)' }}>Vendor: </span><strong style={{ color: 'var(--green)' }}>{order.vendor}</strong></div>
        <div><span style={{ color: 'var(--text-light)' }}>Destination: </span><strong>Telescopes Canada Warehouse</strong></div>
        <div><span style={{ color: 'var(--text-light)' }}>Created: </span><strong>{fmtDate(order.created_at)}</strong></div>
        <div><span style={{ color: 'var(--text-light)' }}>Expected: </span><strong>{fmtDate(order.expected_date)}</strong></div>
        <div><span style={{ color: 'var(--text-light)' }}>Received: </span><strong>{totalReceived} / {totalOrdered}</strong></div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 6, flex: '1 1 320px', minWidth: 240 }}>
          <span style={{ color: 'var(--text-light)', whiteSpace: 'nowrap' }}>Comments:</span>
          <textarea
            value={commentsDraft}
            onChange={e => { setCommentsDraft(e.target.value); setCommentsDirty(true); }}
            onFocus={e => { e.target.rows = 3; }}
            onBlur={e => { e.target.rows = 1; saveComments(); }}
            placeholder="Internal notes (vendor confirmations, special instructions, payment terms…)"
            rows={1}
            style={{
              flex: 1,
              padding: '6px 10px', borderRadius: 6, border: '1px solid var(--border)',
              fontSize: 13, fontFamily: 'inherit', resize: 'vertical',
              backgroundColor: 'var(--white)',
            }}
          />
          {commentsDirty && (
            <span title="Unsaved — click outside to save"
              style={{ fontSize: 11, color: '#f59e0b', whiteSpace: 'nowrap' }}>●</span>
          )}
        </div>
      </div>
      {/* Units recorded as received that never reached Shopify. This state
          lives on the server, so it survives a refresh, navigating away, or
          starting the next receive batch — the three ways a pending push
          used to disappear without trace. */}
      {(order.unpushed_qty || 0) > 0 && !editMode && (
        <div style={{
          margin: '0 24px 12px', padding: '12px 16px', borderRadius: 8,
          backgroundColor: '#fef3c7', border: '1px solid #f59e0b',
          display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 16,
        }}>
          <div style={{ fontSize: 13, color: '#92400e' }}>
            <strong>{order.unpushed_qty} unit{order.unpushed_qty === 1 ? '' : 's'}</strong>
            {' '}across {order.unpushed_lines} line{order.unpushed_lines === 1 ? '' : 's'}
            {' '}received but <strong>not yet in Shopify</strong>.
          </div>
          <button onClick={() => handleIncreaseStock(true)} disabled={reviewLoading}
            style={{
              padding: '8px 18px', borderRadius: 6, border: 'none', whiteSpace: 'nowrap',
              backgroundColor: '#f59e0b', color: '#fff', fontWeight: 700, fontSize: 12,
              cursor: reviewLoading ? 'default' : 'pointer',
            }}>
            {reviewLoading ? 'Loading…' : 'Push to Shopify'}
          </button>
        </div>
      )}

      {/* Receive items header. Waitlist top-up runs automatically on PO
          mount (see the useEffect on orderId) so there's no button here
          for it — operators don't have to remember to click anything. */}
      <div style={{ padding: '12px 24px', backgroundColor: 'var(--white)', display: 'flex', justifyContent: 'space-between', alignItems: 'center', borderBottom: '1px solid var(--border)' }}>
        <h3 style={{ fontSize: 15, fontWeight: 700, margin: 0 }}>Receive items</h3>
        <div style={{ display: 'flex', gap: 8 }}>
          {order.status !== 'closed' && (
            <button onClick={() => setShowAddItem(true)}
              title="Add a line item to this purchase order"
              style={{ padding: '6px 16px', borderRadius: 6, border: '1px solid var(--border)', backgroundColor: 'var(--white)', color: 'var(--text)', fontWeight: 600, fontSize: 12, cursor: 'pointer' }}>
              + Add item
            </button>
          )}
          {hasUnreceived && !editMode && order.status !== 'closed' && (
            <button onClick={fillAllRemaining} style={{ padding: '6px 16px', borderRadius: 6, border: '2px solid var(--green)', backgroundColor: 'transparent', color: 'var(--green)', fontWeight: 600, fontSize: 12, cursor: 'pointer' }}>Receive remaining</button>
          )}
        </div>
      </div>
      {/* Items table */}
      <div style={{ flex: 1, overflow: 'auto', backgroundColor: 'var(--bg)' }}>
        <table style={{ width: '100%', borderCollapse: 'collapse', backgroundColor: 'var(--white)', fontSize: 13 }}>
          <thead style={{ position: 'sticky', top: 0, backgroundColor: 'var(--white)', zIndex: 10 }}>
            <tr style={{ borderBottom: '2px solid var(--border)' }}>
              <th onClick={() => handleDetailSort('product_title')} style={detailHeaderStyle('left')}>Name{detailSortArrow('product_title')}</th>
              <th onClick={() => handleDetailSort('sku')} style={detailHeaderStyle('left')}>SKU{detailSortArrow('sku')}</th>
              <th onClick={() => handleDetailSort('bin')} style={detailHeaderStyle('left')} title="Warehouse bin location (Shopify variant metafield stock.bin). Editable — changes save straight to Shopify.">Bin{detailSortArrow('bin')}</th>
              <th onClick={() => handleDetailSort('barcode')} style={detailHeaderStyle('left')} title="Shopify variant barcode (UPC/EAN). Editable — changes save straight to Shopify.">Barcode{detailSortArrow('barcode')}</th>
              <th onClick={() => handleDetailSort('ordered_qty')} style={detailHeaderStyle('right')} title="Ordered quantity. The bracketed number, when shown, is the seasonal-adjusted replenishment recommendation for reference.">Ordered{detailSortArrow('ordered_qty')}</th>
              <th onClick={() => handleDetailSort('current_stock')} style={detailHeaderStyle('right')} title="Current Shopify stock for this SKU. Dash means the SKU isn't in the velocity cache (e.g. brand new product not yet in Shopify).">In stock{detailSortArrow('current_stock')}</th>
              <th onClick={() => handleDetailSort('on_order_other')} style={detailHeaderStyle('right')} title="Units of this SKU already on order on OTHER open purchase orders (excludes this PO). Helps avoid double-ordering.">On order{detailSortArrow('on_order_other')}</th>
              <th onClick={() => handleDetailSort('avg_monthly_velocity')} style={detailHeaderStyle('right')} title="Average units sold per month over the trailing 365 days (or actual listed window for new products). Not seasonally adjusted.">AVG/mo{detailSortArrow('avg_monthly_velocity')}</th>
              <th onClick={() => handleDetailSort('received_qty')} style={detailHeaderStyle('right')}>Received{detailSortArrow('received_qty')}</th>
              <th onClick={() => handleDetailSort('remaining')} style={detailHeaderStyle('right')}>Remaining{detailSortArrow('remaining')}</th>
              <th onClick={() => handleDetailSort('waiter_count')} style={detailHeaderStyle('right')} title="Customers waiting for back-in-stock. +N = additional units still needed beyond this PO's qty + current stock (60% conversion assumed). +0 (or no badge) means this PO already covers the waitlist.">Waiters{detailSortArrow('waiter_count')}</th>
              <th style={{ padding: '10px 12px', textAlign: 'center', color: 'var(--text-light)', fontWeight: 500, width: 140 }}>To receive</th>
              <th onClick={() => handleDetailSort('unit_cost')} style={detailHeaderStyle('right')} title="Cost per unit. Editable while in Edit mode.">Unit cost{detailSortArrow('unit_cost')}</th>
              <th onClick={() => handleDetailSort('line_total')} style={detailHeaderStyle('right')} title="Total cost for this line = ordered quantity × unit cost.">Total cost{detailSortArrow('line_total')}</th>
              {editMode && <th style={{ padding: '10px 12px', width: 36 }} />}
              <th style={{ padding: '10px 12px', textAlign: 'center', color: 'var(--text-light)', fontWeight: 500, width: 60 }} title="Click a row's icon to exclude that product from future replenishment suggestions. Click again to re-enable.">Suggest</th>
            </tr>
          </thead>
          <tbody>
            {items.map(item => {
              const remaining = (item.ordered_qty||0) - (item.received_qty||0);
              const ev = editValues[item.id];
              const toRecQty = toReceive[item.id] || 0;
              return (
                <tr key={item.id} style={{ borderBottom: '1px solid var(--border)' }}>
                  <td style={{ padding: '12px' }}>
                    <div style={{ fontWeight: 500, display: 'flex', alignItems: 'center', gap: 6 }}>
                      {item.product_title}
                      {item.is_vendor_sale && (
                        <span
                          title={
                            item.regular_unit_cost
                              ? `Bought on vendor sale at $${(item.unit_cost || 0).toFixed(2)} (regular $${item.regular_unit_cost.toFixed(2)})`
                              : 'Bought on vendor sale'
                          }
                          style={{
                            padding: '1px 6px', borderRadius: 8,
                            backgroundColor: '#fef3c7', color: '#92400e',
                            fontSize: 9, fontWeight: 700, letterSpacing: 0.4,
                            textTransform: 'uppercase', whiteSpace: 'nowrap',
                          }}>
                          ON SALE
                        </span>
                      )}
                    </div>
                    {item.variant_title && <div style={{ fontSize: 11, color: 'var(--text-muted)' }}>{item.variant_title}</div>}
                  </td>
                  <td style={{ padding: '12px', color: 'var(--text-light)' }}>{item.sku}</td>
                  <td style={{ padding: '8px 12px' }}>
                    {/* Bin location — editable, writes stock.bin metafield
                        to Shopify on blur. Always editable (not gated on
                        editMode) since it's an independent per-variant
                        attribute, not a PO line field. */}
                    <input
                      type="text"
                      value={binEdits[item.id] ?? item.bin ?? ''}
                      placeholder="-"
                      disabled={binSaving.has(item.id)}
                      onChange={e => setBinEdits(p => ({ ...p, [item.id]: e.target.value }))}
                      onBlur={() => handleBinSave(item)}
                      onKeyDown={e => { if (e.key === 'Enter') e.target.blur(); }}
                      title="Shopify variant metafield stock.bin — edits save to Shopify on blur"
                      style={{
                        width: 80, padding: '4px 6px', borderRadius: 4,
                        border: '1px solid var(--border)', fontSize: 13,
                        backgroundColor: binSaving.has(item.id) ? '#f1f5f9' : 'var(--white)',
                        color: 'var(--text)',
                      }}
                    />
                  </td>
                  <td style={{ padding: '8px 12px' }}>
                    {/* Barcode — editable, writes the native Shopify variant
                        barcode (UPC/EAN) on blur. Always editable (not gated
                        on editMode) since it's a per-variant attribute, not a
                        PO line field. */}
                    <input
                      type="text"
                      value={barcodeEdits[item.id] ?? item.barcode ?? ''}
                      placeholder="-"
                      disabled={barcodeSaving.has(item.id)}
                      onChange={e => setBarcodeEdits(p => ({ ...p, [item.id]: e.target.value }))}
                      onBlur={() => handleBarcodeSave(item)}
                      onKeyDown={e => { if (e.key === 'Enter') e.target.blur(); }}
                      title="Shopify variant barcode (UPC/EAN) — edits save to Shopify on blur"
                      style={{
                        width: 120, padding: '4px 6px', borderRadius: 4,
                        border: '1px solid var(--border)', fontSize: 13,
                        backgroundColor: barcodeSaving.has(item.id) ? '#f1f5f9' : 'var(--white)',
                        color: 'var(--text)',
                      }}
                    />
                  </td>
                  <td style={{ padding: '12px', textAlign: 'right' }}>
                    {editMode ? (
                      <input type="number" value={ev?.ordered_qty ?? item.ordered_qty} min={0}
                        onChange={e => setEditValues(p => ({...p,[item.id]:{...p[item.id],ordered_qty:parseInt(e.target.value)||0}}))}
                        style={{ width: 60, textAlign: 'right', padding: '4px 6px', borderRadius: 4, border: '1px solid var(--border)', fontSize: 13 }} />
                    ) : (
                      <>
                        {item.ordered_qty}
                        {item.seasonal_replenish_qty != null && item.seasonal_replenish_qty > 0 && item.seasonal_replenish_qty !== item.ordered_qty && (
                          <span
                            title="Seasonal-adjusted replenishment recommendation, for reference"
                            style={{ marginLeft: 4, color: 'var(--text-muted)', fontSize: 11 }}>
                            ({item.seasonal_replenish_qty})
                          </span>
                        )}
                      </>
                    )}
                  </td>
                  <td style={{ padding: '12px', textAlign: 'right' }}>
                    {item.current_stock == null ? (
                      <span style={{ color: 'var(--text-muted)' }} title="Not in velocity cache yet">—</span>
                    ) : (
                      <span style={{
                        color: item.current_stock <= 0 ? '#dc2626' : 'var(--text)',
                        fontWeight: item.current_stock <= 0 ? 600 : 400,
                      }}>{item.current_stock}</span>
                    )}
                  </td>
                  <td style={{ padding: '12px', textAlign: 'right' }}>
                    {item.on_order_other > 0 ? (
                      <span
                        title={`${item.on_order_other} unit${item.on_order_other === 1 ? '' : 's'} already on order on other open PO(s) — excludes this PO`}
                        style={{ color: 'var(--green)', fontWeight: 600 }}>
                        {item.on_order_other}
                      </span>
                    ) : (
                      <span style={{ color: 'var(--text-muted)' }}>—</span>
                    )}
                  </td>
                  <td style={{ padding: '12px', textAlign: 'right' }}>
                    {item.avg_monthly_velocity == null ? (
                      <span style={{ color: 'var(--text-muted)' }}>—</span>
                    ) : (item.avg_monthly_velocity || 0).toFixed(2)}
                  </td>
                  <td style={{ padding: '12px', textAlign: 'right' }}
                      title={receiptTooltip(item)}>
                    {editMode ? <input type="number" value={ev?.received_qty ?? item.received_qty} min={0} onChange={e => setEditValues(p => ({...p,[item.id]:{...p[item.id],received_qty:parseInt(e.target.value)||0}}))} style={{ width: 60, textAlign: 'right', padding: '4px 6px', borderRadius: 4, border: '1px solid var(--border)', fontSize: 13 }} /> : (
                      <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6, justifyContent: 'flex-end' }}>
                        <span style={{
                          borderBottom: (item.receipts?.length > 0) ? '1px dotted var(--text-muted)' : 'none',
                          cursor: (item.receipts?.length > 0) ? 'help' : 'default',
                        }}>{item.received_qty}</span>
                        {(item.received_qty || 0) > 0 && order.status !== 'closed' && (
                          <button
                            onClick={() => handleUndoReceive(item)}
                            disabled={undoing === item.id}
                            title={`Undo receiving ${item.received_qty} \u00d7 ${item.sku}`}
                            style={{
                              border: '1px solid var(--border)', borderRadius: 4,
                              background: 'var(--white)', color: 'var(--text-muted)',
                              fontSize: 11, lineHeight: 1.3, padding: '1px 5px',
                              cursor: undoing === item.id ? 'default' : 'pointer',
                              opacity: undoing === item.id ? 0.5 : 1,
                            }}>\u21b6</button>
                        )}
                      </span>
                    )}
                  </td>
                  <td style={{ padding: '12px', textAlign: 'right', fontWeight: 600 }}>
                    {editMode ? ((ev?.ordered_qty ?? item.ordered_qty) - (ev?.received_qty ?? item.received_qty)) : remaining}
                  </td>
                  <td style={{ padding: '12px', textAlign: 'right' }}>
                    {item.waiter_count > 0 ? (
                      <span
                        title={
                          item.demand_boost > 0
                            ? `${item.waiter_count} customer${item.waiter_count === 1 ? '' : 's'} waiting · ideal coverage ${item.demand_boost_ideal} units · +${item.demand_boost} more needed beyond this PO's qty (${item.ordered_qty || 0}) + stock (${item.current_stock ?? 0})`
                            : `${item.waiter_count} customer${item.waiter_count === 1 ? '' : 's'} waiting · this PO already covers the waitlist (${item.ordered_qty || 0} ordered + ${item.current_stock ?? 0} in stock vs ${item.demand_boost_ideal} ideal)`
                        }
                        style={{
                          padding: '2px 8px', borderRadius: 10,
                          backgroundColor: item.demand_boost > 0 ? '#eef2ff' : '#dcfce7',
                          color: item.demand_boost > 0 ? '#6366f1' : '#15803d',
                          fontWeight: 700, fontSize: 11,
                        }}>
                        {item.waiter_count}{item.demand_boost > 0 ? ` (+${item.demand_boost})` : ' ✓'}
                      </span>
                    ) : (
                      <span style={{ color: 'var(--text-muted)', fontSize: 11 }}>—</span>
                    )}
                  </td>
                  <td style={{ padding: '12px', textAlign: 'center' }}>
                    {!editMode && remaining > 0 && order.status !== 'closed' ? (
                      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 6 }}>
                        <button onClick={() => fillToReceive(item.id, remaining)} title="Fill remaining" style={{ width: 28, height: 28, borderRadius: 14, border: '1px solid var(--green)', backgroundColor: 'transparent', color: 'var(--green)', cursor: 'pointer', fontSize: 14, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>{'\u2192'}</button>
                        <input type="number" value={toRecQty || ''} min={0} max={remaining} onChange={e => updateToReceive(item.id, e.target.value)} style={{ width: 55, textAlign: 'right', padding: '4px 6px', borderRadius: 4, border: '1px solid var(--border)', fontSize: 13 }} />
                      </div>
                    ) : remaining === 0 && !editMode ? <span style={{ color: 'var(--green)', fontSize: 14 }}>{'\u2713'}</span> : null}
                  </td>
                  <td style={{ padding: '12px', textAlign: 'right' }}>
                    {editMode ? <input type="number" step="0.01" value={ev?.unit_cost ?? item.unit_cost} min={0} onChange={e => setEditValues(p => ({...p,[item.id]:{...p[item.id],unit_cost:parseFloat(e.target.value)||0}}))} style={{ width: 80, textAlign: 'right', padding: '4px 6px', borderRadius: 4, border: '1px solid var(--border)', fontSize: 13 }} /> : fmt(item.unit_cost||0)}
                  </td>
                  <td style={{ padding: '12px', textAlign: 'right', fontWeight: 500 }} title="Ordered quantity × unit cost">
                    {fmt((ev?.unit_cost ?? item.unit_cost ?? 0) * (ev?.ordered_qty ?? item.ordered_qty ?? 0))}
                  </td>
                  {editMode && (
                    <td style={{ padding: '12px', textAlign: 'center' }}>
                      <button
                        onClick={() => handleDeleteItem(item)}
                        title={`Remove ${item.sku} from this PO`}
                        style={{
                          width: 24, height: 24, borderRadius: 12,
                          border: '1px solid var(--border)', backgroundColor: 'transparent',
                          color: '#dc2626', cursor: 'pointer', fontSize: 14,
                          display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
                          padding: 0, lineHeight: 1,
                        }}>
                        {'\u00d7'}
                      </button>
                    </td>
                  )}
                  <td style={{ padding: '12px', textAlign: 'center' }}>
                    {/* Replenishment-suggestion toggle. \ud83d\udeab solid red = excluded
                        (never suggested); faded gray = normal. Reversible. */}
                    <button
                      onClick={() => handleToggleReplenishable(item)}
                      disabled={replToggling.has(item.id)}
                      title={item.is_non_replenishable
                        ? `${item.sku} is EXCLUDED from replenishment suggestions \u2014 click to re-enable`
                        : `Never suggest ${item.sku} for replenishment again`}
                      style={{
                        width: 28, height: 28, borderRadius: 14,
                        border: item.is_non_replenishable ? '1px solid #dc2626' : '1px solid var(--border)',
                        backgroundColor: item.is_non_replenishable ? '#fee2e2' : 'transparent',
                        cursor: replToggling.has(item.id) ? 'wait' : 'pointer',
                        fontSize: 14, lineHeight: 1, padding: 0,
                        display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
                        opacity: item.is_non_replenishable ? 1 : 0.35,
                      }}>
                      {'\u{1F6AB}'}
                    </button>
                  </td>
                </tr>
              );
            })}
          </tbody>
          <tfoot><tr style={{ borderTop: '2px solid var(--border)', fontWeight: 700, fontSize: 12 }}>
            <td style={{ padding: '12px' }}>{items.length} items</td><td /><td /><td />
            <td style={{ padding: '12px', textAlign: 'right' }}>{totalOrdered}</td>
            <td style={{ padding: '12px', textAlign: 'right' }}>{totalInStock}</td>
            <td style={{ padding: '12px', textAlign: 'right' }}>{totalOnOrderOther || ''}</td>
            <td />
            <td style={{ padding: '12px', textAlign: 'right' }}>{totalReceived}</td>
            <td style={{ padding: '12px', textAlign: 'right' }}>{totalOrdered - totalReceived}</td>
            <td />
            <td style={{ padding: '12px', textAlign: 'center' }}>{Object.values(toReceive).reduce((s,v)=>s+(v||0),0) || ''}</td>
            <td />
            <td style={{ padding: '12px', textAlign: 'right' }}>{fmt(totalCost)}</td>
            {editMode && <td />}
            <td />
          </tr></tfoot>
        </table>
      </div>
      {/* Bottom bar */}
      <div style={{ padding: '12px 24px', borderTop: '1px solid var(--border)', backgroundColor: 'var(--white)', display: 'flex', gap: 12, alignItems: 'center' }}>
        {editMode && (<>
          <button onClick={saveEdits} disabled={savingEdits} style={{ padding: '10px 28px', borderRadius: 20, border: 'none', backgroundColor: 'var(--green)', color: '#fff', fontWeight: 700, fontSize: 14, cursor: 'pointer' }}>{savingEdits ? 'Saving...' : 'Save'}</button>
          <button onClick={() => setEditMode(false)} disabled={savingEdits} style={{ padding: '10px 24px', borderRadius: 20, border: 'none', backgroundColor: '#e74c3c', color: '#fff', fontWeight: 700, fontSize: 14, cursor: 'pointer' }}>Discard</button>
        </>)}
        {!editMode && hasPendingReceives && (<>
          <button onClick={handleSave} disabled={saving} style={{ padding: '10px 28px', borderRadius: 20, border: 'none', backgroundColor: 'var(--green)', color: '#fff', fontWeight: 700, fontSize: 14, cursor: 'pointer' }}>{saving ? 'Saving...' : 'Save'}</button>
          <button onClick={discardReceives} disabled={saving} style={{ padding: '10px 24px', borderRadius: 20, border: 'none', backgroundColor: '#e74c3c', color: '#fff', fontWeight: 700, fontSize: 14, cursor: 'pointer' }}>Discard</button>
        </>)}
        {!editMode && !hasPendingReceives && (<>
          <button onClick={onBack} style={{ padding: '10px 24px', borderRadius: 6, border: '1px solid var(--border)', backgroundColor: 'transparent', fontSize: 13, cursor: 'pointer', fontWeight: 600 }}>Back to stock orders</button>
          {lastReceived && lastReceived.length > 0 && <button onClick={handleIncreaseStock} disabled={reviewLoading} style={{ padding: '10px 24px', borderRadius: 6, border: 'none', backgroundColor: 'var(--green)', color: '#fff', fontWeight: 700, fontSize: 13, cursor: 'pointer' }}>{reviewLoading ? 'Loading...' : 'Increase stock in Shopify'}</button>}
          {lastReceived && lastReceived.length > 0 && (
            <button
              onClick={handlePrintRfidLabels}
              disabled={printingRfid}
              title="Queue one RFID label per received unit on the warehouse Zebra (via the RFID Stickers app). Each label prints the product's home bin; pair the tags in the RFID app's Batch tagging."
              style={{ marginLeft: 'auto', padding: '10px 24px', borderRadius: 6, border: 'none', backgroundColor: '#2980b9', color: '#fff', fontWeight: 700, fontSize: 13, cursor: 'pointer' }}
            >
              {printingRfid ? 'Queueing...' : 'Print labels'}
            </button>
          )}
        </>)}
      </div>
      {showStockModal && <StockUpdateModal reviewData={reviewData} onApply={handleApplyStockUpdate} onCancel={() => { setShowStockModal(false); setReviewData(null); }} applying={applying} />}
      {showAddItem && (
        <AddItemModal
          orderId={orderId}
          orderVendor={order.vendor}
          onClose={() => setShowAddItem(false)}
          onAdded={() => { setShowAddItem(false); fetchOrder(); }}
          onToast={onToast}
        />
      )}
    </div>
  );
}

// ─── Add Item Modal ──────────────────────────────────────────

function AddItemModal({ orderId, orderVendor, onClose, onAdded, onToast }) {
  const [sku, setSku] = useState('');
  const [lookup, setLookup] = useState(null); // null = not searched, {found, ...} = result
  const [looking, setLooking] = useState(false);
  const [manual, setManual] = useState(false); // user forced manual mode
  const [qty, setQty] = useState(1);
  const [cost, setCost] = useState('');
  const [title, setTitle] = useState('');
  const [vendor, setVendor] = useState('');
  const [saving, setSaving] = useState(false);

  const reset = () => { setLookup(null); setManual(false); setQty(1); setCost(''); setTitle(''); setVendor(''); };

  const doLookup = async () => {
    if (!sku.trim()) return;
    setLooking(true);
    setLookup(null);
    setManual(false);
    try {
      const res = await api.lookupSku(sku.trim());
      setLookup(res);
      if (res.found) {
        // Prefill — user can still override
        setTitle(res.product_title || '');
        setVendor(res.vendor || '');
        setCost(res.cost != null ? String(res.cost) : '');
      } else {
        // Unknown SKU — user will need to fill in manually
        setTitle('');
        setCost('');
        setVendor(orderVendor || '');
      }
    } catch (e) { onToast?.('Lookup failed: ' + e.message, 'error'); }
    setLooking(false);
  };

  const handleSave = async () => {
    if (!sku.trim()) { onToast?.('SKU is required', 'error'); return; }
    const q = parseInt(qty);
    if (!q || q <= 0) { onToast?.('Quantity must be > 0', 'error'); return; }
    // For items not found in the cache, require a product title
    if ((!lookup?.found || manual) && !title.trim()) {
      onToast?.('Product title is required for new items', 'error');
      return;
    }
    setSaving(true);
    try {
      const payload = {
        sku: sku.trim(),
        ordered_qty: q,
        unit_cost: cost !== '' ? parseFloat(cost) : null,
      };
      // Include overrides whenever user has entered values
      if (title.trim()) payload.product_title = title.trim();
      if (vendor.trim()) payload.vendor = vendor.trim();
      const res = await api.addStockOrderItem(orderId, payload);
      onToast?.(res.source === 'cache'
        ? `Added ${sku.trim()} from inventory`
        : `Added new item ${sku.trim()} (manual entry)`,
        'success');
      onAdded?.();
    } catch (e) { onToast?.('Add failed: ' + e.message, 'error'); }
    setSaving(false);
  };

  // Auto-lookup on Enter in the SKU field
  const handleSkuKey = (e) => { if (e.key === 'Enter') { e.preventDefault(); doLookup(); } };

  // Determine the state we're in for rendering
  const showForm = lookup !== null || manual;
  const isNewItem = manual || (lookup && !lookup.found);

  const inp = { padding: '7px 10px', borderRadius: 6, border: '1px solid var(--border)', fontSize: 13, width: '100%', boxSizing: 'border-box' };

  return (
    <div onClick={onClose} style={{ position: 'fixed', inset: 0, backgroundColor: 'rgba(0,0,0,0.5)', zIndex: 1000, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
      <div onClick={e => e.stopPropagation()}
        style={{ backgroundColor: 'var(--white)', borderRadius: 10, width: 500, maxHeight: '85vh', overflow: 'auto', boxShadow: '0 10px 40px rgba(0,0,0,0.2)' }}>
        <div style={{ padding: '16px 20px', borderBottom: '1px solid var(--border)', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <h3 style={{ margin: 0, fontSize: 16, fontWeight: 700 }}>Add item to PO</h3>
          <button onClick={onClose} style={{ background: 'none', border: 'none', fontSize: 20, cursor: 'pointer', color: 'var(--text-muted)' }}>×</button>
        </div>

        <div style={{ padding: 20 }}>
          {/* Step 1: SKU input + lookup */}
          <div style={{ marginBottom: 14 }}>
            <label style={{ fontSize: 11, fontWeight: 600, color: 'var(--text-muted)', display: 'block', marginBottom: 4 }}>SKU *</label>
            <div style={{ display: 'flex', gap: 6 }}>
              <input type="text" value={sku} onChange={e => { setSku(e.target.value); setLookup(null); setManual(false); }}
                onKeyDown={handleSkuKey} placeholder="e.g. 11068 or NEW-PRODUCT-001" autoFocus
                style={{ ...inp, flex: 1, fontFamily: 'monospace' }} />
              <button onClick={doLookup} disabled={!sku.trim() || looking}
                style={{ padding: '7px 16px', borderRadius: 6, border: '1px solid #6366f1', backgroundColor: '#6366f1', color: '#fff', fontWeight: 600, fontSize: 12, cursor: 'pointer', whiteSpace: 'nowrap' }}>
                {looking ? 'Looking up...' : 'Look up'}
              </button>
            </div>
            <div style={{ fontSize: 11, color: 'var(--text-muted)', marginTop: 4 }}>
              Look up an existing SKU to auto-fill, or type a new SKU and the form will let you enter details.
            </div>
          </div>

          {/* Lookup result banner */}
          {lookup && lookup.found && (
            <div style={{ padding: '10px 12px', backgroundColor: '#d1fae5', border: '1px solid #86efac', borderRadius: 6, marginBottom: 14, fontSize: 12 }}>
              <div style={{ fontWeight: 600, color: '#065f46' }}>✓ Found in inventory</div>
              <div style={{ color: '#064e3b', marginTop: 2 }}>{lookup.product_title}</div>
              <div style={{ color: '#047857', fontSize: 11, marginTop: 4 }}>
                Current stock: <strong>{lookup.current_stock}</strong>
                {lookup.cost > 0 && <> · Cost: <strong>${lookup.cost.toFixed(2)}</strong></>}
                {lookup.vendor && <> · Vendor: <strong>{lookup.vendor}</strong></>}
              </div>
            </div>
          )}
          {lookup && !lookup.found && !manual && (
            <div style={{ padding: '10px 12px', backgroundColor: '#fef3c7', border: '1px solid #fcd34d', borderRadius: 6, marginBottom: 14, fontSize: 12 }}>
              <div style={{ fontWeight: 600, color: '#92400e' }}>⚠ SKU not found in inventory</div>
              <div style={{ color: '#78350f', marginTop: 2 }}>
                This SKU isn't in your velocity cache. You can add it as a new item by entering the product title below.
              </div>
              <button onClick={() => setManual(true)}
                style={{ marginTop: 6, padding: '4px 10px', borderRadius: 4, border: '1px solid #92400e', backgroundColor: 'transparent', color: '#92400e', fontWeight: 600, fontSize: 11, cursor: 'pointer' }}>
                Add as new item →
              </button>
            </div>
          )}

          {/* Form fields — shown once we've looked up or user forced manual */}
          {showForm && (
            <>
              {isNewItem && (
                <div style={{ marginBottom: 12 }}>
                  <label style={{ fontSize: 11, fontWeight: 600, color: 'var(--text-muted)', display: 'block', marginBottom: 4 }}>Product title *</label>
                  <input value={title} onChange={e => setTitle(e.target.value)}
                    placeholder="Enter product name" style={inp} />
                </div>
              )}

              {isNewItem && (
                <div style={{ marginBottom: 12 }}>
                  <label style={{ fontSize: 11, fontWeight: 600, color: 'var(--text-muted)', display: 'block', marginBottom: 4 }}>Vendor</label>
                  <input value={vendor} onChange={e => setVendor(e.target.value)}
                    placeholder={orderVendor ? `Defaults to "${orderVendor}"` : 'e.g. Celestron'} style={inp} />
                </div>
              )}

              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10, marginBottom: 12 }}>
                <div>
                  <label style={{ fontSize: 11, fontWeight: 600, color: 'var(--text-muted)', display: 'block', marginBottom: 4 }}>Quantity *</label>
                  <input type="number" min="1" value={qty} onChange={e => setQty(e.target.value)}
                    style={inp} />
                </div>
                <div>
                  <label style={{ fontSize: 11, fontWeight: 600, color: 'var(--text-muted)', display: 'block', marginBottom: 4 }}>
                    Unit cost {lookup?.found && lookup.cost > 0 && <span style={{ color: 'var(--text-muted)', fontWeight: 400 }}>(from cache)</span>}
                  </label>
                  <input type="number" step="0.01" min="0" value={cost} onChange={e => setCost(e.target.value)}
                    placeholder={lookup?.found && lookup.cost > 0 ? String(lookup.cost) : '0.00'}
                    style={inp} />
                </div>
              </div>

              {lookup?.found && (
                <div style={{ fontSize: 10, color: 'var(--text-muted)', marginBottom: 10 }}>
                  Other fields (title, vendor) are pulled from inventory. Unit cost can be overridden for this line.
                </div>
              )}
            </>
          )}
        </div>

        <div style={{ padding: '14px 20px', borderTop: '1px solid var(--border)', display: 'flex', justifyContent: 'flex-end', gap: 8, backgroundColor: '#fafbfc' }}>
          <button onClick={onClose} style={{ padding: '8px 16px', borderRadius: 6, border: '1px solid var(--border)', backgroundColor: 'var(--white)', fontWeight: 600, fontSize: 12, cursor: 'pointer' }}>Cancel</button>
          <button onClick={handleSave} disabled={saving || !sku.trim() || !showForm}
            style={{ padding: '8px 20px', borderRadius: 6, border: 'none', backgroundColor: 'var(--green)', color: '#fff', fontWeight: 600, fontSize: 12, cursor: 'pointer', opacity: (saving || !sku.trim() || !showForm) ? 0.5 : 1 }}>
            {saving ? 'Adding...' : 'Add item'}
          </button>
        </div>
      </div>
    </div>
  );
}


// Stock Orders List
export default function StockOrdersPage({ onToast, resetSignal }) {
  const [orders, setOrders] = useState([]);
  const [loading, setLoading] = useState(true);
  const [statusFilter, setStatusFilter] = useState('open');
  const [vendorFilter, setVendorFilter] = useState('');
  const [searchTerm, setSearchTerm] = useState('');
  const [sortField, setSortField] = useState('reference_number');
  const [sortDir, setSortDir] = useState('desc');
  const [selectedOrderId, setSelectedOrderId] = useState(null);
  const [ipSyncOpen, setIpSyncOpen] = useState(false);
  const [ipSyncing, setIpSyncing] = useState(false);
  const [ipSyncResult, setIpSyncResult] = useState(null);
  const [ipClearExisting, setIpClearExisting] = useState(true);
  const [ipIncludeClosed, setIpIncludeClosed] = useState(false);

  const runIpSync = async () => {
    const willClear = ipClearExisting;
    const confirmMsg = willClear
      ? `This will DELETE all existing stock orders (${orders.length} visible here), then re-import from Inventory Planner. Continue?`
      : `Import orders from Inventory Planner (existing orders will be kept — duplicates possible). Continue?`;
    if (!window.confirm(confirmMsg)) return;
    setIpSyncing(true);
    setIpSyncResult(null);
    try {
      const result = await api.importStockOrdersFromIP({
        clearExisting: willClear,
        includeClosed: ipIncludeClosed,
        confirm: true,
      });
      setIpSyncResult(result);
      onToast(result.message, 'success');
      await fetchOrders();
    } catch (e) {
      onToast('IP sync failed: ' + e.message, 'error');
      setIpSyncResult({ error: e.message });
    }
    setIpSyncing(false);
  };

  const fetchOrders = useCallback(async (searchOverride) => {
    setLoading(true);
    try {
      const term = searchOverride !== undefined ? searchOverride : searchTerm;
      const d = await api.listStockOrders({
        status: statusFilter,
        vendor: vendorFilter || null,
        search: term || null,
      });
      setOrders(d.orders || []);
    } catch (e) { onToast('Failed: ' + e.message, 'error'); }
    setLoading(false);
  }, [statusFilter, vendorFilter, searchTerm, onToast]);

  // Re-fetch when filters change (immediate)
  useEffect(() => { fetchOrders(); /* eslint-disable-line react-hooks/exhaustive-deps */ }, [statusFilter, vendorFilter]);

  // External "go to list" signal: when the user clicks the Stock Orders
  // sidebar icon while a PO detail is open, App.jsx bumps `resetSignal`.
  // Use that as a trigger to drop the selected order so the list view
  // shows. Skips the initial render (resetSignal starts at 0).
  useEffect(() => {
    if (resetSignal) {
      setSelectedOrderId(null);
      fetchOrders();
    }
    /* eslint-disable-next-line react-hooks/exhaustive-deps */
  }, [resetSignal]);

  // Debounce search-driven re-fetch (300ms after user stops typing)
  useEffect(() => {
    const t = setTimeout(() => { fetchOrders(); /* eslint-disable-line react-hooks/exhaustive-deps */ }, 300);
    return () => clearTimeout(t);
  }, [searchTerm]); // eslint-disable-line react-hooks/exhaustive-deps

  const filtered = useMemo(() => {
    // Search is now server-side; this memo only handles sorting.
    const items = [...orders];
    items.sort((a, b) => {
      let av, bv;
      if (sortField === 'received_pct') {
        // Sort by % received — fully received goes to the bottom when ascending
        const aOrd = a.total_ordered || 0, aRec = a.total_received || 0;
        const bOrd = b.total_ordered || 0, bRec = b.total_received || 0;
        av = aOrd > 0 ? aRec / aOrd : -1;
        bv = bOrd > 0 ? bRec / bOrd : -1;
      } else if (sortField === 'expected_date' || sortField === 'created_at' || sortField === 'updated_at') {
        // Sort dates — nulls go last regardless of direction
        av = a[sortField] ? new Date(a[sortField]).getTime() : (sortDir === 'asc' ? Infinity : -Infinity);
        bv = b[sortField] ? new Date(b[sortField]).getTime() : (sortDir === 'asc' ? Infinity : -Infinity);
      } else {
        av = a[sortField]; bv = b[sortField];
        if (typeof av === 'string') av = av.toLowerCase();
        if (typeof bv === 'string') bv = bv.toLowerCase();
      }
      if (av === bv) return 0;
      return sortDir === 'desc' ? (av > bv ? -1 : 1) : (av < bv ? -1 : 1);
    });
    return items;
  }, [orders, sortField, sortDir]);

  const handleSort = (f) => { if (sortField === f) setSortDir(d => d === 'asc' ? 'desc' : 'asc'); else { setSortField(f); setSortDir('desc'); } };
  const sortArrow = (f) => sortField === f ? (sortDir === 'asc' ? ' \u2191' : ' \u2193') : '';
  const sortableHeaderStyle = (align = 'left', width) => ({
    padding: '10px 12px',
    textAlign: align,
    color: 'var(--text-light)',
    fontWeight: 500,
    cursor: 'pointer',
    userSelect: 'none',
    whiteSpace: 'nowrap',
    ...(width ? { width } : {}),
  });
  const vendors = useMemo(() => [...new Set(orders.map(o => o.vendor).filter(Boolean))].sort(), [orders]);

  if (selectedOrderId) return <StockOrderDetail orderId={selectedOrderId} onBack={() => { setSelectedOrderId(null); fetchOrders(); }} onToast={onToast} />;

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100vh' }}>
      <div style={{ padding: '14px 24px', borderBottom: '1px solid var(--border)', backgroundColor: 'var(--white)', display: 'flex', alignItems: 'center', gap: 12 }}>
        <h1 style={{ fontSize: 20, fontWeight: 700, margin: 0 }}>Stock orders</h1>
        <div style={{ display: 'flex', gap: 2, backgroundColor: '#f0f1f3', borderRadius: 6, padding: 2 }}>
          {[['open','Open'],['all','All'],['closed','Closed']].map(([v,l]) => <button key={v} onClick={() => setStatusFilter(v)} style={{ padding: '6px 14px', borderRadius: 4, border: 'none', fontSize: 12, fontWeight: 600, cursor: 'pointer', backgroundColor: statusFilter === v ? 'var(--white)' : 'transparent', color: statusFilter === v ? 'var(--green)' : 'var(--text-light)', boxShadow: statusFilter === v ? '0 1px 3px rgba(0,0,0,0.1)' : 'none' }}>{l}</button>)}
        </div>
        <select value={vendorFilter} onChange={e => setVendorFilter(e.target.value)} style={{ padding: '6px 12px', borderRadius: 6, border: '1px solid var(--border)', fontSize: 13, color: vendorFilter ? 'var(--green)' : 'var(--text)', fontWeight: vendorFilter ? 600 : 400 }}>
          <option value="">All Vendors</option>
          {vendors.map(v => <option key={v} value={v}>{v}</option>)}
        </select>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '6px 14px', borderRadius: 6, border: '1px solid var(--border)', backgroundColor: '#fafafa', maxWidth: 280, flex: 1 }}>
          <span style={{ color: 'var(--text-muted)', fontSize: 13 }}>{'\uD83D\uDD0D'}</span>
          <input type="text" placeholder="Search by reference, vendor, or SKU..." value={searchTerm} onChange={e => setSearchTerm(e.target.value)} style={{ border: 'none', outline: 'none', flex: 1, fontSize: 13, backgroundColor: 'transparent' }} />
        </div>
        <span style={{ fontSize: 13, color: 'var(--text-light)' }}>{filtered.length} orders</span>
        <button onClick={() => { setIpSyncOpen(true); setIpSyncResult(null); }} title="Import stock orders from Inventory Planner"
          style={{ padding: '6px 12px', borderRadius: 6, border: '1px solid #3498db', backgroundColor: 'transparent', color: '#3498db', fontSize: 12, fontWeight: 600, cursor: 'pointer' }}>
          {'\u21BB'} Sync from Inventory Planner
        </button>
      </div>
      <div style={{ flex: 1, overflow: 'auto', backgroundColor: 'var(--bg)' }}>
        {loading ? <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: 300, gap: 12 }}><div className="spinner" /><span style={{ color: 'var(--text-light)' }}>Loading...</span></div> :
        filtered.length === 0 ? <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', height: 300, gap: 12 }}><span style={{ fontSize: 40 }}>{'\uD83D\uDCCB'}</span><span style={{ color: 'var(--text-light)' }}>No stock orders found</span></div> :
        <table style={{ width: '100%', borderCollapse: 'collapse', backgroundColor: 'var(--white)', fontSize: 13 }}>
          <thead style={{ position: 'sticky', top: 0, backgroundColor: 'var(--white)', zIndex: 10 }}><tr style={{ borderBottom: '2px solid var(--border)' }}>
            <th onClick={() => handleSort('reference_number')} style={sortableHeaderStyle('left', 80)}>Ref #{sortArrow('reference_number')}</th>
            <th onClick={() => handleSort('status')} style={sortableHeaderStyle('left')}>Status{sortArrow('status')}</th>
            <th onClick={() => handleSort('vendor')} style={sortableHeaderStyle('left')}>Vendor{sortArrow('vendor')}</th>
            <th onClick={() => handleSort('received_pct')} style={sortableHeaderStyle('right')} title="Sort by % received">Received / Ordered{sortArrow('received_pct')}</th>
            <th onClick={() => handleSort('currency')} style={sortableHeaderStyle('center')}>Currency{sortArrow('currency')}</th>
            <th onClick={() => handleSort('created_at')} style={sortableHeaderStyle('left')}>Created{sortArrow('created_at')}</th>
            <th onClick={() => handleSort('expected_date')} style={sortableHeaderStyle('left')}>Expected{sortArrow('expected_date')}</th>
            <th onClick={() => handleSort('total_cost')} style={sortableHeaderStyle('right')}>Total{sortArrow('total_cost')}</th>
            <th onClick={() => handleSort('updated_at')} style={sortableHeaderStyle('left')}>Last modified{sortArrow('updated_at')}</th>
          </tr></thead>
          <tbody>
            {filtered.map(order => {
              const isPD = order.expected_date && new Date(order.expected_date) < new Date() && order.status !== 'closed';
              return (<tr key={order.id} onClick={() => setSelectedOrderId(order.id)} style={{ borderBottom: '1px solid var(--border)', cursor: 'pointer' }} onMouseEnter={e => e.currentTarget.style.backgroundColor='#fafbfc'} onMouseLeave={e => e.currentTarget.style.backgroundColor='transparent'}>
                <td style={{ padding: '12px', fontWeight: 600, color: 'var(--green)' }}>{order.reference_number}</td>
                <td style={{ padding: '12px' }}><div style={{ display: 'flex', gap: 6, alignItems: 'center', flexWrap: 'wrap' }}><StatusBadge status={order.status} />{isPD && <span style={{ padding: '2px 6px', borderRadius: 4, fontSize: 10, fontWeight: 600, color: '#e74c3c', backgroundColor: '#fde8e5' }}>Past due</span>}</div></td>
                <td style={{ padding: '12px' }}>{order.vendor}</td>
                <td style={{ padding: '12px', textAlign: 'right' }}>{order.total_received||0} / {order.total_ordered||0}</td>
                <td style={{ padding: '12px', textAlign: 'center' }}>{order.currency || 'CAD'}</td>
                <td style={{ padding: '12px', fontSize: 12 }}>{fmtDate(order.created_at)}</td>
                <td style={{ padding: '12px', fontSize: 12 }}>{fmtDate(order.expected_date)}</td>
                <td style={{ padding: '12px', textAlign: 'right', fontWeight: 500 }}>{fmt(order.total_cost||0)}</td>
                <td style={{ padding: '12px', fontSize: 12, color: 'var(--text-light)' }}>{fmtDateTime(order.updated_at)}</td>
              </tr>);
            })}
          </tbody>
          <tfoot><tr style={{ borderTop: '2px solid var(--border)', fontWeight: 700 }}><td colSpan={7} /><td style={{ padding: '12px', textAlign: 'right' }}>{fmt(filtered.reduce((s,o)=>s+(o.total_cost||0),0))}</td><td /></tr></tfoot>
        </table>}
      </div>
      <div style={{ padding: '12px 24px', borderTop: '1px solid var(--border)', backgroundColor: 'var(--white)', display: 'flex', gap: 10 }}>
        <button style={{ padding: '8px 16px', borderRadius: 6, border: '1px solid var(--border)', backgroundColor: 'transparent', fontSize: 13, cursor: 'pointer' }}>{'\uD83D\uDCE4'} Export</button>
      </div>

      {/* Inventory Planner sync modal */}
      {ipSyncOpen && (
        <div style={{ position: 'fixed', top: 0, left: 0, right: 0, bottom: 0, backgroundColor: 'rgba(0,0,0,0.4)', zIndex: 1000, display: 'flex', alignItems: 'center', justifyContent: 'center' }}
             onClick={() => !ipSyncing && setIpSyncOpen(false)}>
          <div style={{ backgroundColor: 'var(--white)', borderRadius: 8, padding: 24, maxWidth: 560, width: '90%', boxShadow: '0 20px 40px rgba(0,0,0,0.2)' }}
               onClick={e => e.stopPropagation()}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 }}>
              <h2 style={{ fontSize: 18, fontWeight: 700, margin: 0 }}>Sync Stock Orders from Inventory Planner</h2>
              <button onClick={() => !ipSyncing && setIpSyncOpen(false)} disabled={ipSyncing}
                style={{ border: 'none', background: 'none', fontSize: 18, cursor: ipSyncing ? 'not-allowed' : 'pointer', color: 'var(--text-muted)' }}>✕</button>
            </div>

            <p style={{ fontSize: 13, color: 'var(--text-light)', marginBottom: 16 }}>
              Pulls purchase orders from the Inventory Planner API using the account credentials configured in Azure.
              Each TC order gets a new sequential reference number; the original IP reference and ID are saved in the order's notes for traceability.
            </p>

            <div style={{ backgroundColor: '#f8f9fa', borderRadius: 6, padding: 12, marginBottom: 16 }}>
              <label style={{ display: 'flex', alignItems: 'flex-start', gap: 8, cursor: 'pointer', marginBottom: 10 }}>
                <input type="checkbox" checked={ipClearExisting} onChange={e => setIpClearExisting(e.target.checked)}
                  disabled={ipSyncing} style={{ marginTop: 2, accentColor: '#e74c3c' }} />
                <div>
                  <div style={{ fontWeight: 600, fontSize: 13 }}>Clear existing stock orders first</div>
                  <div style={{ fontSize: 11, color: 'var(--text-muted)', marginTop: 2 }}>
                    Deletes all TC stock orders and line items before importing. Recommended to avoid duplicates when TC data is stale.
                  </div>
                </div>
              </label>

              <label style={{ display: 'flex', alignItems: 'flex-start', gap: 8, cursor: 'pointer' }}>
                <input type="checkbox" checked={ipIncludeClosed} onChange={e => setIpIncludeClosed(e.target.checked)}
                  disabled={ipSyncing} style={{ marginTop: 2, accentColor: 'var(--green)' }} />
                <div>
                  <div style={{ fontWeight: 600, fontSize: 13 }}>Include closed / cancelled orders</div>
                  <div style={{ fontSize: 11, color: 'var(--text-muted)', marginTop: 2 }}>
                    By default only open orders are imported. Enable this to pull full history (slower, more data).
                  </div>
                </div>
              </label>
            </div>

            {ipClearExisting && !ipSyncResult && (
              <div style={{ padding: '10px 14px', borderRadius: 6, backgroundColor: '#fef5f5', borderLeft: '4px solid #e74c3c', marginBottom: 16, fontSize: 12 }}>
                <strong style={{ color: '#e74c3c' }}>⚠ Destructive:</strong> All {orders.length}+ existing stock orders (and their items) will be deleted. This cannot be undone.
              </div>
            )}

            {ipSyncResult && (
              <div style={{ padding: 12, borderRadius: 6, backgroundColor: ipSyncResult.error ? '#fef5f5' : '#e8f5f0', marginBottom: 16, fontSize: 12 }}>
                {ipSyncResult.error ? (
                  <span style={{ color: '#e74c3c' }}>Error: {ipSyncResult.error}</span>
                ) : (
                  <div>
                    <div style={{ fontWeight: 600, marginBottom: 6 }}>✓ Sync complete</div>
                    {ipSyncResult.cleared && (
                      <div>Cleared {ipSyncResult.cleared.orders_deleted} orders ({ipSyncResult.cleared.items_deleted} items)</div>
                    )}
                    {ipSyncResult.imported && (
                      <div>
                        Imported {ipSyncResult.imported.imported} orders ({ipSyncResult.imported.total_items} items)
                        {ipSyncResult.imported.skipped_empty > 0 && `, skipped ${ipSyncResult.imported.skipped_empty}`}
                        {ipSyncResult.imported.errors?.length > 0 && ` — ${ipSyncResult.imported.errors.length} errors`}
                      </div>
                    )}
                  </div>
                )}
              </div>
            )}

            <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8 }}>
              <button onClick={() => !ipSyncing && setIpSyncOpen(false)} disabled={ipSyncing}
                style={{ padding: '8px 18px', borderRadius: 6, border: '1px solid var(--border)', backgroundColor: 'transparent', fontSize: 13, cursor: ipSyncing ? 'not-allowed' : 'pointer' }}>
                {ipSyncResult ? 'Close' : 'Cancel'}
              </button>
              {!ipSyncResult && (
                <button onClick={runIpSync} disabled={ipSyncing}
                  style={{ padding: '8px 18px', borderRadius: 6, border: 'none',
                    backgroundColor: ipClearExisting ? '#e74c3c' : '#3498db',
                    color: '#fff', fontWeight: 600, fontSize: 13, cursor: ipSyncing ? 'wait' : 'pointer' }}>
                  {ipSyncing ? 'Syncing...' : (ipClearExisting ? 'Clear & Import' : 'Import')}
                </button>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
