import { useState, useEffect, Fragment } from 'react';
import * as api from './api';

const fmt = (n) => new Intl.NumberFormat('en-CA', { style: 'currency', currency: 'CAD', minimumFractionDigits: 0, maximumFractionDigits: 0 }).format(n || 0);
const fmtFull = (n) => new Intl.NumberFormat('en-CA', { style: 'currency', currency: 'CAD', minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(n || 0);

const inputStyle = {
  padding: '6px 10px', borderRadius: 6, border: '1px solid var(--border)',
  backgroundColor: 'var(--input-bg)', color: 'var(--text)', fontSize: 12, width: '100%',
};


function StatusBadge({ status }) {
  const styles = {
    pending: { bg: '#fef3c7', color: '#d97706', label: 'pending' },
    approved: { bg: '#dcfce7', color: '#16a34a', label: 'approved' },
    auto_imported: { bg: '#dbeafe', color: '#2563eb', label: 'auto-imported' },
    rejected: { bg: '#fee2e2', color: '#dc2626', label: 'rejected' },
    extraction_failed: { bg: '#fee2e2', color: '#dc2626', label: 'extraction failed' },
  };
  const s = styles[status] || { bg: 'var(--hover-bg)', color: 'var(--text-muted)', label: status };
  return (
    <span style={{ padding: '2px 8px', borderRadius: 4, fontSize: 10, fontWeight: 600,
      backgroundColor: s.bg, color: s.color }}>{s.label}</span>
  );
}


function MatchBadge({ type }) {
  const styles = {
    exact: { bg: '#dcfce7', color: '#16a34a', label: 'Exact' },
    mapped: { bg: '#dbeafe', color: '#2563eb', label: 'Saved' },
    suggested: { bg: '#fef3c7', color: '#d97706', label: 'Suggested' },
    unmatched: { bg: '#fee2e2', color: '#dc2626', label: 'Unmatched' },
    manual: { bg: '#e0e7ff', color: '#6366f1', label: 'Manual' },
  };
  const s = styles[type] || styles.unmatched;
  return (
    <span style={{ padding: '2px 8px', borderRadius: 4, fontSize: 10, fontWeight: 600,
      backgroundColor: s.bg, color: s.color }}>{s.label}</span>
  );
}


// --- DETAIL / REVIEW VIEW (inline expand) ---
function PendingDetail({ id, onDone, onReject, onToast }) {
  const [pending, setPending] = useState(null);
  const [loading, setLoading] = useState(true);
  const [vendor, setVendor] = useState('');
  const [invoiceNum, setInvoiceNum] = useState('');
  const [invoiceDate, setInvoiceDate] = useState('');
  const [currency, setCurrency] = useState('USD');
  const [isVendorSale, setIsVendorSale] = useState(false);
  const [saveAlias, setSaveAlias] = useState(true);
  const [matchPreview, setMatchPreview] = useState(null);
  const [matchItems, setMatchItems] = useState([]);
  const [previewing, setPreviewing] = useState(false);
  const [approving, setApproving] = useState(false);

  useEffect(() => {
    let alive = true;
    setLoading(true);
    api.fetchPendingInvoice(id).then(p => {
      if (!alive) return;
      setPending(p);
      setVendor(p.vendor_canonical || p.vendor_raw || '');
      setInvoiceNum(p.invoice_number || '');
      setInvoiceDate(p.invoice_date || '');
      setCurrency(p.currency || 'USD');
      // Default save_alias on if the LLM saw an unrecognized name.
      setSaveAlias(!p.vendor_canonical && !!p.vendor_raw);
      setLoading(false);
    }).catch(err => {
      if (alive) { onToast?.('Load failed: ' + err.message, 'error'); setLoading(false); }
    });
    return () => { alive = false; };
  }, [id]);

  // Auto-run match preview once we have a vendor
  useEffect(() => {
    if (!pending || !vendor) return;
    let alive = true;
    setPreviewing(true);
    api.previewPendingMatch(id, vendor).then(res => {
      if (!alive) return;
      if (res.status === 'ok') {
        setMatchPreview(res);
        setMatchItems(res.items.map(i => ({ ...i })));
      } else {
        setMatchPreview(null); setMatchItems([]);
        onToast?.(res.message || 'Match preview failed', 'error');
      }
      setPreviewing(false);
    }).catch(err => {
      if (alive) { onToast?.('Preview failed: ' + err.message, 'error'); setPreviewing(false); }
    });
    return () => { alive = false; };
  }, [pending, vendor]);

  const updateMatch = (idx, sku, title) => {
    setMatchItems(prev => {
      const next = [...prev];
      next[idx] = { ...next[idx], matched_sku: sku, matched_title: title, match_type: sku ? 'manual' : 'unmatched' };
      return next;
    });
  };
  const clearMatch = (idx) => updateMatch(idx, '', '');

  const handleApprove = async () => {
    if (!vendor || !invoiceNum || !invoiceDate) {
      onToast?.('Vendor, invoice #, and date are required', 'error'); return;
    }
    setApproving(true);
    try {
      // Send the (possibly user-edited) extracted items so the server can
      // re-run preview + confirm. items_override lets the user add manual
      // SKU matches without going through the matcher again.
      const items = pending.items?.map((it, i) => {
        // Item-level overrides come from matchItems if present (the matcher
        // returned the same order as the LLM).
        const m = matchItems[i];
        if (!m) return it;
        return {
          ...it,
          // If user picked a manual SKU but the matcher had something else,
          // we still let the backend re-match. Items_override here is just
          // the LLM extraction; manual-pick overrides come back through
          // confirm_invoice via the regular preview output.
        };
      });
      const res = await api.approvePendingInvoice(id, {
        vendor, invoice_number: invoiceNum, invoice_date: invoiceDate, currency,
        is_vendor_sale: isVendorSale, save_alias: saveAlias,
        items: items || null,
      });
      if (res.status === 'ok') {
        onToast?.(
          'Invoice imported: ' + res.matched_count + ' lines, ' + fmtFull(res.total_cost_cad) +
          (res.alias_saved ? ' · alias saved' : ''),
          'success'
        );
        onDone?.();
      } else {
        onToast?.(res.message || 'Approve failed', 'error');
      }
    } catch (err) {
      onToast?.('Approve failed: ' + err.message, 'error');
    }
    setApproving(false);
  };

  const handleReject = async () => {
    const reason = prompt('Reason for rejection (optional):') ?? null;
    try {
      const res = await api.rejectPendingInvoice(id, reason || null);
      if (res.status === 'ok') {
        onToast?.('Invoice rejected', 'success');
        onReject?.();
      }
    } catch (err) { onToast?.('Reject failed: ' + err.message, 'error'); }
  };

  if (loading || !pending) {
    return <div style={{ padding: 16, fontSize: 12, color: 'var(--text-muted)' }}>Loading…</div>;
  }

  if (pending.status === 'extraction_failed') {
    return (
      <div style={{ padding: 16 }}>
        <div style={{ marginBottom: 12, padding: 12, borderRadius: 6, backgroundColor: '#fef2f2', color: '#dc2626', fontSize: 12, border: '1px solid #fecaca' }}>
          AI extraction failed: {pending.error_message || 'unknown error'}
        </div>
        <div style={{ fontSize: 12, color: 'var(--text-muted)', marginBottom: 12 }}>
          File: {pending.file_name}. Open it manually and use the Inventory Invoices tab to import it.
        </div>
        <button onClick={handleReject} style={{
          padding: '6px 12px', borderRadius: 6, border: '1px solid var(--border)',
          cursor: 'pointer', backgroundColor: 'transparent', color: '#ef4444', fontSize: 12,
        }}>Mark as rejected</button>
      </div>
    );
  }

  const aliasMissing = !pending.vendor_canonical && pending.vendor_raw;

  return (
    <div style={{ padding: 16, backgroundColor: 'var(--hover-bg)' }}>
      {/* Header / extraction summary */}
      {aliasMissing && (
        <div style={{ marginBottom: 12, padding: '8px 12px', borderRadius: 6, backgroundColor: '#fef9c3', color: '#854d0e', fontSize: 12, border: '1px solid #fde68a' }}>
          Vendor on this invoice is "<strong>{pending.vendor_raw}</strong>" — not in your alias table yet. Set the canonical Vendor below; the "save alias" box is on by default so the next email auto-resolves.
        </div>
      )}

      {pending.body_text && (
        <details style={{ marginBottom: 12, fontSize: 12 }}>
          <summary style={{ cursor: 'pointer', color: 'var(--text-muted)' }}>Email body ({pending.sender_email || 'unknown sender'})</summary>
          <pre style={{ marginTop: 8, padding: 8, fontFamily: 'inherit', whiteSpace: 'pre-wrap', backgroundColor: 'var(--card-bg)', border: '1px solid var(--border)', borderRadius: 6, fontSize: 11 }}>{pending.body_text}</pre>
        </details>
      )}

      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr 1fr', gap: 8, marginBottom: 12 }}>
        <div>
          <div style={{ fontSize: 10, color: 'var(--text-muted)', marginBottom: 2 }}>Vendor (canonical) *</div>
          <input value={vendor} onChange={e => setVendor(e.target.value)} placeholder="e.g. ZWO" style={inputStyle} />
        </div>
        <div>
          <div style={{ fontSize: 10, color: 'var(--text-muted)', marginBottom: 2 }}>Invoice # *</div>
          <input value={invoiceNum} onChange={e => setInvoiceNum(e.target.value)} style={inputStyle} />
        </div>
        <div>
          <div style={{ fontSize: 10, color: 'var(--text-muted)', marginBottom: 2 }}>Date *</div>
          <input type="date" value={invoiceDate} onChange={e => setInvoiceDate(e.target.value)} style={inputStyle} />
        </div>
        <div>
          <div style={{ fontSize: 10, color: 'var(--text-muted)', marginBottom: 2 }}>Currency</div>
          <select value={currency} onChange={e => setCurrency(e.target.value)} style={inputStyle}>
            <option>USD</option><option>CAD</option><option>EUR</option>
          </select>
        </div>
      </div>

      <div style={{ marginBottom: 12, display: 'flex', gap: 16, alignItems: 'center', fontSize: 12 }}>
        <label style={{ display: 'flex', alignItems: 'center', gap: 6, cursor: 'pointer' }}>
          <input type="checkbox" checked={isVendorSale} onChange={e => setIsVendorSale(e.target.checked)} />
          Vendor sale invoice
        </label>
        {pending.vendor_raw && pending.vendor_raw.toLowerCase() !== (vendor || '').toLowerCase() && (
          <label style={{ display: 'flex', alignItems: 'center', gap: 6, cursor: 'pointer' }}>
            <input type="checkbox" checked={saveAlias} onChange={e => setSaveAlias(e.target.checked)} />
            Save alias: "<strong>{pending.vendor_raw}</strong>" → "<strong>{vendor}</strong>"
          </label>
        )}
      </div>

      {/* Match preview */}
      {previewing && <div style={{ fontSize: 12, color: 'var(--text-muted)', marginBottom: 12 }}>Matching SKUs…</div>}
      {matchPreview && (
        <>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 8, marginBottom: 12 }}>
            {[
              { label: 'Exact / Saved', value: matchPreview.matched_count, color: '#22c55e' },
              { label: 'Suggested', value: matchPreview.suggested_count, color: '#d97706' },
              { label: 'Unmatched', value: matchPreview.unmatched_count, color: '#ef4444' },
              { label: 'Total', value: matchPreview.total_items, color: '#6366f1' },
            ].map((c, i) => (
              <div key={i} style={{ padding: 8, borderRadius: 6, borderLeft: '4px solid ' + c.color,
                border: '1px solid var(--border)', backgroundColor: 'var(--card-bg)' }}>
                <div style={{ fontSize: 10, color: 'var(--text-muted)' }}>{c.label}</div>
                <div style={{ fontSize: 16, fontWeight: 700 }}>{c.value}</div>
              </div>
            ))}
          </div>

          <div style={{ maxHeight: 320, overflowY: 'auto', marginBottom: 12, border: '1px solid var(--border)', borderRadius: 6 }}>
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 11 }}>
              <thead>
                <tr>
                  {['Status', 'Vendor SKU', 'Description', 'Qty', 'Cost (' + matchPreview.currency + ')', 'Matched SKU'].map(h => (
                    <th key={h} style={{ padding: '6px 8px', fontSize: 10, fontWeight: 600, color: 'var(--text-muted)',
                      textAlign: ['Qty', 'Cost (' + matchPreview.currency + ')'].includes(h) ? 'right' : 'left',
                      borderBottom: '1px solid var(--border)', position: 'sticky', top: 0,
                      backgroundColor: 'var(--card-bg)', zIndex: 1 }}>{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {matchItems.map((item, idx) => (
                  <tr key={idx} style={{ backgroundColor: idx % 2 === 0 ? 'transparent' : 'var(--row-alt)' }}>
                    <td style={{ padding: '4px 8px' }}><MatchBadge type={item.match_type} /></td>
                    <td style={{ padding: '4px 8px', fontFamily: 'monospace' }}>{item.vendor_sku}</td>
                    <td style={{ padding: '4px 8px', maxWidth: 240, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{item.description}</td>
                    <td style={{ padding: '4px 8px', textAlign: 'right' }}>{item.quantity}</td>
                    <td style={{ padding: '4px 8px', textAlign: 'right' }}>{fmtFull(item.unit_cost_foreign)}</td>
                    <td style={{ padding: '4px 8px' }}>
                      {item.matched_sku ? (
                        <div style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
                          <span style={{ fontFamily: 'monospace' }}>{item.matched_sku}</span>
                          <span style={{ fontSize: 10, color: 'var(--text-muted)', maxWidth: 140, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{item.matched_title}</span>
                          {item.match_type !== 'exact' && item.match_type !== 'mapped' && (
                            <button onClick={() => clearMatch(idx)} style={{ border: 'none', background: 'none', cursor: 'pointer', fontSize: 11, color: '#ef4444' }}>x</button>
                          )}
                        </div>
                      ) : (
                        item.suggestions?.length > 0 ? (
                          <select onChange={e => {
                            const s = item.suggestions.find(sg => sg.sku === e.target.value);
                            if (s) updateMatch(idx, s.sku, s.title);
                          }} value="" style={{ fontSize: 11, padding: '2px 4px', borderRadius: 4, border: '1px solid var(--border)', backgroundColor: 'var(--input-bg)', color: 'var(--text)' }}>
                            <option value="">-- Pick match --</option>
                            {item.suggestions.map(s => <option key={s.sku} value={s.sku}>{s.sku} — {s.title}</option>)}
                          </select>
                        ) : <span style={{ fontSize: 10, color: 'var(--text-muted)' }}>No match</span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}

      <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
        <button onClick={handleReject} style={{
          padding: '8px 14px', borderRadius: 6, border: '1px solid var(--border)',
          cursor: 'pointer', backgroundColor: 'transparent', color: '#ef4444', fontSize: 13,
        }}>Reject</button>
        <button onClick={handleApprove} disabled={approving || !vendor || !invoiceNum || !invoiceDate}
          style={{
            padding: '8px 20px', borderRadius: 6, border: 'none', cursor: 'pointer',
            backgroundColor: '#22c55e', color: '#fff', fontWeight: 600, fontSize: 13,
            opacity: (approving || !vendor || !invoiceNum || !invoiceDate) ? 0.5 : 1,
          }}>{approving ? 'Importing…' : 'Approve & Import'}</button>
      </div>
    </div>
  );
}


// --- VENDOR ALIAS PANEL ---
function VendorAliasPanel({ onToast }) {
  const [aliases, setAliases] = useState([]);
  const [newAlias, setNewAlias] = useState('');
  const [newCanonical, setNewCanonical] = useState('');
  const [loading, setLoading] = useState(true);

  const load = async () => {
    setLoading(true);
    try {
      const res = await api.fetchVendorAliases();
      setAliases(res.aliases || []);
    } catch (err) { onToast?.('Failed to load aliases: ' + err.message, 'error'); }
    setLoading(false);
  };
  useEffect(() => { load(); }, []);

  const handleAdd = async () => {
    if (!newAlias || !newCanonical) { onToast?.('Both fields are required', 'error'); return; }
    try {
      await api.createVendorAlias(newAlias, newCanonical);
      setNewAlias(''); setNewCanonical('');
      onToast?.('Alias added', 'success'); load();
    } catch (err) { onToast?.('Add failed: ' + err.message, 'error'); }
  };

  const handleDelete = async (id) => {
    if (!confirm('Delete this alias?')) return;
    try {
      await api.deleteVendorAlias(id);
      onToast?.('Alias deleted', 'success'); load();
    } catch (err) { onToast?.('Delete failed: ' + err.message, 'error'); }
  };

  return (
    <details style={{ marginBottom: 16, backgroundColor: 'var(--card-bg)', border: '1px solid var(--border)', borderRadius: 8 }}>
      <summary style={{ padding: '10px 16px', cursor: 'pointer', fontSize: 13, fontWeight: 600 }}>
        Vendor aliases ({aliases.length}) <span style={{ fontSize: 11, fontWeight: 400, color: 'var(--text-muted)' }}>— click to manage</span>
      </summary>
      <div style={{ padding: 16, borderTop: '1px solid var(--border)' }}>
        <div style={{ fontSize: 11, color: 'var(--text-muted)', marginBottom: 12 }}>
          Maps the vendor name as printed on the invoice (left) to the canonical short name TC Planner uses (right).
          Saved automatically when you check "Save alias" while approving a pending invoice.
        </div>
        <div style={{ display: 'grid', gridTemplateColumns: '2fr 1fr 80px', gap: 8, marginBottom: 12 }}>
          <input value={newAlias} onChange={e => setNewAlias(e.target.value)} placeholder='Alias e.g. "SUZHOU ZWO CO., LTD."' style={inputStyle} />
          <input value={newCanonical} onChange={e => setNewCanonical(e.target.value)} placeholder="Canonical e.g. ZWO" style={inputStyle} />
          <button onClick={handleAdd} style={{
            padding: '6px 12px', borderRadius: 6, border: 'none', cursor: 'pointer',
            backgroundColor: '#6366f1', color: '#fff', fontWeight: 600, fontSize: 12,
          }}>Add</button>
        </div>
        {loading ? (
          <div style={{ fontSize: 12, color: 'var(--text-muted)' }}>Loading…</div>
        ) : aliases.length === 0 ? (
          <div style={{ fontSize: 12, color: 'var(--text-muted)' }}>No aliases yet.</div>
        ) : (
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
            <thead>
              <tr>
                <th style={{ padding: '6px 8px', textAlign: 'left', fontSize: 11, color: 'var(--text-muted)', borderBottom: '1px solid var(--border)' }}>Alias on invoice</th>
                <th style={{ padding: '6px 8px', textAlign: 'left', fontSize: 11, color: 'var(--text-muted)', borderBottom: '1px solid var(--border)' }}>Canonical vendor</th>
                <th style={{ padding: '6px 8px', borderBottom: '1px solid var(--border)' }}></th>
              </tr>
            </thead>
            <tbody>
              {aliases.map(a => (
                <tr key={a.id}>
                  <td style={{ padding: '4px 8px' }}>{a.alias}</td>
                  <td style={{ padding: '4px 8px', fontWeight: 600 }}>{a.canonical_vendor}</td>
                  <td style={{ padding: '4px 8px', textAlign: 'right' }}>
                    <button onClick={() => handleDelete(a.id)}
                      style={{ border: 'none', background: 'none', cursor: 'pointer', fontSize: 12, color: '#ef4444' }}>x</button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </details>
  );
}


// --- MAIN PAGE ---
export default function PendingInvoicesTab({ onToast }) {
  const [pending, setPending] = useState([]);
  const [loading, setLoading] = useState(true);
  const [filter, setFilter] = useState('pending');
  const [expanded, setExpanded] = useState(null);

  const load = async () => {
    setLoading(true);
    try {
      const res = await api.fetchPendingInvoices(filter || undefined);
      setPending(res.pending || []);
    } catch (err) { onToast?.('Failed to load pending invoices: ' + err.message, 'error'); }
    setLoading(false);
  };
  useEffect(() => { load(); }, [filter]);

  const counts = pending.reduce((acc, p) => {
    acc[p.status] = (acc[p.status] || 0) + 1;
    return acc;
  }, {});

  return (
    <div style={{ padding: '0 0 24px' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
        <div>
          <h2 style={{ fontSize: 18, fontWeight: 700, margin: 0 }}>Pending Invoices</h2>
          <div style={{ fontSize: 12, color: 'var(--text-muted)', marginTop: 4 }}>
            Invoices the email ingest queued for review. Approve to import into FIFO COGS, or reject to drop.
          </div>
        </div>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center', fontSize: 12 }}>
          <span style={{ color: 'var(--text-muted)' }}>Show</span>
          <select value={filter} onChange={e => setFilter(e.target.value)} style={{
            padding: '4px 8px', borderRadius: 4, border: '1px solid var(--border)',
            backgroundColor: 'var(--input-bg)', color: 'var(--text)', fontSize: 12,
          }}>
            <option value="pending">Pending only</option>
            <option value="">All</option>
            <option value="approved">Approved</option>
            <option value="auto_imported">Auto-imported</option>
            <option value="rejected">Rejected</option>
            <option value="extraction_failed">Extraction failed</option>
          </select>
          <button onClick={load} style={{
            padding: '4px 10px', borderRadius: 4, border: '1px solid var(--border)',
            cursor: 'pointer', backgroundColor: 'transparent', color: 'var(--text)', fontSize: 12,
          }}>Refresh</button>
        </div>
      </div>

      <VendorAliasPanel onToast={onToast} />

      <div style={{ backgroundColor: 'var(--card-bg)', border: '1px solid var(--border)', borderRadius: 8, overflow: 'hidden' }}>
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
          <thead>
            <tr>
              {['Received', 'Status', 'Vendor', 'Invoice #', 'Date', 'Items', 'Total', 'Source', ''].map(h => (
                <th key={h} style={{ padding: '8px 12px', fontSize: 11, fontWeight: 600, color: 'var(--text-muted)',
                  textAlign: ['Items', 'Total'].includes(h) ? 'right' : 'left',
                  borderBottom: '2px solid var(--border)' }}>{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {loading && <tr><td colSpan={9} style={{ padding: 24, textAlign: 'center', color: 'var(--text-muted)' }}>Loading…</td></tr>}
            {!loading && pending.length === 0 && (
              <tr><td colSpan={9} style={{ padding: 24, textAlign: 'center', color: 'var(--text-muted)' }}>
                {filter === 'pending'
                  ? 'No invoices waiting for review. Forward an invoice email to your ingest address to see it here.'
                  : 'Nothing in this view.'}
              </td></tr>
            )}
            {pending.map((p, i) => (
              <Fragment key={p.id}>
                <tr style={{ cursor: 'pointer', backgroundColor: i % 2 === 0 ? 'transparent' : 'var(--row-alt)' }}
                    onClick={() => setExpanded(expanded === p.id ? null : p.id)}>
                  <td style={{ padding: '6px 12px', fontSize: 11 }}>{p.created_at?.slice(0, 16).replace('T', ' ')}</td>
                  <td style={{ padding: '6px 12px' }}><StatusBadge status={p.status} /></td>
                  <td style={{ padding: '6px 12px' }}>
                    {p.vendor_canonical || (
                      <span style={{ color: '#dc2626' }}>{p.vendor_raw || '—'}<span style={{ fontSize: 10, marginLeft: 4 }}>(no alias)</span></span>
                    )}
                  </td>
                  <td style={{ padding: '6px 12px', fontFamily: 'monospace', fontSize: 11 }}>{p.invoice_number || '—'}</td>
                  <td style={{ padding: '6px 12px' }}>{p.invoice_date || '—'}</td>
                  <td style={{ padding: '6px 12px', textAlign: 'right' }}>{p.item_count}</td>
                  <td style={{ padding: '6px 12px', textAlign: 'right', fontWeight: 600 }}>
                    {p.total_amount ? fmtFull(p.total_amount) : '—'}
                    {p.currency && p.currency !== 'CAD' && <span style={{ fontSize: 10, color: 'var(--text-muted)', marginLeft: 4 }}>{p.currency}</span>}
                  </td>
                  <td style={{ padding: '6px 12px', fontSize: 11, color: 'var(--text-muted)' }}>
                    {p.sender_email ? p.sender_email : p.source}
                  </td>
                  <td style={{ padding: '6px 12px', textAlign: 'right', fontSize: 11, color: 'var(--text-muted)' }}>
                    {expanded === p.id ? '▾' : '▸'}
                  </td>
                </tr>
                {expanded === p.id && (
                  <tr>
                    <td colSpan={9} style={{ padding: 0, borderBottom: '1px solid var(--border)' }}>
                      <PendingDetail
                        id={p.id}
                        onDone={() => { setExpanded(null); load(); }}
                        onReject={() => { setExpanded(null); load(); }}
                        onToast={onToast}
                      />
                    </td>
                  </tr>
                )}
              </Fragment>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
