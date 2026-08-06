/**
 * SalesManagerPage — Manage vendor sales pricing.
 * Create sales, upload sale pricelists, preview/approve changes,
 * activate/revert sales, and schedule automatic start/end dates.
 */
import { useState, useEffect, useCallback } from 'react';
import * as api from './api';

const fmt = (n) => new Intl.NumberFormat('en-CA', { style: 'currency', currency: 'CAD', minimumFractionDigits: 2 }).format(n || 0);
const fmtPct = (n) => n != null ? `${n.toFixed(1)}%` : '—';
const fmtDate = (d) => d ? new Date(d).toLocaleString('en-CA', { dateStyle: 'medium', timeStyle: 'short' }) : '—';

const STATUS_COLORS = {
  pending: { bg: '#1e3a5f', color: '#93c5fd', label: '⏳ Pending' },
  active: { bg: '#065f46', color: '#6ee7b7', label: '🟢 Active' },
  completed: { bg: '#374151', color: '#9ca3af', label: '✅ Completed' },
  cancelled: { bg: '#4a2020', color: '#fca5a5', label: '❌ Cancelled' },
};

const s = {
  page: { padding: 24, maxWidth: 1600 },
  card: { border: '1px solid var(--border)', borderRadius: 8, padding: 16, marginBottom: 16, background: 'var(--card-bg, #1a1f2e)' },
  h1: { fontSize: 20, fontWeight: 700, margin: 0 },
  h2: { fontSize: 15, fontWeight: 600, margin: '0 0 12px', color: 'var(--text)' },
  subtitle: { fontSize: 12, color: 'var(--text-muted)', marginTop: 4 },
  btn: (v) => ({
    padding: '6px 14px', borderRadius: 5, border: 'none', cursor: 'pointer', fontSize: 12, fontWeight: 500,
    background: v === 'primary' ? '#3b82f6' : v === 'success' ? '#059669' : v === 'danger' ? '#dc2626' : v === 'warning' ? '#d97706' : v === 'purple' ? '#7c3aed' : '#4b5563',
    color: '#fff',
  }),
  input: { padding: '6px 10px', fontSize: 13, borderRadius: 5, border: '1px solid var(--border)', background: 'var(--input-bg)', color: 'var(--text)', width: '100%' },
  select: { padding: '6px 10px', fontSize: 13, borderRadius: 5, border: '1px solid var(--border)', background: 'var(--input-bg)', color: 'var(--text)' },
  label: { fontSize: 11, color: 'var(--text-muted)', marginBottom: 2, display: 'block' },
  badge: (status) => ({
    display: 'inline-block', fontSize: 11, padding: '2px 8px', borderRadius: 10, fontWeight: 500,
    background: (STATUS_COLORS[status] || STATUS_COLORS.pending).bg,
    color: (STATUS_COLORS[status] || STATUS_COLORS.pending).color,
  }),
  table: { width: '100%', borderCollapse: 'collapse', fontSize: 12 },
  th: { padding: '8px 10px', fontSize: 11, fontWeight: 600, color: 'var(--text-muted)', textAlign: 'left', borderBottom: '2px solid var(--border)', whiteSpace: 'nowrap' },
  td: { padding: '6px 10px', borderBottom: '1px solid var(--border)' },
  row: (i) => ({ backgroundColor: i % 2 === 0 ? 'transparent' : 'var(--row-alt, rgba(255,255,255,0.02))' }),
  statRow: { display: 'flex', gap: 16, flexWrap: 'wrap', marginBottom: 16 },
  stat: { background: 'var(--card-bg, #1a1f2e)', border: '1px solid var(--border)', borderRadius: 8, padding: '12px 16px', minWidth: 120, textAlign: 'center' },
  statVal: { fontSize: 22, fontWeight: 700 },
  statLabel: { fontSize: 10, color: 'var(--text-muted)', marginTop: 2, textTransform: 'uppercase', letterSpacing: '0.5px' },
};


// ─── SALE DASHBOARD ─────────────────────────────────────────────

function SaleDashboard({ sales, onSelect, onRefresh }) {
  const active = sales.filter(s => s.status === 'active');
  const pending = sales.filter(s => s.status === 'pending');
  const completed = sales.filter(s => s.status === 'completed' || s.status === 'cancelled');

  return (
    <div>
      <div style={s.statRow}>
        <div style={s.stat}>
          <div style={{ ...s.statVal, color: '#6ee7b7' }}>{active.length}</div>
          <div style={s.statLabel}>Active Sales</div>
        </div>
        <div style={s.stat}>
          <div style={{ ...s.statVal, color: '#93c5fd' }}>{pending.length}</div>
          <div style={s.statLabel}>Pending</div>
        </div>
        <div style={s.stat}>
          <div style={{ ...s.statVal, color: '#9ca3af' }}>{completed.length}</div>
          <div style={s.statLabel}>Completed</div>
        </div>
        <div style={s.stat}>
          <div style={s.statVal}>{sales.reduce((sum, s) => sum + (s.item_count || 0), 0)}</div>
          <div style={s.statLabel}>Total SKUs</div>
        </div>
      </div>

      <div style={{ overflowX: 'auto' }}>
        <table style={s.table}>
          <thead>
            <tr>
              {['Status', 'Sale Name', 'Vendor', 'Items', 'Avg Discount', 'Schedule', 'Created', 'Actions'].map(h => (
                <th key={h} style={s.th}>{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {sales.length === 0 && (
              <tr><td colSpan={8} style={{ ...s.td, textAlign: 'center', color: 'var(--text-muted)', padding: 30 }}>
                No sales yet. Create your first sale below.
              </td></tr>
            )}
            {sales.map((sale, i) => (
              <tr key={sale.id} style={s.row(i)}>
                <td style={s.td}><span style={s.badge(sale.status)}>{(STATUS_COLORS[sale.status] || STATUS_COLORS.pending).label}</span></td>
                <td style={{ ...s.td, fontWeight: 600, cursor: 'pointer', color: '#60a5fa' }} onClick={() => onSelect(sale)}>{sale.name}</td>
                <td style={s.td}>{sale.vendor}</td>
                <td style={{ ...s.td, textAlign: 'right' }}>{sale.item_count || 0} ({sale.active_items || 0} active)</td>
                <td style={{ ...s.td, textAlign: 'right' }}>{fmtPct(sale.avg_discount_pct)}</td>
                <td style={{ ...s.td, fontSize: 11 }}>
                  {sale.start_at || sale.end_at ? (
                    <>{fmtDate(sale.start_at)} → {fmtDate(sale.end_at)}</>
                  ) : <span style={{ color: 'var(--text-muted)' }}>Manual</span>}
                </td>
                <td style={{ ...s.td, fontSize: 11 }}>{fmtDate(sale.created_at)}</td>
                <td style={s.td}>
                  <button style={s.btn()} onClick={() => onSelect(sale)}>View</button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}


// ─── CREATE SALE FORM ───────────────────────────────────────────

function CreateSaleForm({ vendors, onCreated, onToast }) {
  const [form, setForm] = useState({
    vendor: '', name: '', currency: 'USD',
    start_date: '', start_time: '00:00',
    end_date: '', end_time: '00:00',
    notes: '', collection_name: '',
  });
  const [creating, setCreating] = useState(false);

  const handleCreate = async () => {
    if (!form.vendor || !form.name) { onToast?.('Vendor and sale name are required', 'error'); return; }
    setCreating(true);
    try {
      // Get current FX rate
      let fxRate = 1.0;
      if (form.currency !== 'CAD') {
        try {
          const fxData = await api.fetchFxRate(form.currency + 'CAD');
          fxRate = fxData?.effective_rate || fxData?.rate || 1.0;
        } catch { /* use 1.0 */ }
      }
      // Combine date + time into datetime strings
      const start_at = form.start_date ? `${form.start_date}T${form.start_time || '00:00'}` : '';
      const end_at = form.end_date ? `${form.end_date}T${form.end_time || '00:00'}` : '';
      const result = await api.createSale({
        ...form, start_at, end_at, fx_rate: fxRate,
        collection_name: form.collection_name || null,
      });
      onToast?.('Sale created', 'success');
      onCreated(result.id);
    } catch (e) {
      onToast?.(e.message, 'error');
    }
    setCreating(false);
  };

  return (
    <div style={s.card}>
      <div style={s.h2}>Create New Sale</div>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(200px, 1fr))', gap: 12 }}>
        <div>
          <label style={s.label}>Vendor *</label>
          <select style={s.select} value={form.vendor} onChange={e => setForm(f => ({ ...f, vendor: e.target.value }))}>
            <option value="">Select vendor...</option>
            {vendors.map(v => <option key={v} value={v}>{v}</option>)}
          </select>
        </div>
        <div>
          <label style={s.label}>Sale Name *</label>
          <input style={s.input} value={form.name} onChange={e => setForm(f => ({ ...f, name: e.target.value }))}
                 placeholder="e.g. iOptron Spring Sale 2026" />
        </div>
        <div>
          <label style={s.label}>Price Currency</label>
          <select style={s.select} value={form.currency} onChange={e => setForm(f => ({ ...f, currency: e.target.value }))}>
            <option value="CAD">CAD</option><option value="USD">USD</option><option value="EUR">EUR</option>
          </select>
        </div>
        <div>
          <label style={s.label}>Start Date (optional)</label>
          <input type="date" style={s.input} value={form.start_date} onChange={e => setForm(f => ({ ...f, start_date: e.target.value }))} />
        </div>
        <div>
          <label style={s.label}>Start Time (EST)</label>
          <input type="time" style={s.input} value={form.start_time} onChange={e => setForm(f => ({ ...f, start_time: e.target.value }))} />
        </div>
        <div>
          <label style={s.label}>End Date (optional)</label>
          <input type="date" style={s.input} value={form.end_date} onChange={e => setForm(f => ({ ...f, end_date: e.target.value }))} />
        </div>
        <div>
          <label style={s.label}>End Time (EST)</label>
          <input type="time" style={s.input} value={form.end_time} onChange={e => setForm(f => ({ ...f, end_time: e.target.value }))} />
        </div>
        <div>
          <label style={s.label}>Notes</label>
          <input style={s.input} value={form.notes} onChange={e => setForm(f => ({ ...f, notes: e.target.value }))} placeholder="Internal notes..." />
        </div>
        <div>
          <label style={s.label}>Shopify Collection (optional)</label>
          <input style={s.input} value={form.collection_name}
                 onChange={e => setForm(f => ({ ...f, collection_name: e.target.value }))}
                 placeholder="e.g. iOptron Spring Sale" />
          <div style={{ fontSize: 10, color: 'var(--text-muted)', marginTop: 2 }}>Creates a manual collection and adds sale products to it on activation</div>
        </div>
      </div>
      <div style={{ marginTop: 12 }}>
        <button style={s.btn('primary')} onClick={handleCreate} disabled={creating}>
          {creating ? 'Creating...' : '+ Create Sale'}
        </button>
      </div>
    </div>
  );
}


// ─── SALE DETAIL VIEW ───────────────────────────────────────────

function SaleDetailView({ saleId, onBack, onToast }) {
  const [sale, setSale] = useState(null);
  const [loading, setLoading] = useState(true);
  const [uploading, setUploading] = useState(false);
  const [preview, setPreview] = useState(null);
  const [confirming, setConfirming] = useState(false);
  const [activating, setActivating] = useState(false);
  const [reverting, setReverting] = useState(false);

  // Upload form
  const [file, setFile] = useState(null);
  const [fileHeaders, setFileHeaders] = useState([]);
  const [skuCol, setSkuCol] = useState('');
  const [priceCol, setPriceCol] = useState('');
  const [priceCurrency, setPriceCurrency] = useState('');
  const [dealerCostCol, setDealerCostCol] = useState('');
  const [dealerCostCurrency, setDealerCostCurrency] = useState('');

  // Parse file headers when a file is selected
  const handleFileSelect = (selectedFile) => {
    setFile(selectedFile);
    setFileHeaders([]);
    setSkuCol(''); setPriceCol(''); setDealerCostCol('');
    if (!selectedFile) return;

    const fname = selectedFile.name.toLowerCase();
    const isXlsx = fname.endsWith('.xlsx') || fname.endsWith('.xls');

    if (isXlsx) {
      // For XLSX: read as ArrayBuffer and send to server for header detection
      const headerReader = new FileReader();
      headerReader.onload = async (ev) => {
        try {
          const token = localStorage.getItem('tc_planner_token') || '';
          const formData = new FormData();
          formData.append('file', selectedFile);
          const resp = await fetch(
            (window.TC_API_BASE || '/api') + '/sales/detect-headers',
            { method: 'POST', headers: { Authorization: 'Bearer ' + token }, body: formData }
          );
          if (resp.ok) {
            const data = await resp.json();
            if (data.headers?.length) {
              _applyHeaders(data.headers);
            }
          }
        } catch { /* fall back to text inputs */ }
      };
      headerReader.readAsArrayBuffer(selectedFile.slice(0, 65536));
      return;
    }

    // For CSV/TSV: read first chunk as text
    const reader = new FileReader();
    reader.onload = (e) => {
      try {
        const text = e.target.result;
        const firstLine = text.split(/[\r\n]+/)[0];
        let headers;
        if (fname.endsWith('.tsv')) {
          headers = firstLine.split('\t').map(h => h.trim()).filter(Boolean);
        } else {
          headers = firstLine.split(',').map(h => h.trim().replace(/^["']|["']$/g, '')).filter(Boolean);
        }
        _applyHeaders(headers);
      } catch { /* ignore */ }
    };
    const blob = selectedFile.slice(0, 4096);
    reader.readAsText(blob);
  };

  const _applyHeaders = (headers) => {
    setFileHeaders(headers);
    const lowerHeaders = headers.map(h => h.toLowerCase());
    const skuIdx = lowerHeaders.findIndex(h => h === 'sku' || h === 'item number' || h === 'item_number' || h === 'product_code');
    if (skuIdx >= 0) setSkuCol(headers[skuIdx]);
    const priceIdx = lowerHeaders.findIndex(h => h.includes('sale') && h.includes('price') || h === 'map' || h === 'msrp');
    if (priceIdx >= 0) setPriceCol(headers[priceIdx]);
    const costIdx = lowerHeaders.findIndex(h => h.includes('dealer') || h.includes('wholesale') || (h.includes('cost') && !h.includes('msrp')));
    if (costIdx >= 0) setDealerCostCol(headers[costIdx]);
  };

  const loadSale = useCallback(async () => {
    setLoading(true);
    try {
      const data = await api.getSale(saleId);
      setSale(data);
    } catch (e) {
      onToast?.(e.message, 'error');
    }
    setLoading(false);
  }, [saleId]);

  useEffect(() => { loadSale(); }, [loadSale]);

  const handleUpload = async () => {
    if (!file || !skuCol || !priceCol) {
      onToast?.('Please select a file and map SKU and Price columns', 'error');
      return;
    }
    setUploading(true);
    try {
      const effectivePriceCurrency = priceCurrency || sale.currency || 'USD';
      const effectiveDealerCurrency = dealerCostCurrency || effectivePriceCurrency;

      // Get FX rate for sale price currency
      let priceFxRate = 1.0;
      if (effectivePriceCurrency !== 'CAD') {
        try {
          const fxData = await api.fetchFxRate(effectivePriceCurrency + 'CAD');
          priceFxRate = fxData?.effective_rate || fxData?.rate || 1.0;
        } catch { /* use 1.0 */ }
      }

      // Get FX rate for dealer cost currency (may be different)
      let dealerFxRate = null;
      if (dealerCostCol && effectiveDealerCurrency !== 'CAD') {
        try {
          if (effectiveDealerCurrency === effectivePriceCurrency) {
            dealerFxRate = priceFxRate;
          } else {
            const fxData = await api.fetchFxRate(effectiveDealerCurrency + 'CAD');
            dealerFxRate = fxData?.effective_rate || fxData?.rate || 1.0;
          }
        } catch { dealerFxRate = priceFxRate; }
      }

      const result = await api.uploadSalePricelist(
        saleId, file, skuCol, priceCol, effectivePriceCurrency, priceFxRate,
        dealerCostCol || null,
        dealerCostCol ? effectiveDealerCurrency : null,
        dealerFxRate
      );
      setPreview(result);
      if (result.status === 'error') onToast?.(result.message, 'error');
    } catch (e) {
      onToast?.(e.message, 'error');
    }
    setUploading(false);
  };

  const handleConfirm = async () => {
    if (!preview?.items?.length) return;
    setConfirming(true);
    try {
      await api.confirmSale(saleId, preview.items);
      setPreview(null);
      onToast?.('Sale items saved', 'success');
      await loadSale();
    } catch (e) {
      onToast?.(e.message, 'error');
    }
    setConfirming(false);
  };

  const handleActivate = async () => {
    if (!window.confirm(`Activate this sale? This will update ${sale.items?.length || 0} products in Shopify with sale pricing.`)) return;
    setActivating(true);
    try {
      const result = await api.activateSale(saleId);
      onToast?.(`Sale activated: ${result.activated} items updated`, 'success');
      await loadSale();
    } catch (e) {
      onToast?.(e.message, 'error');
    }
    setActivating(false);
  };

  const handleRevert = async () => {
    if (!window.confirm('Revert this sale? This will restore original prices for all active items.')) return;
    setReverting(true);
    try {
      const result = await api.revertSale(saleId);
      onToast?.(`Sale reverted: ${result.reverted} items restored`, 'success');
      await loadSale();
    } catch (e) {
      onToast?.(e.message, 'error');
    }
    setReverting(false);
  };

  const handleExclude = async (sku) => {
    if (!window.confirm(`Remove ${sku} from the active sale? This will restore its original price.`)) return;
    try {
      await api.excludeSaleItem(saleId, sku);
      onToast?.(`${sku} removed from sale`, 'success');
      await loadSale();
    } catch (e) {
      onToast?.(e.message, 'error');
    }
  };

  const handleScheduleUpdate = async (field, value) => {
    try {
      await api.updateSale(saleId, { [field]: value || null });
      setSale(prev => ({ ...prev, [field]: value || null }));
      onToast?.('Schedule updated', 'success');
    } catch (e) {
      onToast?.(e.message, 'error');
    }
  };

  if (loading) return <div style={{ padding: 40, textAlign: 'center', color: 'var(--text-muted)' }}>Loading sale...</div>;
  if (!sale) return <div style={{ padding: 40, textAlign: 'center' }}>Sale not found</div>;

  const canUpload = sale.status === 'pending' || sale.status === 'cancelled';
  const canActivate = sale.status === 'pending' && sale.items?.length > 0;
  const canRevert = sale.status === 'active';
  const canResubmit = sale.status === 'completed' || sale.status === 'cancelled';
  const canActivateNow = (sale.status === 'completed' || sale.status === 'cancelled') && sale.items?.length > 0;
  const canDelete = sale.status === 'pending' || sale.status === 'cancelled' || sale.status === 'completed';

  const handleResubmit = async () => {
    if (!window.confirm('Resubmit this sale to pending? Original prices will be refreshed from current Shopify data.')) return;
    try {
      const result = await api.resubmitSale(saleId);
      onToast?.(`Sale resubmitted: ${result.items_reset} items reset to pending`, 'success');
      await loadSale();
    } catch (e) {
      onToast?.(e.message, 'error');
    }
  };

  const handleActivateNow = async () => {
    if (!window.confirm(`Activate this sale now? This will resubmit to pending (refreshing current prices) and immediately activate ${sale.items?.length || 0} items in Shopify.`)) return;
    setActivating(true);
    try {
      // Step 1: Resubmit to pending
      await api.resubmitSale(saleId);
      // Step 2: Activate immediately
      const result = await api.activateSale(saleId);
      onToast?.(`Sale activated: ${result.activated} items updated`, 'success');
      await loadSale();
    } catch (e) {
      onToast?.(e.message, 'error');
      await loadSale(); // Refresh to show current state
    }
    setActivating(false);
  };

  const handleDelete = async () => {
    if (!window.confirm(`Delete sale "${sale.name}"? This cannot be undone.`)) return;
    try {
      const result = await api.deleteSale(saleId);
      if (result.status === 'error') {
        onToast?.(result.message, 'error');
      } else {
        onToast?.('Sale deleted', 'success');
        onBack();
      }
    } catch (e) {
      onToast?.(e.message, 'error');
    }
  };

  return (
    <div>
      {/* Header */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
        <div>
          <button onClick={onBack} style={{ ...s.btn(), marginRight: 12 }}>← Back</button>
          <span style={{ fontSize: 18, fontWeight: 700 }}>{sale.name}</span>
          <span style={{ ...s.badge(sale.status), marginLeft: 10 }}>{(STATUS_COLORS[sale.status] || STATUS_COLORS.pending).label}</span>
          <span style={{ fontSize: 12, color: 'var(--text-muted)', marginLeft: 10 }}>{sale.vendor} · {sale.currency}</span>
        </div>
        <div style={{ display: 'flex', gap: 8 }}>
          {canActivate && <button style={s.btn('success')} onClick={handleActivate} disabled={activating}>{activating ? 'Activating...' : '▶ Activate Sale'}</button>}
          {canActivateNow && <button style={s.btn('success')} onClick={handleActivateNow} disabled={activating}>{activating ? 'Activating...' : '▶ Activate Now'}</button>}
          {canRevert && <button style={s.btn('danger')} onClick={handleRevert} disabled={reverting}>{reverting ? 'Reverting...' : '⏹ Revert Sale'}</button>}
          {canResubmit && <button style={s.btn('purple')} onClick={handleResubmit}>🔄 Resubmit to Pending</button>}
          {canDelete && <button style={s.btn('danger')} onClick={handleDelete}>🗑 Delete</button>}
        </div>
      </div>

      {/* Schedule */}
      <div style={s.card}>
        <div style={s.h2}>Schedule</div>
        <div style={{ display: 'flex', gap: 16, flexWrap: 'wrap', alignItems: 'flex-end' }}>
          <div>
            <label style={s.label}>Start Date</label>
            <input type="datetime-local" style={{ ...s.input, width: 220 }}
                   value={sale.start_at ? sale.start_at.slice(0, 16) : ''}
                   onChange={e => handleScheduleUpdate('start_at', e.target.value)} />
          </div>
          <div>
            <label style={s.label}>End Date</label>
            <input type="datetime-local" style={{ ...s.input, width: 220 }}
                   value={sale.end_at ? sale.end_at.slice(0, 16) : ''}
                   onChange={e => handleScheduleUpdate('end_at', e.target.value)} />
          </div>
          <div style={{ fontSize: 11, color: 'var(--text-muted)', maxWidth: 300 }}>
            {sale.start_at && sale.end_at
              ? 'Sale will auto-activate and auto-revert on schedule.'
              : sale.start_at
              ? 'Sale will auto-activate. Manual revert required.'
              : sale.end_at
              ? 'Manual activation. Will auto-revert on end date.'
              : 'No schedule — activate and revert manually.'}
          </div>
        </div>
      </div>

      {/* Upload (only for pending/cancelled) */}
      {canUpload && (
        <div style={s.card}>
          <div style={s.h2}>Upload Sale Pricelist</div>
          <div style={{ marginBottom: 12 }}>
            <label style={s.label}>File (CSV or XLSX)</label>
            <input type="file" accept=".csv,.xlsx,.xls,.tsv"
                   onChange={e => handleFileSelect(e.target.files[0])} style={{ fontSize: 12 }} />
          </div>
          {fileHeaders.length > 0 && (
            <div style={{ display: 'flex', gap: 12, alignItems: 'flex-end', flexWrap: 'wrap', marginBottom: 12 }}>
              <div>
                <label style={s.label}>SKU Column *</label>
                <select style={s.select} value={skuCol} onChange={e => setSkuCol(e.target.value)}>
                  <option value="">— Select —</option>
                  {fileHeaders.map(h => <option key={h} value={h}>{h}</option>)}
                </select>
              </div>
              <div>
                <label style={s.label}>Sale Price Column *</label>
                <select style={s.select} value={priceCol} onChange={e => setPriceCol(e.target.value)}>
                  <option value="">— Select —</option>
                  {fileHeaders.map(h => <option key={h} value={h}>{h}</option>)}
                </select>
              </div>
              <div>
                <label style={s.label}>Price Currency</label>
                <select style={s.select} value={priceCurrency || sale?.currency || 'USD'}
                        onChange={e => setPriceCurrency(e.target.value)}>
                  <option value="CAD">CAD</option><option value="USD">USD</option><option value="EUR">EUR</option>
                </select>
              </div>
              <div>
                <label style={s.label}>Dealer Cost Column</label>
                <select style={s.select} value={dealerCostCol} onChange={e => setDealerCostCol(e.target.value)}>
                  <option value="">(optional)</option>
                  {fileHeaders.map(h => <option key={h} value={h}>{h}</option>)}
                </select>
              </div>
              {dealerCostCol && (
                <div>
                  <label style={s.label}>Cost Currency</label>
                  <select style={s.select} value={dealerCostCurrency || priceCurrency || sale?.currency || 'USD'}
                          onChange={e => setDealerCostCurrency(e.target.value)}>
                    <option value="CAD">CAD</option><option value="USD">USD</option><option value="EUR">EUR</option>
                  </select>
                </div>
              )}
              <button style={s.btn('primary')} onClick={handleUpload} disabled={uploading || !skuCol || !priceCol}>
                {uploading ? 'Uploading...' : '📊 Preview'}
              </button>
            </div>
          )}
          {file && fileHeaders.length === 0 && (
            <div style={{ display: 'flex', gap: 12, alignItems: 'flex-end', flexWrap: 'wrap', marginBottom: 12 }}>
              <div style={{ fontSize: 11, color: 'var(--text-muted)', width: '100%', marginBottom: 4 }}>
                Could not auto-detect columns. Type the exact column header names from your spreadsheet:
              </div>
              <div>
                <label style={s.label}>SKU Column *</label>
                <input style={{ ...s.input, width: 140 }} value={skuCol} onChange={e => setSkuCol(e.target.value)} placeholder="e.g. SKU" />
              </div>
              <div>
                <label style={s.label}>Sale Price Column *</label>
                <input style={{ ...s.input, width: 140 }} value={priceCol} onChange={e => setPriceCol(e.target.value)} placeholder="e.g. Sale Price" />
              </div>
              <div>
                <label style={s.label}>Price Currency</label>
                <select style={s.select} value={priceCurrency || sale?.currency || 'USD'}
                        onChange={e => setPriceCurrency(e.target.value)}>
                  <option value="CAD">CAD</option><option value="USD">USD</option><option value="EUR">EUR</option>
                </select>
              </div>
              <div>
                <label style={s.label}>Dealer Cost Column</label>
                <input style={{ ...s.input, width: 140 }} value={dealerCostCol} onChange={e => setDealerCostCol(e.target.value)} placeholder="(optional)" />
              </div>
              {dealerCostCol && (
                <div>
                  <label style={s.label}>Cost Currency</label>
                  <select style={s.select} value={dealerCostCurrency || priceCurrency || sale?.currency || 'USD'}
                          onChange={e => setDealerCostCurrency(e.target.value)}>
                    <option value="CAD">CAD</option><option value="USD">USD</option><option value="EUR">EUR</option>
                  </select>
                </div>
              )}
              <button style={s.btn('primary')} onClick={handleUpload} disabled={uploading || !skuCol || !priceCol}>
                {uploading ? 'Uploading...' : '📊 Preview'}
              </button>
            </div>
          )}
        </div>
      )}

      {/* Preview results */}
      {preview && preview.status === 'ok' && (
        <div style={s.card}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 }}>
            <div style={s.h2}>
              Preview: {preview.matched} matched, {preview.unmatched_count} unmatched
              {preview.avg_discount_pct > 0 && <span style={{ fontWeight: 400, color: '#f59e0b', marginLeft: 8 }}>avg {fmtPct(preview.avg_discount_pct)} off</span>}
            </div>
            <div style={{ display: 'flex', gap: 8 }}>
              <button style={s.btn('success')} onClick={handleConfirm} disabled={confirming}>
                {confirming ? 'Saving...' : '✓ Confirm & Save Items'}
              </button>
              <button style={s.btn()} onClick={() => setPreview(null)}>Cancel</button>
            </div>
          </div>

          {preview.warnings?.length > 0 && (
            <div style={{ background: '#4a2020', border: '1px solid #7f1d1d', borderRadius: 6, padding: 10, marginBottom: 12, fontSize: 12, color: '#fca5a5' }}>
              ⚠️ <strong>Warnings ({preview.warnings.length})</strong>
              <ul style={{ margin: '4px 0 0 16px', padding: 0 }}>
                {preview.warnings.slice(0, 10).map((w, i) => <li key={i}>{w}</li>)}
                {preview.warnings.length > 10 && <li>...and {preview.warnings.length - 10} more</li>}
              </ul>
            </div>
          )}

          {preview.unmatched?.length > 0 && (
            <div style={{ background: '#1e293b', borderRadius: 6, padding: 10, marginBottom: 12, fontSize: 11, color: '#94a3b8' }}>
              Unmatched SKUs: {preview.unmatched.slice(0, 20).join(', ')}
              {preview.unmatched.length > 20 && ` ...+${preview.unmatched.length - 20} more`}
            </div>
          )}

          <div style={{ maxHeight: 400, overflow: 'auto' }}>
            <table style={s.table}>
              <thead style={{ position: 'sticky', top: 0, background: 'var(--card-bg, #1a1f2e)', zIndex: 1 }}>
                <tr>
                  {['SKU', 'Title', 'Current', 'Sale Price', 'Discount',
                    ...(preview.items.some(i => i.dealer_cost_cad) ? ['Dealer Cost'] : ['Cost']),
                    'Sale Margin', 'Warning'].map(h => (
                    <th key={h} style={{ ...s.th, textAlign: ['Current', 'Sale Price', 'Discount', 'Cost', 'Dealer Cost', 'Sale Margin'].includes(h) ? 'right' : 'left' }}>{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {preview.items.map((item, i) => {
                  // Cost-precedence display: vendor_promo > dealer > cache.
                  // Whatever's shown here is also what feeds the margin %
                  // (the backend uses the same precedence in preview).
                  const promoActive = item.vendor_promo_active && item.vendor_promo_cost_cad;
                  const displayCost = promoActive
                    ? item.vendor_promo_cost_cad
                    : (item.dealer_cost_cad || item.cost);
                  const regularCost = item.dealer_cost_cad || item.cost;
                  const promoEnds = item.vendor_promo_ends_at
                    ? new Date(item.vendor_promo_ends_at).toLocaleString()
                    : '';
                  const costColor = promoActive ? '#f59e0b' : (item.dealer_cost_cad ? '#60a5fa' : 'var(--text-muted)');
                  return (
                    <tr key={item.sku} style={{ ...s.row(i), background: item.warning?.includes('BELOW COST') ? 'rgba(220,38,38,0.1)' : undefined }}>
                      <td style={{ ...s.td, fontWeight: 500, whiteSpace: 'nowrap' }}>{item.sku}</td>
                      <td style={{ ...s.td, maxWidth: 250, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{item.title}</td>
                      <td style={{ ...s.td, textAlign: 'right' }}>{fmt(item.original_price)}</td>
                      <td style={{ ...s.td, textAlign: 'right', fontWeight: 600, color: '#f59e0b' }}>{fmt(item.sale_price_cad)}</td>
                      <td style={{ ...s.td, textAlign: 'right', color: '#f59e0b' }}>-{fmtPct(item.discount_pct)}</td>
                      <td style={{ ...s.td, textAlign: 'right', color: costColor }}>
                        <div style={{ display: 'inline-flex', alignItems: 'center', gap: 4, justifyContent: 'flex-end' }}>
                          {fmt(displayCost)}
                          {promoActive && (
                            <span
                              title={`Vendor promo cost — regular ${fmt(regularCost)}${promoEnds ? `, ends ${promoEnds}` : ''}. Margin uses this discounted cost.`}
                              style={{
                                padding: '1px 5px', borderRadius: 6,
                                backgroundColor: '#fef3c7', color: '#92400e',
                                fontSize: 8, fontWeight: 700, letterSpacing: 0.4,
                                textTransform: 'uppercase', whiteSpace: 'nowrap',
                              }}>
                              VENDOR PROMO
                            </span>
                          )}
                          {!promoActive && item.dealer_cost_cad && (
                            <span style={{ fontSize: 9, color: '#6b7280' }}>(CAD)</span>
                          )}
                        </div>
                      </td>
                      <td style={{ ...s.td, textAlign: 'right', color: item.sale_margin_pct < 10 ? '#ef4444' : 'var(--text-muted)' }}>
                        {fmtPct(item.sale_margin_pct)}
                        {promoActive && (
                          <span
                            title={`Margin computed using vendor promo cost (${fmt(item.vendor_promo_cost_cad)}) instead of regular ${fmt(regularCost)}`}
                            style={{ fontSize: 10, color: '#92400e', marginLeft: 3, cursor: 'help' }}>
                            *
                          </span>
                        )}
                      </td>
                      <td style={{ ...s.td, fontSize: 11, color: item.warning?.includes('BELOW COST') ? '#ef4444' : '#f59e0b', maxWidth: 150 }}>{item.warning || ''}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* Saved items list */}
      {sale.items?.length > 0 && !preview && (
        <div style={s.card}>
          <div style={s.h2}>Sale Items ({sale.items.length})</div>
          <div style={{ maxHeight: 500, overflow: 'auto' }}>
            <table style={s.table}>
              <thead style={{ position: 'sticky', top: 0, background: 'var(--card-bg, #1a1f2e)', zIndex: 1 }}>
                <tr>
                  {['SKU', 'Original', 'Sale Price', 'Discount', 'Dealer Cost', 'Margin', 'Status', ''].map(h => (
                    <th key={h} style={{ ...s.th, textAlign: ['Original', 'Sale Price', 'Discount', 'Dealer Cost', 'Margin'].includes(h) ? 'right' : 'left' }}>{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {sale.items.map((item, i) => (
                  <tr key={item.sku} style={s.row(i)}>
                    <td style={{ ...s.td, fontWeight: 500, whiteSpace: 'nowrap' }}>{item.sku}</td>
                    <td style={{ ...s.td, textAlign: 'right' }}>{fmt(item.original_price)}</td>
                    <td style={{ ...s.td, textAlign: 'right', fontWeight: 600, color: '#f59e0b' }}>{fmt(item.sale_price_cad)}</td>
                    <td style={{ ...s.td, textAlign: 'right', color: '#f59e0b' }}>-{fmtPct(item.discount_pct)}</td>
                    <td style={{ ...s.td, textAlign: 'right', color: item.dealer_cost_cad ? '#60a5fa' : 'var(--text-muted)' }}>
                      {item.dealer_cost_cad ? fmt(item.dealer_cost_cad) : fmt(item.cost_cad)}
                    </td>
                    <td style={{ ...s.td, textAlign: 'right', color: item.sale_margin_pct < 10 ? '#ef4444' : 'var(--text-muted)' }}>{fmtPct(item.sale_margin_pct)}</td>
                    <td style={s.td}><span style={s.badge(item.status)}>{item.status}</span></td>
                    <td style={s.td}>
                      {item.status === 'active' && (
                        <button style={{ ...s.btn('danger'), fontSize: 11, padding: '3px 8px' }} onClick={() => handleExclude(item.sku)}>Remove</button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}


// ─── MAIN PAGE ──────────────────────────────────────────────────

export default function SalesManagerPage({ onToast }) {
  const [sales, setSales] = useState([]);
  const [loading, setLoading] = useState(true);
  const [selectedSaleId, setSelectedSaleId] = useState(null);
  const [vendors, setVendors] = useState([]);

  const loadSales = useCallback(async () => {
    setLoading(true);
    try {
      const data = await api.listSales();
      setSales(data);
    } catch (e) {
      onToast?.(e.message, 'error');
    }
    setLoading(false);
  }, []);

  const loadVendors = useCallback(async () => {
    try {
      const data = await api.fetchVendors();
      setVendors(data.map(v => v.vendor).filter(Boolean).sort());
    } catch { /* ignore */ }
  }, []);

  useEffect(() => { loadSales(); loadVendors(); }, [loadSales, loadVendors]);

  if (selectedSaleId) {
    return (
      <div style={s.page}>
        <SaleDetailView
          saleId={selectedSaleId}
          onBack={() => { setSelectedSaleId(null); loadSales(); }}
          onToast={onToast}
        />
      </div>
    );
  }

  return (
    <div style={s.page}>
      <div style={{ marginBottom: 20 }}>
        <h1 style={s.h1}>🏷️ Sales Manager</h1>
        <div style={s.subtitle}>Manage temporary vendor sale pricing. Upload sale pricelists, preview changes, and schedule automatic activation.</div>
      </div>

      {loading ? (
        <div style={{ padding: 40, textAlign: 'center', color: 'var(--text-muted)' }}>Loading sales...</div>
      ) : (
        <>
          <SaleDashboard
            sales={sales}
            onSelect={(sale) => setSelectedSaleId(sale.id)}
            onRefresh={loadSales}
          />

          <div style={{ marginTop: 24 }}>
            <CreateSaleForm
              vendors={vendors}
              onCreated={(id) => { loadSales(); setSelectedSaleId(id); }}
              onToast={onToast}
            />
          </div>
        </>
      )}
    </div>
  );
}
