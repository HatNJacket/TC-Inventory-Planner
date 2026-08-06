import { useState, useEffect, useCallback, useMemo } from 'react';
import * as api from './api';

const fmt = (n) => new Intl.NumberFormat('en-CA').format(n);
const fmtDate = (d) => { if (!d) return '—'; try { return new Date(d).toLocaleDateString('en-CA'); } catch { return d; } };
const fmtDateTime = (d) => { if (!d) return '—'; try { return new Date(d).toLocaleString('en-CA', { year: 'numeric', month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' }); } catch { return d; } };
const daysAgo = (d) => { if (!d) return null; try { return Math.floor((Date.now() - new Date(d).getTime()) / 86400000); } catch { return null; } };

const s = {
  card: { backgroundColor: 'var(--card-bg, #fff)', border: '1px solid var(--border)', borderRadius: 8, padding: '16px 20px', marginBottom: 16 },
  btn: (variant) => ({
    padding: '7px 16px', borderRadius: 6, border: 'none', cursor: 'pointer', fontWeight: 600, fontSize: 12,
    backgroundColor: variant === 'primary' ? '#6366f1' : variant === 'danger' ? '#ef4444' : variant === 'ghost' ? 'transparent' : '#e2e8f0',
    color: variant === 'primary' || variant === 'danger' ? '#fff' : 'var(--text)',
    ...(variant === 'ghost' ? { border: '1px solid var(--border)' } : {}),
  }),
  th: { padding: '8px 10px', fontSize: 11, fontWeight: 600, color: 'var(--text-muted)', textAlign: 'left', borderBottom: '2px solid var(--border)', position: 'sticky', top: 0, backgroundColor: 'var(--card-bg, #fff)', zIndex: 1 },
  td: { padding: '7px 10px', fontSize: 12, borderBottom: '1px solid var(--border)' },
};

export default function WaitersPage({ onToast }) {
  const [tab, setTab] = useState('current'); // 'current' | 'upload' | 'snapshots'
  const [current, setCurrent] = useState([]);
  const [snapshots, setSnapshots] = useState([]);
  const [loading, setLoading] = useState(false);
  const [searchTerm, setSearchTerm] = useState('');
  const [vendorFilter, setVendorFilter] = useState('');
  const [showOosOnly, setShowOosOnly] = useState(false);

  const loadCurrent = useCallback(async () => {
    setLoading(true);
    try {
      const data = await api.getCurrentWaiters();
      setCurrent(data.waiters || []);
    } catch (e) { onToast?.('Failed to load waiters: ' + e.message, 'error'); }
    setLoading(false);
  }, [onToast]);

  const loadSnapshots = useCallback(async () => {
    try {
      const data = await api.listWaiterSnapshots();
      setSnapshots(data.snapshots || []);
    } catch (e) { onToast?.('Failed to load snapshots: ' + e.message, 'error'); }
  }, [onToast]);

  // Always know whether any snapshot exists — the "no reports uploaded" empty
  // state on the Current tab depends on snapshotCount, so we can't wait for
  // the user to visit the History tab to populate it.
  useEffect(() => { loadSnapshots(); }, [loadSnapshots]);

  useEffect(() => {
    if (tab === 'current') loadCurrent();
  }, [tab, loadCurrent]);

  const vendors = useMemo(() =>
    [...new Set(current.map(c => c.vendor).filter(Boolean))].sort(),
    [current]);

  const filtered = useMemo(() => {
    let items = current;
    if (searchTerm) {
      const t = searchTerm.toLowerCase();
      items = items.filter(c =>
        (c.sku || '').toLowerCase().includes(t) ||
        (c.product_title || '').toLowerCase().includes(t) ||
        (c.vendor || '').toLowerCase().includes(t));
    }
    if (vendorFilter) items = items.filter(c => c.vendor === vendorFilter);
    if (showOosOnly) items = items.filter(c => (c.current_stock ?? 0) <= 0);
    return items;
  }, [current, searchTerm, vendorFilter, showOosOnly]);

  const totalWaiters = useMemo(() => filtered.reduce((s, c) => s + (c.waiter_count || 0), 0), [filtered]);
  const totalBoost = useMemo(() => filtered.reduce((s, c) => s + (c.demand_boost || 0), 0), [filtered]);
  const unmatchedCount = useMemo(() => filtered.filter(c => !c.matched).length, [filtered]);

  return (
    <div style={{ padding: '24px 28px', overflow: 'auto', height: '100vh' }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 20 }}>
        <div>
          <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>Back-in-Stock Waiters</h1>
          <div style={{ fontSize: 12, color: 'var(--text-muted)', marginTop: 4 }}>
            Customers waiting for out-of-stock items (from the Now In Stock Shopify app). Uses latest snapshot.
          </div>
        </div>
      </div>

      {/* Tabs */}
      <div style={{ display: 'flex', gap: 2, backgroundColor: '#f0f1f3', borderRadius: 6, padding: 2, marginBottom: 16, width: 'fit-content' }}>
        {[
          ['current', `Current Waiters${current.length ? ` (${current.length})` : ''}`],
          ['upload', 'Upload Report'],
          ['snapshots', `Upload History${snapshots.length ? ` (${snapshots.length})` : ''}`],
        ].map(([v, l]) => (
          <button key={v} onClick={() => setTab(v)}
            style={{ padding: '6px 16px', borderRadius: 4, border: 'none', fontSize: 12, fontWeight: 600, cursor: 'pointer',
              backgroundColor: tab === v ? '#fff' : 'transparent',
              color: tab === v ? '#6366f1' : 'var(--text-muted)',
              boxShadow: tab === v ? '0 1px 3px rgba(0,0,0,0.1)' : 'none' }}>{l}</button>
        ))}
      </div>

      {tab === 'current' && (
        <CurrentWaitersView
          filtered={filtered} loading={loading}
          searchTerm={searchTerm} setSearchTerm={setSearchTerm}
          vendorFilter={vendorFilter} setVendorFilter={setVendorFilter}
          showOosOnly={showOosOnly} setShowOosOnly={setShowOosOnly}
          vendors={vendors}
          totalWaiters={totalWaiters} totalBoost={totalBoost} unmatchedCount={unmatchedCount}
          snapshotCount={snapshots.length}
        />
      )}

      {tab === 'upload' && <UploadView onToast={onToast} onDone={() => { loadCurrent(); loadSnapshots(); setTab('current'); }} />}

      {tab === 'snapshots' && <SnapshotsView snapshots={snapshots} onToast={onToast} onChange={loadSnapshots} />}
    </div>
  );
}

function CurrentWaitersView({ filtered, loading, searchTerm, setSearchTerm, vendorFilter, setVendorFilter,
                              showOosOnly, setShowOosOnly, vendors, totalWaiters, totalBoost, unmatchedCount,
                              snapshotCount }) {
  if (!loading && snapshotCount === 0) {
    return (
      <div style={s.card}>
        <div style={{ padding: 40, textAlign: 'center' }}>
          <div style={{ fontSize: 40, marginBottom: 8 }}>📋</div>
          <div style={{ fontSize: 14, fontWeight: 600, marginBottom: 4 }}>No waiter reports uploaded yet</div>
          <div style={{ fontSize: 12, color: 'var(--text-muted)' }}>
            Upload your first iStock report to see customers waiting for out-of-stock items.
          </div>
        </div>
      </div>
    );
  }

  return (
    <>
      {/* Summary cards */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 12, marginBottom: 16 }}>
        <div style={s.card}>
          <div style={{ fontSize: 11, color: 'var(--text-muted)', fontWeight: 600 }}>SKUs with Waiters</div>
          <div style={{ fontSize: 22, fontWeight: 700, marginTop: 4 }}>{filtered.length}</div>
        </div>
        <div style={s.card}>
          <div style={{ fontSize: 11, color: 'var(--text-muted)', fontWeight: 600 }}>Total Waiters</div>
          <div style={{ fontSize: 22, fontWeight: 700, marginTop: 4 }}>{totalWaiters}</div>
        </div>
        <div style={s.card}>
          <div style={{ fontSize: 11, color: 'var(--text-muted)', fontWeight: 600 }}>Demand Boost (60%)</div>
          <div style={{ fontSize: 22, fontWeight: 700, marginTop: 4, color: '#6366f1' }}>{totalBoost}</div>
          <div style={{ fontSize: 10, color: 'var(--text-muted)', marginTop: 2 }}>extra units to order</div>
        </div>
        <div style={s.card}>
          <div style={{ fontSize: 11, color: 'var(--text-muted)', fontWeight: 600 }}>Unmatched SKUs</div>
          <div style={{ fontSize: 22, fontWeight: 700, marginTop: 4, color: unmatchedCount > 0 ? '#ef4444' : 'var(--text)' }}>{unmatchedCount}</div>
          <div style={{ fontSize: 10, color: 'var(--text-muted)', marginTop: 2 }}>not in inventory</div>
        </div>
      </div>

      {/* Filters */}
      <div style={s.card}>
        <div style={{ display: 'flex', gap: 10, alignItems: 'center', marginBottom: 10 }}>
          <input placeholder="🔍 Search SKU, product, or vendor..." value={searchTerm}
            onChange={e => setSearchTerm(e.target.value)}
            style={{ flex: 1, padding: '7px 12px', borderRadius: 6, border: '1px solid var(--border)', fontSize: 13, maxWidth: 400 }} />
          <select value={vendorFilter} onChange={e => setVendorFilter(e.target.value)}
            style={{ padding: '7px 12px', borderRadius: 6, border: '1px solid var(--border)', fontSize: 13 }}>
            <option value="">All Vendors</option>
            {vendors.map(v => <option key={v} value={v}>{v}</option>)}
          </select>
          <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 13, cursor: 'pointer' }}>
            <input type="checkbox" checked={showOosOnly} onChange={e => setShowOosOnly(e.target.checked)}
              style={{ accentColor: '#ef4444' }} />
            Out of stock only
          </label>
        </div>

        {loading ? (
          <div style={{ padding: 30, textAlign: 'center', fontSize: 13, color: 'var(--text-muted)' }}>Loading...</div>
        ) : filtered.length === 0 ? (
          <div style={{ padding: 30, textAlign: 'center', fontSize: 13, color: 'var(--text-muted)' }}>
            No waiters match your filter.
          </div>
        ) : (
          <div style={{ maxHeight: 600, overflow: 'auto', border: '1px solid var(--border)', borderRadius: 6 }}>
            <table style={{ width: '100%', borderCollapse: 'collapse' }}>
              <thead>
                <tr>
                  <th style={s.th}>SKU</th>
                  <th style={s.th}>Product</th>
                  <th style={s.th}>Vendor</th>
                  <th style={{ ...s.th, textAlign: 'right' }}>Stock</th>
                  <th style={{ ...s.th, textAlign: 'right' }}>Waiters</th>
                  <th style={{ ...s.th, textAlign: 'right' }} title="60% of waiters, rounded up">Demand +</th>
                  <th style={s.th}>Oldest</th>
                  <th style={s.th}>Newest</th>
                </tr>
              </thead>
              <tbody>
                {filtered.map((w, i) => {
                  const isOos = (w.current_stock ?? 0) <= 0;
                  const oldestDays = daysAgo(w.oldest_request);
                  return (
                    <tr key={i} style={{ backgroundColor: i % 2 === 0 ? 'transparent' : 'var(--row-alt, #fafbfc)' }}>
                      <td style={{ ...s.td, fontFamily: 'monospace', fontSize: 11, fontWeight: 500 }}>
                        {w.sku}
                        {!w.matched && <span title="Not in current inventory" style={{ color: '#ef4444', marginLeft: 4, fontSize: 10 }}>⚠</span>}
                      </td>
                      <td style={{ ...s.td, maxWidth: 400, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} title={w.product_title || ''}>
                        {w.product_title || <span style={{ color: 'var(--text-muted)', fontStyle: 'italic' }}>(not in inventory)</span>}
                      </td>
                      <td style={s.td}>{w.vendor || '—'}</td>
                      <td style={{ ...s.td, textAlign: 'right', color: isOos ? '#ef4444' : 'var(--text)', fontWeight: isOos ? 600 : 400 }}>
                        {w.current_stock ?? '—'}
                      </td>
                      <td style={{ ...s.td, textAlign: 'right', fontWeight: 700 }}>
                        <span style={{ padding: '2px 8px', borderRadius: 10, backgroundColor: '#eef2ff', color: '#6366f1' }}>
                          {w.waiter_count}
                        </span>
                      </td>
                      <td style={{ ...s.td, textAlign: 'right', fontWeight: 600, color: '#065f46' }}>
                        +{w.demand_boost}
                      </td>
                      <td style={{ ...s.td, fontSize: 11, color: 'var(--text-muted)' }}>
                        {fmtDate(w.oldest_request)} {oldestDays != null && <span style={{ fontSize: 10 }}>({oldestDays}d ago)</span>}
                      </td>
                      <td style={{ ...s.td, fontSize: 11, color: 'var(--text-muted)' }}>{fmtDate(w.newest_request)}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </>
  );
}

function UploadView({ onToast, onDone }) {
  const [file, setFile] = useState(null);
  const [snapshotDate, setSnapshotDate] = useState(new Date().toISOString().slice(0, 10));
  const [notes, setNotes] = useState('');
  const [loading, setLoading] = useState(false);
  const [preview, setPreview] = useState(null);
  const [importing, setImporting] = useState(false);

  const handlePreview = async () => {
    if (!file) return;
    setLoading(true);
    setPreview(null);
    try {
      const data = await api.previewWaiters(file, snapshotDate);
      setPreview(data);
    } catch (e) { onToast?.('Preview failed: ' + e.message, 'error'); }
    setLoading(false);
  };

  const handleImport = async () => {
    if (!file || !preview) return;
    setImporting(true);
    try {
      const result = await api.importWaiters(file, snapshotDate, notes || null);
      const cleared = result.cleared_prior_requests || 0;
      const summary = `Imported: ${result.total_requests} requests across ${result.unique_skus} SKUs`
        + (cleared > 0 ? ` (replaced ${cleared} prior waiter${cleared === 1 ? '' : 's'})` : '');
      onToast?.(summary, 'success');
      onDone?.();
    } catch (e) { onToast?.('Import failed: ' + e.message, 'error'); }
    setImporting(false);
  };

  return (
    <div style={s.card}>
      <div style={{ fontSize: 14, fontWeight: 700, marginBottom: 12 }}>Upload iStock Report</div>
      <div style={{ fontSize: 12, color: 'var(--text-muted)', marginBottom: 16 }}>
        Export the individual customer report (not the summary) from the Now In Stock app. It should have columns: <strong>Product Info, SKU, Customer Email, Date Added</strong>. CSV or XLSX accepted.
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: '1fr 180px', gap: 12, marginBottom: 12 }}>
        <div>
          <label style={{ fontSize: 11, fontWeight: 600, color: 'var(--text-muted)', display: 'block', marginBottom: 3 }}>File *</label>
          <input type="file" accept=".csv,.xlsx,.xls,.txt" onChange={e => { setFile(e.target.files?.[0] || null); setPreview(null); }}
            style={{ fontSize: 13 }} />
        </div>
        <div>
          <label style={{ fontSize: 11, fontWeight: 600, color: 'var(--text-muted)', display: 'block', marginBottom: 3 }}>Snapshot Date</label>
          <input type="date" value={snapshotDate} onChange={e => setSnapshotDate(e.target.value)}
            style={{ padding: '6px 10px', borderRadius: 6, border: '1px solid var(--border)', fontSize: 13, width: '100%', boxSizing: 'border-box' }} />
        </div>
      </div>

      <div style={{ marginBottom: 12 }}>
        <label style={{ fontSize: 11, fontWeight: 600, color: 'var(--text-muted)', display: 'block', marginBottom: 3 }}>Notes (optional)</label>
        <input value={notes} onChange={e => setNotes(e.target.value)} placeholder="e.g. April month-end export"
          style={{ padding: '6px 10px', borderRadius: 6, border: '1px solid var(--border)', fontSize: 13, width: '100%', boxSizing: 'border-box' }} />
      </div>

      <div style={{ display: 'flex', gap: 8 }}>
        <button onClick={handlePreview} disabled={!file || loading} style={s.btn('primary')}>
          {loading ? 'Parsing...' : 'Preview'}
        </button>
        {preview && (
          <button onClick={handleImport} disabled={importing} style={{ ...s.btn('primary'), backgroundColor: '#16a34a' }}>
            {importing ? 'Importing...' : 'Confirm & Import'}
          </button>
        )}
      </div>

      {preview && (
        <div style={{ marginTop: 20, padding: 16, backgroundColor: '#f8fafc', borderRadius: 6 }}>
          <div style={{ fontSize: 13, fontWeight: 700, marginBottom: 10 }}>Preview</div>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(5, 1fr)', gap: 10, marginBottom: 16 }}>
            <PreviewStat label="Rows parsed" value={preview.total_rows_parsed} />
            <PreviewStat label="Dupes removed" value={preview.duplicates_removed} color={preview.duplicates_removed > 0 ? '#f59e0b' : null} />
            <PreviewStat label="After dedupe" value={preview.total_requests_after_dedupe} color="#065f46" />
            <PreviewStat label="Matched SKUs" value={`${preview.matched_skus} / ${preview.unique_skus}`} color="#065f46" />
            <PreviewStat label="Unmatched" value={preview.unmatched_skus} color={preview.unmatched_skus > 0 ? '#ef4444' : null} />
          </div>
          <div style={{ maxHeight: 340, overflow: 'auto', border: '1px solid var(--border)', borderRadius: 4 }}>
            <table style={{ width: '100%', borderCollapse: 'collapse' }}>
              <thead>
                <tr>
                  <th style={s.th}>Report SKU</th>
                  <th style={s.th}>→ TC SKU</th>
                  <th style={s.th}>Product</th>
                  <th style={{ ...s.th, textAlign: 'right' }}>Count</th>
                  <th style={{ ...s.th, textAlign: 'right' }}>Demand +</th>
                  <th style={s.th}>Oldest</th>
                </tr>
              </thead>
              <tbody>
                {preview.per_sku.map((r, i) => (
                  <tr key={i} style={{ backgroundColor: r.matched ? 'transparent' : '#fef5f5' }}>
                    <td style={{ ...s.td, fontFamily: 'monospace', fontSize: 11 }}>{r.report_sku}</td>
                    <td style={{ ...s.td, fontFamily: 'monospace', fontSize: 11, color: r.matched ? '#6366f1' : '#ef4444' }}>
                      {r.shopify_sku || '⚠ Unmatched'}
                    </td>
                    <td style={{ ...s.td, maxWidth: 300, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} title={r.product_title || ''}>{r.product_title || '—'}</td>
                    <td style={{ ...s.td, textAlign: 'right', fontWeight: 600 }}>{r.count}</td>
                    <td style={{ ...s.td, textAlign: 'right', color: '#065f46', fontWeight: 600 }}>+{r.demand_boost}</td>
                    <td style={{ ...s.td, fontSize: 11, color: 'var(--text-muted)' }}>{fmtDate(r.oldest_request)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {preview.unmatched_skus > 0 && (
            <div style={{ marginTop: 10, padding: 8, backgroundColor: '#fef3e2', borderLeft: '3px solid #f59e0b', fontSize: 11, color: '#92400e' }}>
              {preview.unmatched_skus} SKU{preview.unmatched_skus === 1 ? '' : 's'} not found in current inventory — these will be imported but won't influence ordering until the SKUs match.
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function PreviewStat({ label, value, color }) {
  return (
    <div>
      <div style={{ fontSize: 10, color: 'var(--text-muted)', fontWeight: 600, textTransform: 'uppercase' }}>{label}</div>
      <div style={{ fontSize: 18, fontWeight: 700, marginTop: 2, color: color || 'var(--text)' }}>{value}</div>
    </div>
  );
}

function SnapshotsView({ snapshots, onToast, onChange }) {
  const handleDelete = async (snap) => {
    if (!window.confirm(`Delete snapshot from ${snap.snapshot_date}? This will remove ${snap.total_requests} waiter records.`)) return;
    try {
      await api.deleteWaiterSnapshot(snap.id);
      onToast?.('Snapshot deleted', 'success');
      onChange?.();
    } catch (e) { onToast?.('Delete failed: ' + e.message, 'error'); }
  };

  return (
    <div style={s.card}>
      <div style={{ fontSize: 14, fontWeight: 700, marginBottom: 4 }}>Upload History</div>
      <div style={{ fontSize: 11, color: 'var(--text-muted)', marginBottom: 12 }}>
        Each upload is stored as a snapshot. Only the most recent snapshot is used for current waiter counts — older ones are kept for history.
      </div>

      {snapshots.length === 0 ? (
        <div style={{ padding: 30, textAlign: 'center', fontSize: 13, color: 'var(--text-muted)' }}>No snapshots yet.</div>
      ) : (
        <table style={{ width: '100%', borderCollapse: 'collapse' }}>
          <thead>
            <tr>
              <th style={s.th}>Snapshot Date</th>
              <th style={s.th}>Filename</th>
              <th style={{ ...s.th, textAlign: 'right' }}>Requests</th>
              <th style={{ ...s.th, textAlign: 'right' }}>SKUs</th>
              <th style={s.th}>Imported</th>
              <th style={s.th}>Notes</th>
              <th style={{ ...s.th, width: 80 }}></th>
            </tr>
          </thead>
          <tbody>
            {snapshots.map((snap, i) => (
              <tr key={snap.id} style={{ backgroundColor: i === 0 ? '#f0f9ff' : (i % 2 === 0 ? 'transparent' : 'var(--row-alt, #fafbfc)') }}>
                <td style={{ ...s.td, fontWeight: 600 }}>
                  {fmtDate(snap.snapshot_date)}
                  {i === 0 && <span style={{ marginLeft: 6, fontSize: 9, padding: '1px 5px', backgroundColor: '#6366f1', color: '#fff', borderRadius: 3, fontWeight: 700 }}>LATEST</span>}
                </td>
                <td style={{ ...s.td, fontSize: 11, color: 'var(--text-muted)' }}>{snap.source_filename || '—'}</td>
                <td style={{ ...s.td, textAlign: 'right' }}>{fmt(snap.total_requests)}</td>
                <td style={{ ...s.td, textAlign: 'right' }}>{fmt(snap.unique_skus)}</td>
                <td style={{ ...s.td, fontSize: 11, color: 'var(--text-muted)' }}>{fmtDateTime(snap.imported_at)}</td>
                <td style={{ ...s.td, fontSize: 11, color: 'var(--text-muted)', maxWidth: 200, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{snap.notes || ''}</td>
                <td style={{ ...s.td, textAlign: 'center' }}>
                  <button onClick={() => handleDelete(snap)} title="Delete snapshot"
                    style={{ border: 'none', background: 'none', cursor: 'pointer', fontSize: 14, color: '#ef4444' }}>🗑</button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
