import { useState, useEffect, useMemo, useRef, Fragment } from 'react';
import * as api from './api';
import ScrapeConfigEditor from './ScrapeConfigEditor';
import SkuMappingsPanel from './SkuMappingsPanel';

const fmt = (n) => new Intl.NumberFormat('en-CA', { style: 'currency', currency: 'CAD', minimumFractionDigits: 2 }).format(n);
const fmtPct = (n) => `${n.toFixed(1)}%`;

// Small inline loading indicator used on buttons that fire long-running
// Shopify writes (bulk cost / price update, etc.). Uses the existing
// global `spin` keyframe (defined in styles.css) so it doesn't need its
// own animation declaration.
function InlineSpinner({ color = 'currentColor', size = 12 }) {
  return (
    <span
      aria-label="loading"
      style={{
        display: 'inline-block', width: size, height: size,
        border: '2px solid rgba(255,255,255,0.35)',
        borderTopColor: color,
        borderRadius: '50%',
        animation: 'spin 0.7s linear infinite',
      }}
    />
  );
}

// Build a TSV string from a 2D array and write it to the clipboard.
// We deliberately use raw numbers (not formatted currency strings) so the
// values paste into Excel / Google Sheets as numbers rather than text.
// Returns true on success.
async function copyTsv(rows) {
  const tsv = rows
    .map(r => r.map(cell => {
      if (cell == null) return '';
      const s = String(cell);
      // Tabs and newlines inside a cell would corrupt the TSV layout, so
      // strip them. Quotes are fine in TSV (unlike CSV).
      return s.replace(/[\t\r\n]+/g, ' ');
    }).join('\t'))
    .join('\n');
  try {
    if (navigator.clipboard && navigator.clipboard.writeText) {
      await navigator.clipboard.writeText(tsv);
      return true;
    }
  } catch (_) { /* fall through to legacy path */ }
  // Legacy fallback for non-secure contexts
  try {
    const ta = document.createElement('textarea');
    ta.value = tsv;
    ta.style.position = 'fixed';
    ta.style.left = '-9999px';
    document.body.appendChild(ta);
    ta.select();
    const ok = document.execCommand('copy');
    document.body.removeChild(ta);
    return ok;
  } catch (_) {
    return false;
  }
}

const COPY_BTN_STYLE = {
  padding: '3px 8px',
  borderRadius: 4,
  border: '1px solid var(--border)',
  cursor: 'pointer',
  backgroundColor: 'transparent',
  color: 'var(--text)',
  fontSize: 10,
  fontWeight: 600,
};

// ─── FX RATE PANEL ──────────────────────────────────────────────

function FxRateRow({ pair, label, onToast }) {
  const [fx, setFx] = useState(null);
  const [offset, setOffset] = useState('');
  const [refreshing, setRefreshing] = useState(false);

  useEffect(() => {
    api.fetchFxRate(pair).then(data => {
      setFx(data);
      setOffset(String(data.offset_pct || 0));
    }).catch(() => {});
  }, [pair]);

  const handleRefresh = async () => {
    setRefreshing(true);
    try {
      const result = await api.refreshFxRate(pair, parseFloat(offset) || 0);
      setFx(result);
      onToast?.(`${label} rate refreshed`, 'success');
    } catch (err) {
      onToast?.('Failed to refresh: ' + err.message, 'error');
    }
    setRefreshing(false);
  };

  const handleOffsetSave = async () => {
    try {
      const result = await api.setFxOffset(pair, parseFloat(offset) || 0);
      setFx(result);
      onToast?.(`${label} offset saved`, 'success');
    } catch (err) {
      onToast?.('Failed to save offset: ' + err.message, 'error');
    }
  };

  return (
    <div style={{ display: 'flex', gap: 20, alignItems: 'flex-end', flexWrap: 'wrap' }}>
      <div style={{ fontWeight: 600, fontSize: 13, width: 75 }}>{label}</div>
      <div>
        <div style={{ fontSize: 10, color: 'var(--text-muted)', marginBottom: 2 }}>Bank Rate</div>
        <div style={{ fontSize: 18, fontWeight: 700 }}>{fx?.rate ? fx.rate.toFixed(4) : '—'}</div>
      </div>
      <div>
        <div style={{ fontSize: 10, color: 'var(--text-muted)', marginBottom: 2 }}>Offset %</div>
        <div style={{ display: 'flex', gap: 4 }}>
          <input type="number" step="0.5" value={offset}
            onChange={e => setOffset(e.target.value)}
            style={{ padding: '4px 8px', borderRadius: 4, border: '1px solid var(--border)', backgroundColor: 'var(--input-bg)', color: 'var(--text)', width: 60, fontSize: 12 }}
          />
          <button onClick={handleOffsetSave} style={{ padding: '4px 8px', borderRadius: 4, border: '1px solid var(--border)', cursor: 'pointer', backgroundColor: 'transparent', color: 'var(--text)', fontSize: 11 }}>Save</button>
        </div>
      </div>
      <div>
        <div style={{ fontSize: 10, color: 'var(--text-muted)', marginBottom: 2 }}>Effective</div>
        <div style={{ fontSize: 18, fontWeight: 700, color: '#6366f1' }}>{fx?.effective_rate ? fx.effective_rate.toFixed(4) : '—'}</div>
      </div>
      <button onClick={handleRefresh} disabled={refreshing} style={{
        padding: '6px 12px', borderRadius: 6, border: 'none', cursor: 'pointer',
        backgroundColor: '#6366f1', color: '#fff', fontWeight: 600, fontSize: 11,
        opacity: refreshing ? 0.6 : 1,
      }}>
        {refreshing ? '...' : 'Refresh'}
      </button>
      {fx?.fetched_at && (
        <div style={{ fontSize: 10, color: 'var(--text-muted)' }}>
          {new Date(fx.fetched_at).toLocaleDateString()}
        </div>
      )}
    </div>
  );
}

function FxRatePanel({ onToast }) {
  return (
    <div style={{
      backgroundColor: 'var(--card-bg)', border: '1px solid var(--border)',
      borderRadius: 8, padding: 20, marginBottom: 24,
    }}>
      <div style={{ fontSize: 14, fontWeight: 600, marginBottom: 12 }}>Exchange Rates</div>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
        <FxRateRow pair="USDCAD" label="USD → CAD" onToast={onToast} />
        <FxRateRow pair="EURCAD" label="EUR → CAD" onToast={onToast} />
      </div>
    </div>
  );
}

// ─── VENDOR EDITOR MODAL ────────────────────────────────────────

function VendorEditModal({ vendor, onSave, onClose }) {
  const [form, setForm] = useState({
    vendor: vendor?.vendor || '',
    lead_time_days: vendor?.lead_time_days || 14,
    min_order_value: vendor?.min_order_value || '',
    notes: vendor?.notes || '',
    invoice_currency: vendor?.invoice_currency || 'CAD',
    enforces_map: vendor?.enforces_map || false,
    default_markup_pct: vendor?.default_markup_pct || '',
    website_url: vendor?.website_url || '',
  });
  const [saving, setSaving] = useState(false);

  const handleSave = async () => {
    setSaving(true);
    try {
      await api.upsertVendor({
        ...form,
        min_order_value: form.min_order_value !== '' ? parseFloat(form.min_order_value) : null,
        default_markup_pct: form.default_markup_pct !== '' ? parseFloat(form.default_markup_pct) : null,
      });
      onSave();
    } catch (err) {
      alert('Failed to save: ' + err.message);
    }
    setSaving(false);
  };

  const Field = ({ label, children }) => (
    <div style={{ marginBottom: 12 }}>
      <div style={{ fontSize: 11, color: 'var(--text-muted)', marginBottom: 4 }}>{label}</div>
      {children}
    </div>
  );

  const inputStyle = {
    padding: '6px 10px', borderRadius: 6, border: '1px solid var(--border)',
    backgroundColor: 'var(--input-bg)', color: 'var(--text)', fontSize: 13, width: '100%',
  };

  return (
    <div style={{
      position: 'fixed', top: 0, left: 0, right: 0, bottom: 0,
      backgroundColor: 'rgba(0,0,0,0.5)', zIndex: 1000,
      display: 'flex', alignItems: 'center', justifyContent: 'center',
    }} onClick={onClose}>
      <div style={{
        backgroundColor: 'var(--card-bg)', borderRadius: 12, padding: 24,
        width: 500, maxHeight: '80vh', overflowY: 'auto',
        boxShadow: '0 8px 32px rgba(0,0,0,0.3)',
      }} onClick={e => e.stopPropagation()}>
        <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 16 }}>
          <div style={{ fontSize: 16, fontWeight: 600 }}>
            {vendor?.is_new ? 'Configure' : 'Edit'} {form.vendor}
          </div>
          <button onClick={onClose} style={{ border: 'none', background: 'none', cursor: 'pointer', fontSize: 18, color: 'var(--text-muted)' }}>✕</button>
        </div>

        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
          <Field label="Invoice Currency">
            <select value={form.invoice_currency}
              onChange={e => setForm(f => ({ ...f, invoice_currency: e.target.value }))}
              style={inputStyle}>
              <option value="CAD">CAD</option>
              <option value="USD">USD</option>
              <option value="EUR">EUR</option>
              <option value="GBP">GBP</option>
              <option value="CNY">CNY</option>
            </select>
          </Field>
          <Field label="Lead Time (days)">
            <input type="number" value={form.lead_time_days}
              onChange={e => setForm(f => ({ ...f, lead_time_days: parseInt(e.target.value) || 14 }))}
              style={inputStyle} />
          </Field>
          <Field label="Enforces MAP Pricing">
            <div style={{ display: 'flex', gap: 8, marginTop: 4 }}>
              {[{ val: true, label: 'Yes' }, { val: false, label: 'No' }].map(opt => (
                <button key={String(opt.val)} onClick={() => setForm(f => ({ ...f, enforces_map: opt.val }))}
                  style={{
                    padding: '6px 16px', borderRadius: 6, fontSize: 12, fontWeight: 600, cursor: 'pointer',
                    border: '1px solid var(--border)',
                    backgroundColor: form.enforces_map === opt.val ? (opt.val ? '#fef3c7' : '#d1fae5') : 'transparent',
                    color: form.enforces_map === opt.val ? (opt.val ? '#92400e' : '#065f46') : 'var(--text-muted)',
                  }}>
                  {opt.label}
                </button>
              ))}
            </div>
          </Field>
          <Field label="Default Markup % (non-MAP)">
            <input type="number" step="1" placeholder="e.g. 30"
              value={form.default_markup_pct}
              onChange={e => setForm(f => ({ ...f, default_markup_pct: e.target.value }))}
              style={inputStyle} disabled={form.enforces_map} />
          </Field>
          <Field label="Min Order Value">
            <input type="number" step="50" placeholder="Optional"
              value={form.min_order_value}
              onChange={e => setForm(f => ({ ...f, min_order_value: e.target.value }))}
              style={inputStyle} />
          </Field>
          <Field label="Website URL">
            <input type="url" placeholder="https://..."
              value={form.website_url}
              onChange={e => setForm(f => ({ ...f, website_url: e.target.value }))}
              style={inputStyle} />
          </Field>
        </div>

        <Field label="Notes">
          <textarea value={form.notes || ''}
            onChange={e => setForm(f => ({ ...f, notes: e.target.value }))}
            rows={3} style={{ ...inputStyle, resize: 'vertical' }} />
        </Field>

        <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8, marginTop: 16 }}>
          <button onClick={onClose} style={{
            padding: '8px 16px', borderRadius: 6, border: '1px solid var(--border)',
            cursor: 'pointer', backgroundColor: 'transparent', color: 'var(--text)', fontSize: 13,
          }}>Cancel</button>
          <button onClick={handleSave} disabled={saving} style={{
            padding: '8px 20px', borderRadius: 6, border: 'none', cursor: 'pointer',
            backgroundColor: '#6366f1', color: '#fff', fontWeight: 600, fontSize: 13,
          }}>
            {saving ? 'Saving...' : 'Save Settings'}
          </button>
        </div>
      </div>
    </div>
  );
}

// ─── PRICELIST UPLOAD MODAL ─────────────────────────────────────

function SuggestionRow({ vendorName, item, colSpan, onConfirmed, onToast }) {
  const [picked, setPicked] = useState(item.suggestions?.[0]?.sku || '');
  const [busy, setBusy] = useState(false);
  if (!item.suggestions || item.suggestions.length === 0) return null;
  const handleConfirm = async () => {
    if (!picked) return;
    setBusy(true);
    try {
      await api.confirmPricelistMapping(vendorName, item.supplier_sku, picked);
      onToast?.(`Saved mapping: ${item.supplier_sku} → ${picked}`, 'success');
      onConfirmed?.();
    } catch (err) {
      onToast?.('Save failed: ' + err.message, 'error');
    }
    setBusy(false);
  };
  return (
    <tr>
      <td colSpan={colSpan} style={{ padding: '6px 12px 10px 24px', backgroundColor: '#fafafe', borderBottom: '1px solid var(--border)' }}>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center', fontSize: 12 }}>
          <span style={{ color: 'var(--text-muted)', minWidth: 88 }}>Pick a match:</span>
          <select
            value={picked}
            onChange={e => setPicked(e.target.value)}
            disabled={busy}
            style={{ flex: 1, padding: '4px 8px', borderRadius: 4, border: '1px solid var(--border)', backgroundColor: 'var(--input-bg)', color: 'var(--text)', fontSize: 12 }}
          >
            {item.suggestions.map((s, i) => (
              <option key={i} value={s.sku}>
                {s.sku} — {s.title} ({Math.round((s.score || 0) * 100)}%)
              </option>
            ))}
          </select>
          <button
            onClick={handleConfirm}
            disabled={busy || !picked}
            style={{
              padding: '4px 14px', borderRadius: 4, border: 'none', cursor: 'pointer',
              backgroundColor: '#22c55e', color: '#fff', fontWeight: 600, fontSize: 11,
              opacity: busy || !picked ? 0.5 : 1,
            }}
          >
            {busy ? 'Saving…' : 'Confirm & Save'}
          </button>
        </div>
      </td>
    </tr>
  );
}


function EditableSkuCell({ item, onToast, onChanged }) {
  const [editing, setEditing] = useState(false);
  const [value, setValue] = useState(item.sku || '');
  const [busy, setBusy] = useState(false);

  const startEdit = () => {
    setValue(item.sku || '');
    setEditing(true);
  };
  const cancel = () => { setEditing(false); setValue(item.sku || ''); };

  const save = async () => {
    const trimmed = (value || '').trim();
    if (!trimmed) { onToast?.('SKU cannot be blank', 'error'); return; }
    if (trimmed === item.sku) { setEditing(false); return; }
    if (!item.product_id || !item.variant_id) {
      onToast?.('Missing product/variant id — cannot rename', 'error'); return;
    }
    if (!confirm(`Rename Shopify SKU "${item.sku}" → "${trimmed}"?`)) return;
    setBusy(true);
    try {
      const res = await api.updateVariantSku({
        productId: item.product_id,
        variantId: item.variant_id,
        newSku: trimmed,
        oldSku: item.sku,
      });
      onToast?.(`Renamed → ${res.new_sku}`, 'success');
      setEditing(false);
      onChanged?.();
    } catch (err) {
      onToast?.('Rename failed: ' + err.message, 'error');
    }
    setBusy(false);
  };

  if (!item.variant_id) {
    // No variant id (e.g. cache miss) — fall back to read-only.
    return <span style={{ fontFamily: 'monospace', fontSize: 11 }}>{item.sku}</span>;
  }

  if (editing) {
    return (
      <span style={{ display: 'inline-flex', gap: 4, alignItems: 'center' }}>
        <input
          autoFocus
          value={value}
          onChange={e => setValue(e.target.value)}
          onKeyDown={e => {
            if (e.key === 'Enter') save();
            if (e.key === 'Escape') cancel();
          }}
          style={{
            fontFamily: 'monospace', fontSize: 11, padding: '2px 6px', borderRadius: 4,
            border: '1px solid var(--green)', backgroundColor: 'var(--input-bg)', color: 'var(--text)',
            width: 200,
          }}
        />
        <button onClick={save} disabled={busy} title="Save"
          style={{ border: 'none', background: 'none', cursor: 'pointer', fontSize: 12, color: '#16a34a' }}>✓</button>
        <button onClick={cancel} title="Cancel"
          style={{ border: 'none', background: 'none', cursor: 'pointer', fontSize: 12, color: 'var(--text-muted)' }}>×</button>
      </span>
    );
  }

  return (
    <span
      onClick={startEdit}
      title="Click to rename this SKU in Shopify"
      style={{
        fontFamily: 'monospace', fontSize: 11, cursor: 'pointer',
        padding: '2px 4px', borderRadius: 3,
        borderBottom: '1px dashed var(--text-muted)',
      }}
    >
      {item.sku}
    </span>
  );
}


function PrefixMatchRow({ item, onToast, onFixed }) {
  const [busy, setBusy] = useState(false);
  const newSku = item.prefix_match_suggested_sku || item.supplier_sku;
  const handleFix = async () => {
    if (!item.shopify_product_id || !item.shopify_variant_id) {
      onToast?.('Missing product/variant id — cannot rename', 'error'); return;
    }
    if (!newSku) { onToast?.('No suggested SKU', 'error'); return; }
    if (!confirm(`Rename Shopify SKU "${item.shopify_sku}" → "${newSku}"?`)) return;
    setBusy(true);
    try {
      const res = await api.updateVariantSku({
        productId: item.shopify_product_id,
        variantId: item.shopify_variant_id,
        newSku,
        oldSku: item.shopify_sku,
      });
      onToast?.(`Renamed → ${res.new_sku}`, 'success');
      onFixed?.();
    } catch (err) {
      onToast?.('Rename failed: ' + err.message, 'error');
    }
    setBusy(false);
  };
  return (
    <tr>
      <td style={{ padding: '4px 8px', fontFamily: 'monospace', fontSize: 11 }}>{item.supplier_sku}</td>
      <td style={{ padding: '4px 8px', fontFamily: 'monospace', fontSize: 11, color: '#dc2626' }}>{item.shopify_sku}</td>
      <td style={{ padding: '4px 8px', maxWidth: 280, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{item.shopify_title}</td>
      <td style={{ padding: '4px 8px' }}>{fmt(item.shopify_cost || 0)}</td>
      <td style={{ padding: '4px 8px', fontFamily: 'monospace', fontSize: 11, color: '#16a34a' }}>{newSku}</td>
      <td style={{ padding: '4px 8px', textAlign: 'right' }}>
        <button onClick={handleFix} disabled={busy}
          style={{
            padding: '4px 10px', borderRadius: 4, border: 'none', cursor: 'pointer',
            backgroundColor: '#6366f1', color: '#fff', fontWeight: 600, fontSize: 11,
            opacity: busy ? 0.5 : 1,
          }}>
          {busy ? 'Renaming…' : 'Fix SKU in Shopify'}
        </button>
      </td>
    </tr>
  );
}


function PricelistModal({ vendor, initialResult, onDone, onClose, onToast }) {
  const [file, setFile] = useState(null);
  const [columns, setColumns] = useState([]);
  const [skuCol, setSkuCol] = useState('');
  const [skuSecondaryCol, setSkuSecondaryCol] = useState('');
  const [skuSeparator, setSkuSeparator] = useState(' ');
  const [costCol, setCostCol] = useState('');
  const [costCurrency, setCostCurrency] = useState(vendor?.invoice_currency || 'USD');
  const [descCol, setDescCol] = useState('');
  const [msrpCol, setMsrpCol] = useState('');
  const [msrpCurrency, setMsrpCurrency] = useState('CAD');
  const [fallbackMsrpCol, setFallbackMsrpCol] = useState('');
  const [fallbackMsrpCurrency, setFallbackMsrpCurrency] = useState('USD');
  const [barcodeCol, setBarcodeCol] = useState('');
  const [mapCadCol, setMapCadCol] = useState('');
  const [cooCol, setCooCol] = useState('');
  const [uploading, setUploading] = useState(false);
  const [detecting, setDetecting] = useState(false);
  const [aiExtracting, setAiExtracting] = useState(false);
  const [result, setResult] = useState(initialResult || null);
  const [draftSelected, setDraftSelected] = useState(new Set());
  const [drafting, setDrafting] = useState(false);
  const [creatingDraft, setCreatingDraft] = useState(new Set());
  const [draftUrl, setDraftUrl] = useState(null);
  const [draftUrlSku, setDraftUrlSku] = useState(null);
  // Tracks which bulk Shopify update is in progress so we can show
  // an inline spinner on the active button and disable the other one
  // (we don't want a concurrent costs + prices write to race).
  // null | 'costs' | 'prices'.
  const [bulkOp, setBulkOp] = useState(null);
  const fileRef = useRef();

  const exportCsv = () => {
    if (!result) return;
    const curr = result.pricelist_currency || 'USD';
    const rows = [['Type', 'SKU', 'Description', `Pricelist Cost (${curr})`, 'Pricelist Cost (CAD)', 'Shopify Cost (CAD)', 'Shopify Price', 'Stock', 'Cost Changed']];
    (result.matched || []).forEach(m => {
      rows.push(['Matched', m.supplier_sku || m.shopify_sku, m.shopify_title || m.description, m.cost, m.cost_cad || m.cost, m.shopify_cost, m.shopify_price, m.shopify_stock, m.cost_changed ? 'YES' : '']);
    });
    (result.in_pricelist_only || []).forEach(m => {
      rows.push(['New - Not in Shopify', m.supplier_sku, m.description, m.cost, m.cost_cad || m.cost, '', '', '', '']);
    });
    (result.in_shopify_only || []).forEach(m => {
      rows.push(['Missing from Pricelist', m.sku, m.title, '', '', m.cost, m.price, m.stock, '']);
    });
    const csvContent = rows.map(r => r.map(v => '"' + String(v ?? '').replace(/"/g, '""') + '"').join(',')).join('\n');
    const blob = new Blob([csvContent], { type: 'text/csv' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = 'pricelist_comparison_' + vendor.vendor.replace(/ /g, '_') + '.csv';
    a.click();
    URL.revokeObjectURL(url);
  };

  const handleFileChange = async (e) => {
    const f = e.target.files[0];
    if (!f) return;
    setFile(f);
    setDetecting(true);
    setColumns([]);
    try {
      // Use server-side detection (works for CSV, TSV, and XLSX)
      const res = await api.detectPricelistColumns(f);
      const cols = res.columns || [];
      setColumns(cols);

      // Use saved column mappings if they exist and match available columns
      const savedSku = vendor?.pricelist_sku_column;
      const savedCost = vendor?.pricelist_cost_column;
      const savedDesc = vendor?.pricelist_desc_column;
      const savedMsrp = vendor?.pricelist_msrp_column;
      const savedFallback = vendor?.pricelist_fallback_msrp_column;
      const savedBarcode = vendor?.pricelist_barcode_column;
      const savedMapCad = vendor?.pricelist_map_cad_column;
      const savedCoo = vendor?.pricelist_coo_column;

      let foundSku = savedSku && cols.includes(savedSku) ? savedSku : '';
      let foundCost = savedCost && cols.includes(savedCost) ? savedCost : '';
      let foundDesc = savedDesc && cols.includes(savedDesc) ? savedDesc : '';
      let foundMsrp = savedMsrp && cols.includes(savedMsrp) ? savedMsrp : '';
      let foundFallback = savedFallback && cols.includes(savedFallback) ? savedFallback : '';
      let foundBarcode = savedBarcode && cols.includes(savedBarcode) ? savedBarcode : '';
      let foundMapCad = savedMapCad && cols.includes(savedMapCad) ? savedMapCad : '';
      let foundCoo = savedCoo && cols.includes(savedCoo) ? savedCoo : '';

      // Restore saved currency settings
      if (vendor?.pricelist_cost_currency) setCostCurrency(vendor.pricelist_cost_currency);
      if (vendor?.pricelist_msrp_currency) setMsrpCurrency(vendor.pricelist_msrp_currency);
      if (vendor?.pricelist_fallback_msrp_currency) setFallbackMsrpCurrency(vendor.pricelist_fallback_msrp_currency);

      // Restore combine-columns choice if saved
      const savedSecondary = vendor?.pricelist_sku_secondary_column;
      if (savedSecondary && cols.includes(savedSecondary)) setSkuSecondaryCol(savedSecondary);
      if (vendor?.pricelist_sku_separator != null) setSkuSeparator(vendor.pricelist_sku_separator);

      // Fall back to auto-detection if no saved mappings matched
      if (!foundSku || !foundCost) {
        cols.forEach(c => {
          const cl = c.toLowerCase();
          if (!foundSku && (cl.includes('sku') || cl.includes('item') || cl.includes('part') || cl.includes('model'))) foundSku = c;
          if (!foundCost && (cl.includes('cost') || cl.includes('dealer') || cl.includes('wholesale'))) foundCost = c;
          if (!foundDesc && (cl.includes('desc') || cl.includes('name') || cl.includes('title') || cl.includes('product'))) foundDesc = c;
        });
      }

      // Auto-detect MAP/MSRP columns with currency awareness
      if (!foundMsrp) {
        cols.forEach(c => {
          const cl = c.toLowerCase();
          // Prefer CAD MAP column as primary
          if (!foundMsrp && cl.includes('cad') && (cl.includes('map') || cl.includes('msrp') || cl.includes('retail'))) {
            foundMsrp = c;
            setMsrpCurrency('CAD');
          }
        });
        // If no CAD MAP found, look for any MAP/MSRP
        if (!foundMsrp) {
          cols.forEach(c => {
            const cl = c.toLowerCase();
            if (!foundMsrp && (cl.includes('map') || cl.includes('msrp') || cl.includes('retail') || cl.includes('list'))) {
              foundMsrp = c;
              // Guess currency from column name
              if (cl.includes('cad')) setMsrpCurrency('CAD');
              else if (cl.includes('usd') || cl.includes('us')) setMsrpCurrency('USD');
            }
          });
        }
      }

      // Auto-detect fallback MSRP (USD MSRP when primary is CAD MAP)
      cols.forEach(c => {
        const cl = c.toLowerCase();
        if (c !== foundMsrp && !foundFallback && (cl.includes('msrp') || cl.includes('retail') || cl.includes('list'))) {
          foundFallback = c;
          if (cl.includes('usd') || cl.includes('us')) setFallbackMsrpCurrency('USD');
          else if (cl.includes('cad')) setFallbackMsrpCurrency('CAD');
        }
      });

      // Auto-detect Barcode/UPC and explicit CAD MAP columns
      if (!foundBarcode) {
        cols.forEach(c => {
          const cl = c.toLowerCase().trim();
          if (!foundBarcode && (cl === 'upc' || cl === 'upc code' || cl === 'barcode' || cl === 'ean' || cl === 'gtin' || cl.includes('upc') || cl.includes('barcode'))) {
            foundBarcode = c;
          }
        });
      }
      if (!foundMapCad) {
        cols.forEach(c => {
          const cl = c.toLowerCase().trim();
          if (!foundMapCad && (cl === 'cad map' || cl === 'canada map' || cl === 'map cad' || cl === 'map canada' || cl === 'canadian map' || (cl.includes('canada') && cl.includes('map')))) {
            foundMapCad = c;
          }
        });
      }

      // Auto-detect Country of Origin column
      if (!foundCoo) {
        cols.forEach(c => {
          const cl = c.toLowerCase().trim();
          if (!foundCoo && (cl === 'coo' || cl === 'country of origin' || cl === 'country' || cl === 'origin' || cl.includes('country of origin') || (cl.includes('origin') && !cl.includes('original')))) {
            foundCoo = c;
          }
        });
      }

      setSkuCol(foundSku);
      setCostCol(foundCost);
      setDescCol(foundDesc);
      setMsrpCol(foundMsrp);
      setFallbackMsrpCol(foundFallback);
      setBarcodeCol(foundBarcode);
      setMapCadCol(foundMapCad);
      setCooCol(foundCoo);
    } catch (err) {
      onToast?.('Could not read file columns: ' + err.message, 'error');
    }
    setDetecting(false);
  };

  const handleUpload = async () => {
    if (!file || !skuCol || !costCol) return;
    setUploading(true);
    try {
      const res = await api.uploadPricelist(vendor.vendor, file, {
        skuColumn: skuCol,
        costColumn: costCol,
        costCurrency: costCurrency,
        descColumn: descCol || undefined,
        msrpColumn: msrpCol || undefined,
        msrpCurrency: msrpCurrency,
        fallbackMsrpColumn: fallbackMsrpCol || undefined,
        fallbackMsrpCurrency: fallbackMsrpCurrency,
        skuSecondaryColumn: skuSecondaryCol || undefined,
        skuSeparator: skuSeparator,
        barcodeColumn: barcodeCol || undefined,
        mapCadColumn: mapCadCol || undefined,
        cooColumn: cooCol || undefined,
      });
      // The backend returns {status: 'error', message: ...} for soft failures
      // (e.g. "No valid SKUs found in CSV") with a 200 status code, so we have
      // to inspect the payload — a thrown exception above only catches
      // transport / 5xx errors.
      if (res?.status === 'error') {
        onToast?.('Pricelist processing failed: ' + (res.message || 'unknown error'), 'error');
        return;
      }
      setResult(res);
      onToast?.(`Pricelist processed: ${res.matched_count ?? 0} matched, ${res.in_pricelist_only_count ?? 0} new, ${res.in_shopify_only_count ?? 0} missing`, 'success');
    } catch (err) {
      onToast?.('Upload failed: ' + err.message, 'error');
    }
    setUploading(false);
  };

  const ColSelect = ({ label, value, onChange, required }) => (
    <div>
      <div style={{ fontSize: 11, color: 'var(--text-muted)', marginBottom: 4 }}>{label}{required && ' *'}</div>
      <select value={value} onChange={e => onChange(e.target.value)} style={{
        padding: '6px 10px', borderRadius: 6, border: '1px solid var(--border)',
        backgroundColor: 'var(--input-bg)', color: 'var(--text)', fontSize: 12, width: '100%',
      }}>
        <option value="">— Select —</option>
        {columns.map(c => <option key={c} value={c}>{c}</option>)}
      </select>
    </div>
  );

  const CurrencySelect = ({ value, onChange }) => (
    <select value={value} onChange={e => onChange(e.target.value)} style={{
      padding: '6px 10px', borderRadius: 6, border: '1px solid var(--border)',
      backgroundColor: 'var(--input-bg)', color: 'var(--text)', fontSize: 12, width: '100%',
    }}>
      <option value="USD">USD</option>
      <option value="CAD">CAD</option>
      <option value="EUR">EUR</option>
    </select>
  );

  return (
    <div style={{
      position: 'fixed', top: 0, left: 0, right: 0, bottom: 0,
      backgroundColor: 'rgba(0,0,0,0.5)', zIndex: 1000,
      display: 'flex', alignItems: 'center', justifyContent: 'center',
    }} onClick={onClose}>
      <div style={{
        backgroundColor: 'var(--card-bg)', borderRadius: 12, padding: 24,
        width: 950, maxHeight: '85vh', overflowY: 'auto',
        boxShadow: '0 8px 32px rgba(0,0,0,0.3)',
      }} onClick={e => e.stopPropagation()}>
        <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 16 }}>
          <div style={{ fontSize: 16, fontWeight: 600 }}>Upload Pricelist — {vendor.vendor}</div>
          <button onClick={onClose} style={{ border: 'none', background: 'none', cursor: 'pointer', fontSize: 18, color: 'var(--text-muted)' }}>✕</button>
        </div>

        {!result ? (
          <>
            <div style={{ marginBottom: 16, display: 'flex', alignItems: 'center', gap: 8 }}>
              <input ref={fileRef} type="file" accept=".csv,.tsv,.txt,.xlsx,.xls,.pdf" onChange={handleFileChange}
                style={{ fontSize: 13 }} />
              {detecting && <span style={{ fontSize: 12, color: 'var(--text-muted)' }}>Detecting columns...</span>}
              {file && file.name?.toLowerCase().endsWith('.pdf') && (
                <button onClick={async () => {
                  setAiExtracting(true);
                  try {
                    const res = await api.aiExtractPdf(file);
                    setColumns(res.columns);
                    // Create a new file from the AI-extracted CSV
                    const blob = new Blob([res.csv_content], { type: 'text/csv' });
                    const csvFile = new File([blob], file.name.replace('.pdf', '_ai.csv'), { type: 'text/csv' });
                    setFile(csvFile);
                    onToast?.('AI extracted ' + res.columns.length + ' columns (' + (res.tokens_used || 0) + ' tokens)', 'success');
                    // Re-run auto-detect on new columns
                    res.columns.forEach(c => {
                      const cl = c.toLowerCase();
                      if (cl.includes('sku') || cl.includes('item') || cl.includes('part')) setSkuCol(c);
                      if (cl.includes('cost') || cl.includes('dealer') || cl.includes('wholesale')) setCostCol(c);
                      if (cl.includes('desc') || cl.includes('name') || cl.includes('title') || cl.includes('product')) setDescCol(c);
                      if (cl.includes('cad') && (cl.includes('map') || cl.includes('msrp'))) { setMsrpCol(c); setMsrpCurrency('CAD'); }
                    });
                  } catch (err) { onToast?.('AI extraction failed: ' + err.message, 'error'); }
                  setAiExtracting(false);
                }} disabled={aiExtracting} style={{
                  padding: '6px 14px', borderRadius: 6, border: 'none', cursor: 'pointer',
                  backgroundColor: '#8b5cf6', color: '#fff', fontWeight: 600, fontSize: 12,
                  opacity: aiExtracting ? 0.6 : 1, whiteSpace: 'nowrap',
                }}>
                  {aiExtracting ? 'Extracting...' : 'AI Extract'}
                </button>
              )}
            </div>

            {columns.length > 0 && (
              <>
                <div style={{ fontSize: 12, color: 'var(--text-muted)', marginBottom: 8 }}>
                  Detected {columns.length} columns. Map them below:
                </div>
                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 8, marginBottom: 12 }}>
                  <ColSelect label="SKU Column *" value={skuCol} onChange={setSkuCol} required />
                  <div style={{ display: 'flex', gap: 4 }}>
                    <div style={{ flex: 1 }}><ColSelect label="Cost Column *" value={costCol} onChange={setCostCol} required /></div>
                    <div style={{ width: 70 }}>
                      <div style={{ fontSize: 11, color: 'var(--text-muted)', marginBottom: 4 }}>Curr.</div>
                      <CurrencySelect value={costCurrency} onChange={setCostCurrency} />
                    </div>
                  </div>
                  <ColSelect label="Description" value={descCol} onChange={setDescCol} />
                </div>
                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8, marginBottom: 12 }}>
                  <div style={{ display: 'flex', gap: 4 }}>
                    <div style={{ flex: 1 }}><ColSelect label="Primary MAP/MSRP" value={msrpCol} onChange={setMsrpCol} /></div>
                    <div style={{ width: 70 }}>
                      <div style={{ fontSize: 11, color: 'var(--text-muted)', marginBottom: 4 }}>Curr.</div>
                      <CurrencySelect value={msrpCurrency} onChange={setMsrpCurrency} />
                    </div>
                  </div>
                  <div style={{ display: 'flex', gap: 4 }}>
                    <div style={{ flex: 1 }}><ColSelect label="Fallback MSRP (optional)" value={fallbackMsrpCol} onChange={setFallbackMsrpCol} /></div>
                    <div style={{ width: 70 }}>
                      <div style={{ fontSize: 11, color: 'var(--text-muted)', marginBottom: 4 }}>Curr.</div>
                      <CurrencySelect value={fallbackMsrpCurrency} onChange={setFallbackMsrpCurrency} />
                    </div>
                  </div>
                </div>
                {fallbackMsrpCol && (
                  <div style={{ fontSize: 11, color: 'var(--text-muted)', marginBottom: 12, fontStyle: 'italic' }}>
                    Fusion: Uses Primary MAP if available, falls back to Fallback MSRP (converted to CAD) when Primary is blank.
                  </div>
                )}

                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8, marginBottom: 12 }}>
                  <ColSelect label="Barcode / UPC (optional)" value={barcodeCol} onChange={setBarcodeCol} />
                  <ColSelect label="CAD MAP (optional, used as listing price)" value={mapCadCol} onChange={setMapCadCol} />
                </div>
                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8, marginBottom: 12 }}>
                  <ColSelect label="Country of Origin (optional)" value={cooCol} onChange={setCooCol} />
                </div>
                {cooCol && (
                  <div style={{ fontSize: 11, color: 'var(--text-muted)', marginBottom: 12, fontStyle: 'italic' }}>
                    After upload, use "Set Country of Origin in Shopify" to push these codes (e.g. CN, TW, DE) to each matched product's inventory item.
                  </div>
                )}
                {mapCadCol && (
                  <div style={{ fontSize: 11, color: 'var(--text-muted)', marginBottom: 12, fontStyle: 'italic' }}>
                    Draft listings will use this column directly as the CAD listing price (no FX conversion or markup applied).
                  </div>
                )}

                {/* Combine columns: for vendors who split product/size into two columns
                    (e.g. Optolong: Item's Name + Size). Builds a synthetic SKU
                    like 'L-Pro 1.25"' that the matcher can title-bridge to your
                    Shopify variants. */}
                <div style={{ marginBottom: 12, padding: '8px 10px', borderRadius: 6, border: '1px solid var(--border)' }}>
                  <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 12, cursor: 'pointer', marginBottom: skuSecondaryCol ? 8 : 0 }}>
                    <input
                      type="checkbox"
                      checked={!!skuSecondaryCol}
                      onChange={e => { if (!e.target.checked) setSkuSecondaryCol(''); else setSkuSecondaryCol(columns.find(c => c !== skuCol) || ''); }}
                    />
                    <span>Combine 2 columns into the SKU</span>
                    <span style={{ color: 'var(--text-muted)', fontSize: 11 }}>
                      — for vendors that split product name and size, e.g. "L-Pro" + "1.25\""
                    </span>
                  </label>
                  {skuSecondaryCol && (
                    <div style={{ display: 'grid', gridTemplateColumns: '1fr 80px', gap: 8 }}>
                      <ColSelect label="Secondary SKU Column" value={skuSecondaryCol} onChange={setSkuSecondaryCol} />
                      <div>
                        <div style={{ fontSize: 11, color: 'var(--text-muted)', marginBottom: 4 }}>Separator</div>
                        <input
                          value={skuSeparator}
                          onChange={e => setSkuSeparator(e.target.value)}
                          maxLength={5}
                          placeholder=" "
                          style={{ width: '100%', padding: '6px 10px', borderRadius: 6, border: '1px solid var(--border)', backgroundColor: 'var(--input-bg)', color: 'var(--text)', fontSize: 12 }}
                        />
                      </div>
                    </div>
                  )}
                </div>

                <button onClick={handleUpload} disabled={uploading || !skuCol || !costCol} style={{
                  padding: '8px 20px', borderRadius: 6, border: 'none', cursor: 'pointer',
                  backgroundColor: '#6366f1', color: '#fff', fontWeight: 600, fontSize: 13,
                  opacity: (uploading || !skuCol || !costCol) ? 0.5 : 1,
                }}>
                  {uploading ? 'Processing...' : 'Compare Pricelist'}
                </button>
              </>
            )}
          </>
        ) : (
          <>
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 12, marginBottom: 16 }}>
              {[
                { label: 'Total Items', value: result.total_pricelist_items, color: '#6366f1' },
                { label: 'Matched', value: result.matched_count, color: '#22c55e' },
                { label: 'New (not in Shopify)', value: result.in_pricelist_only_count, color: '#f97316' },
                { label: 'Missing from Pricelist', value: result.in_shopify_only_count, color: '#ef4444' },
              ].map((c, i) => (
                <div key={i} style={{
                  padding: 12, borderRadius: 6, borderLeft: `4px solid ${c.color}`,
                  border: '1px solid var(--border)', backgroundColor: 'var(--card-bg)',
                }}>
                  <div style={{ fontSize: 11, color: 'var(--text-muted)' }}>{c.label}</div>
                  <div style={{ fontSize: 20, fontWeight: 700 }}>{c.value}</div>
                </div>
              ))}
            </div>

            {result.fx_rates && Object.keys(result.fx_rates).filter(k => k !== 'CAD').length > 0 && (
              <div style={{ padding: '8px 12px', borderRadius: 6, backgroundColor: 'var(--hover-bg)', marginBottom: 16, fontSize: 12, color: 'var(--text-muted)' }}>
                FX Rates: {Object.entries(result.fx_rates).filter(([k]) => k !== 'CAD').map(([k, v]) => `1 ${k} = ${v.toFixed(4)} CAD`).join(' | ')}
              </div>
            )}

            {(() => {
              const prefixMatches = (result.matched || []).filter(m => m.matched_via === 'prefix');
              if (prefixMatches.length === 0) return null;
              return (
                <div style={{ marginBottom: 16 }}>
                  <div style={{ marginBottom: 8 }}>
                    <div style={{ fontSize: 13, fontWeight: 600, color: '#6366f1' }}>
                      Vendor-prefix matches ({prefixMatches.length})
                    </div>
                    <div style={{ fontSize: 11, color: 'var(--text-muted)', marginTop: 2 }}>
                      The Shopify SKU starts with "{vendor.vendor}" but the pricelist uses the bare SKU. Click <strong>Fix SKU in Shopify</strong> to drop the prefix so future matches are exact.
                    </div>
                  </div>
                  <div style={{ maxHeight: 240, overflowY: 'auto' }}>
                    <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
                      <thead>
                        <tr>
                          {['Pricelist SKU', 'Shopify SKU (current)', 'Product', 'Shopify Cost', '→', ''].map(h => (
                            <th key={h} style={{ padding: '4px 8px', borderBottom: '1px solid var(--border)', textAlign: 'left', fontSize: 11, position: 'sticky', top: 0, backgroundColor: 'var(--card-bg)', zIndex: 1 }}>{h}</th>
                          ))}
                        </tr>
                      </thead>
                      <tbody>
                        {prefixMatches.map((m, i) => (
                          <PrefixMatchRow key={(m.shopify_variant_id || '') + '-' + i} item={m} onToast={onToast} onFixed={async () => {
                            try {
                              const fresh = await api.recomparePricelist(vendor.vendor);
                              setResult(fresh);
                            } catch (err) {
                              onToast?.('Re-compare after rename failed: ' + err.message, 'error');
                            }
                          }} />
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>
              );
            })()}

            {result.cost_changes?.length > 0 && (
              <div style={{ marginBottom: 16 }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
                  <div style={{ fontSize: 13, fontWeight: 600, color: '#f97316' }}>
                    Cost Changes ({result.cost_changes.length} items)
                  </div>
                  <button
                    disabled={bulkOp !== null}
                    onClick={async () => {
                      if (!confirm(`Update ${result.cost_changes.length} product costs in Shopify?`)) return;
                      setBulkOp('costs');
                      onToast?.('Updating costs in Shopify...', 'success');
                      try {
                        const res = await api.bulkUpdateCosts(vendor.vendor);
                        onToast?.(`Updated ${res.updated} of ${res.total} costs`, 'success');
                      } catch (err) { onToast?.('Cost update failed: ' + err.message, 'error'); }
                      setBulkOp(null);
                    }}
                    style={{
                      padding: '6px 14px', borderRadius: 6, border: 'none',
                      cursor: bulkOp !== null ? 'not-allowed' : 'pointer',
                      backgroundColor: '#f97316', color: '#fff', fontWeight: 600, fontSize: 12,
                      opacity: bulkOp && bulkOp !== 'costs' ? 0.5 : 1,
                      display: 'inline-flex', alignItems: 'center', gap: 8,
                    }}>
                    {bulkOp === 'costs' && <InlineSpinner color="#fff" />}
                    {bulkOp === 'costs' ? 'Updating costs...' : 'Update All Costs in Shopify'}
                  </button>
                </div>
                <div style={{ maxHeight: 200, overflowY: 'auto' }}>
                  <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
                    <thead>
                      <tr>
                        {['SKU', 'Product', 'Shopify Cost', `Pricelist (${result.cost_currency || 'USD'})`, 'Pricelist (CAD)', 'Diff (CAD)'].map(h => (
                          <th key={h} style={{ padding: '4px 8px', borderBottom: '1px solid var(--border)', textAlign: 'left', fontSize: 11, position: 'sticky', top: 0, backgroundColor: 'var(--card-bg)', zIndex: 1 }}>{h}</th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {result.cost_changes.map((c, i) => {
                        const costCad = c.cost_cad != null ? c.cost_cad : c.cost;
                        const diff = costCad - c.shopify_cost;
                        return (
                          <tr key={i}>
                            <td style={{ padding: '4px 8px', fontFamily: 'monospace', fontSize: 11 }}>{c.supplier_sku}</td>
                            <td style={{ padding: '4px 8px', maxWidth: 300, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{c.shopify_title}</td>
                            <td style={{ padding: '4px 8px' }}>{fmt(c.shopify_cost)}</td>
                            <td style={{ padding: '4px 8px', color: 'var(--text-muted)' }}>{fmt(c.cost)}</td>
                            <td style={{ padding: '4px 8px', fontWeight: 600 }}>{fmt(costCad)}</td>
                            <td style={{ padding: '4px 8px', color: diff > 0 ? '#ef4444' : '#22c55e', fontWeight: 600 }}>
                              {diff > 0 ? '↑' : '↓'} {fmt(Math.abs(diff))}
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              </div>
            )}

            {result.price_changes?.length > 0 && (
              <div style={{ marginBottom: 16 }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
                  <div style={{ fontSize: 13, fontWeight: 600, color: '#8b5cf6' }}>
                    Selling Price Changes ({result.price_changes.length} items)
                  </div>
                  <button
                    disabled={bulkOp !== null}
                    onClick={async () => {
                      if (!confirm(`Update ${result.price_changes.length} selling prices in Shopify? "On Sale" tagged products are excluded.`)) return;
                      setBulkOp('prices');
                      onToast?.('Updating selling prices in Shopify...', 'success');
                      try {
                        const res = await api.bulkUpdatePrices(vendor.vendor);
                        onToast?.(`Updated ${res.updated} of ${res.total} prices`, 'success');
                      } catch (err) { onToast?.('Price update failed: ' + err.message, 'error'); }
                      setBulkOp(null);
                    }}
                    style={{
                      padding: '6px 14px', borderRadius: 6, border: 'none',
                      cursor: bulkOp !== null ? 'not-allowed' : 'pointer',
                      backgroundColor: '#8b5cf6', color: '#fff', fontWeight: 600, fontSize: 12,
                      opacity: bulkOp && bulkOp !== 'prices' ? 0.5 : 1,
                      display: 'inline-flex', alignItems: 'center', gap: 8,
                    }}>
                    {bulkOp === 'prices' && <InlineSpinner color="#fff" />}
                    {bulkOp === 'prices' ? 'Updating prices...' : 'Update Selling Prices in Shopify'}
                  </button>
                </div>
                <div style={{ maxHeight: 200, overflowY: 'auto' }}>
                  <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
                    <thead>
                      <tr>
                        {['SKU', 'Product', 'Shopify Price', 'Pricelist Price (CAD)', 'Source', 'Diff'].map(h => (
                          <th key={h} style={{ padding: '4px 8px', borderBottom: '1px solid var(--border)', textAlign: 'left', fontSize: 11, position: 'sticky', top: 0, backgroundColor: 'var(--card-bg)', zIndex: 1 }}>{h}</th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {result.price_changes.map((c, i) => {
                        const diff = (c.sale_price_cad || 0) - c.shopify_price;
                        return (
                          <tr key={i}>
                            <td style={{ padding: '4px 8px', fontFamily: 'monospace', fontSize: 11 }}>{c.supplier_sku}</td>
                            <td style={{ padding: '4px 8px', maxWidth: 300, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{c.shopify_title}</td>
                            <td style={{ padding: '4px 8px' }}>{fmt(c.shopify_price)}</td>
                            <td style={{ padding: '4px 8px', fontWeight: 600 }}>{fmt(c.sale_price_cad)}</td>
                            <td style={{ padding: '4px 8px', fontSize: 10, color: 'var(--text-muted)' }}>
                              {c.price_source === 'primary' ? 'MAP' : c.price_source === 'fallback' ? 'MSRP (converted)' : '—'}
                            </td>
                            <td style={{ padding: '4px 8px', color: diff > 0 ? '#22c55e' : '#ef4444', fontWeight: 600 }}>
                              {diff > 0 ? '+' : ''}{fmt(diff)}
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              </div>
            )}

            {result.coo_count > 0 && (
              <div style={{ marginBottom: 16 }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
                  <div style={{ fontSize: 13, fontWeight: 600, color: '#0ea5e9' }}>
                    Country of Origin ({result.coo_count} item{result.coo_count === 1 ? '' : 's'} with a COO)
                  </div>
                  <button
                    disabled={bulkOp !== null}
                    onClick={async () => {
                      if (!confirm(`Set Country of Origin in Shopify for matched ${vendor.vendor} products? Only items whose COO differs from Shopify will be written.`)) return;
                      setBulkOp('coo');
                      onToast?.('Setting Country of Origin in Shopify...', 'success');
                      try {
                        const res = await api.bulkUpdateCountryOfOrigin(vendor.vendor);
                        const unchanged = res.skipped_unchanged ? `, ${res.skipped_unchanged} already correct` : '';
                        onToast?.(`Country of Origin: ${res.updated} of ${res.total} updated${unchanged}`, 'success');
                      } catch (err) { onToast?.('COO update failed: ' + err.message, 'error'); }
                      setBulkOp(null);
                    }}
                    style={{
                      padding: '6px 14px', borderRadius: 6, border: 'none',
                      cursor: bulkOp !== null ? 'not-allowed' : 'pointer',
                      backgroundColor: '#0ea5e9', color: '#fff', fontWeight: 600, fontSize: 12,
                      opacity: bulkOp && bulkOp !== 'coo' ? 0.5 : 1,
                      display: 'inline-flex', alignItems: 'center', gap: 8,
                    }}>
                    {bulkOp === 'coo' && <InlineSpinner color="#fff" />}
                    {bulkOp === 'coo' ? 'Setting COO...' : 'Set Country of Origin in Shopify'}
                  </button>
                </div>
                <div style={{ fontSize: 11, color: 'var(--text-muted)' }}>
                  Pushes the pricelist's COO code to each matched product's Shopify inventory item. Items already set to the correct country are skipped.
                </div>
              </div>
            )}

            {result.in_pricelist_only?.length > 0 && (
              <div style={{ marginBottom: 16 }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
                  <div style={{ fontSize: 13, fontWeight: 600, color: '#f97316' }}>
                    New Items — Not in Shopify ({result.in_pricelist_only.length})
                  </div>
                  <button
                    onClick={async () => {
                      const currency = result.cost_currency || 'USD';
                      const header = ['SKU', 'Description', `Cost (${currency})`, 'Cost (CAD)', 'Sale Price (CAD)'];
                      const rows = [header, ...result.in_pricelist_only.map(item => [
                        item.supplier_sku,
                        item.description || '',
                        item.cost ?? '',
                        item.cost_cad ?? '',
                        item.sale_price_cad ?? '',
                      ])];
                      const ok = await copyTsv(rows);
                      onToast?.(
                        ok ? `Copied ${result.in_pricelist_only.length} row(s) to clipboard` : 'Copy failed',
                        ok ? 'success' : 'error',
                      );
                    }}
                    style={COPY_BTN_STYLE}
                    title="Copy table as TSV (paste into Excel, Sheets, or Slack)"
                  >
                    Copy
                  </button>
                </div>
                <div style={{ maxHeight: 250, overflowY: 'auto' }}>
                  <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
                    <thead>
                      <tr>
                        {['SKU', 'Description', `Cost (${result.cost_currency || 'USD'})`, 'Cost (CAD)', 'Sale Price (CAD)', ''].map(h => (
                          <th key={h} style={{ padding: '4px 8px', borderBottom: '1px solid var(--border)', textAlign: 'left', fontSize: 11, position: 'sticky', top: 0, backgroundColor: 'var(--card-bg)', zIndex: 1 }}>{h}</th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {result.in_pricelist_only.map((item, i) => (
                        <Fragment key={i}>
                        <tr>
                          <td style={{ padding: '4px 8px', fontFamily: 'monospace', fontSize: 11 }}>
                            {item.supplier_sku}
                            {item.suggestions?.length > 0 && (
                              <div style={{ fontSize: 10, color: '#6366f1', marginTop: 2 }}>
                                {item.suggestions.length} suggested match{item.suggestions.length === 1 ? '' : 'es'}
                              </div>
                            )}
                          </td>
                          <td style={{ padding: '4px 8px' }}>{item.description || '—'}</td>
                          <td style={{ padding: '4px 8px' }}>{fmt(item.cost)}</td>
                          <td style={{ padding: '4px 8px' }}>{fmt(item.cost_cad)}</td>
                          <td style={{ padding: '4px 8px', fontWeight: 600 }}>{item.sale_price_cad ? fmt(item.sale_price_cad) : '—'}</td>
                          <td style={{ padding: '4px 8px', textAlign: 'right', minWidth: 220 }}>
                            {creatingDraft.has(item.supplier_sku) ? (
                              <span style={{ fontSize: 11, color: 'var(--text-muted)' }}>Creating draft...</span>
                            ) : draftUrl !== null && draftUrlSku === item.supplier_sku ? (
                              <div style={{ display: 'flex', gap: 4, alignItems: 'center' }}>
                                <input value={draftUrl} onChange={e => setDraftUrl(e.target.value)}
                                  placeholder="https://vendor.com/product"
                                  style={{ padding: '3px 6px', borderRadius: 4, border: '1px solid var(--border)', backgroundColor: 'var(--input-bg)', color: 'var(--text)', fontSize: 10, width: 180 }}
                                  onKeyDown={e => { if (e.key === 'Enter') document.getElementById('draft-go-' + i)?.click(); }}
                                  autoFocus />
                                <button id={'draft-go-' + i} onClick={async (e) => {
                                  e.stopPropagation();
                                  const key = item.supplier_sku;
                                  setCreatingDraft(prev => new Set(prev).add(key));
                                  setDraftUrl(null); setDraftUrlSku(null);
                                  try {
                                    const res = await api.createDraftProduct({
                                      vendor: vendor.vendor,
                                      sku: item.supplier_sku,
                                      description: item.description || '',
                                      cost_foreign: item.cost || 0,
                                      cost_cad: item.cost_cad || 0,
                                      sale_price_cad: item.sale_price_cad || 0,
                                      product_url: draftUrl || '',
                                      barcode: item.barcode || null,
                                      map_cad: item.map_cad || null,
                                    });
                                    let msg = 'Draft created: ' + res.title;
                                    if (res.enriched) msg += ' (AI enriched)';
                                    if (res.publications_published > 0) msg += ` · published to ${res.publications_published} channel${res.publications_published === 1 ? '' : 's'}`;
                                    if (res.publication_error) msg += ' | Sales channels NOT set: ' + res.publication_error;
                                    if (res.pricing_warning) msg += ' | ' + res.pricing_warning;
                                    const isError = res.pricing_warning || res.publication_error;
                                    onToast?.(msg, isError ? 'error' : 'success');
                                  } catch (err) { onToast?.('Draft failed: ' + err.message, 'error'); }
                                  setCreatingDraft(prev => { const n = new Set(prev); n.delete(key); return n; });
                                }} style={{ padding: '3px 8px', borderRadius: 4, border: 'none', cursor: 'pointer', backgroundColor: '#22c55e', color: '#fff', fontWeight: 600, fontSize: 10 }}>Go</button>
                                <button onClick={() => { setDraftUrl(null); setDraftUrlSku(null); }}
                                  style={{ padding: '3px 6px', borderRadius: 4, border: 'none', cursor: 'pointer', backgroundColor: 'transparent', color: 'var(--text-muted)', fontSize: 10 }}>x</button>
                              </div>
                            ) : (
                              <button
                                onClick={(e) => { e.stopPropagation(); setDraftUrlSku(item.supplier_sku); setDraftUrl(''); }}
                                style={{ padding: '3px 10px', borderRadius: 4, border: 'none', cursor: 'pointer', backgroundColor: '#22c55e', color: '#fff', fontWeight: 600, fontSize: 10, whiteSpace: 'nowrap' }}>
                                Create Draft
                              </button>
                            )}
                          </td>
                        </tr>
                        {item.suggestions?.length > 0 && (
                          <SuggestionRow
                            vendorName={vendor.vendor}
                            item={item}
                            colSpan={6}
                            onConfirmed={async () => {
                              try {
                                const fresh = await api.recomparePricelist(vendor.vendor);
                                setResult(fresh);
                              } catch (err) {
                                onToast?.('Re-compare after confirm failed: ' + err.message, 'error');
                              }
                            }}
                            onToast={onToast}
                          />
                        )}
                        </Fragment>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            )}

            {result.in_shopify_only?.length > 0 && (
              <div style={{ marginBottom: 16 }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8, gap: 8 }}>
                  <div style={{ fontSize: 13, fontWeight: 600, color: '#ef4444' }}>
                    In Shopify but Missing from Pricelist ({result.in_shopify_only.length})
                  </div>
                  <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
                    <button
                      onClick={async () => {
                        const header = ['SKU', 'Product', 'Stock', 'Price', 'Cost'];
                        const rows = [header, ...result.in_shopify_only.map(item => [
                          item.sku,
                          item.title || '',
                          item.stock ?? '',
                          item.price ?? '',
                          item.cost ?? '',
                        ])];
                        const ok = await copyTsv(rows);
                        onToast?.(
                          ok ? `Copied ${result.in_shopify_only.length} row(s) to clipboard` : 'Copy failed',
                          ok ? 'success' : 'error',
                        );
                      }}
                      style={COPY_BTN_STYLE}
                      title="Copy table as TSV (paste into Excel, Sheets, or Slack)"
                    >
                      Copy
                    </button>
                  {draftSelected.size > 0 && (
                    <div style={{ display: 'flex', gap: 6 }}>
                      <button onClick={async () => {
                        if (!confirm(`Set ${draftSelected.size} product(s) to "Do not continue selling when out of stock"?`)) return;
                        setDrafting(true);
                        try {
                          const items = result.in_shopify_only
                            .filter(i => draftSelected.has(i.product_id))
                            .map(i => ({ product_id: i.product_id, variant_id: i.variant_id, sku: i.sku }));
                          const res = await api.setInventoryPolicy(items, 'DENY');
                          onToast?.(`Updated ${res.updated} product(s) — will stop selling when OOS`, 'success');
                        } catch (err) { onToast?.('Failed: ' + err.message, 'error'); }
                        setDrafting(false);
                      }} disabled={drafting} style={{
                        padding: '6px 12px', borderRadius: 6, border: 'none', cursor: 'pointer',
                        backgroundColor: '#f97316', color: '#fff', fontWeight: 600, fontSize: 11,
                        opacity: drafting ? 0.6 : 1,
                      }}>
                        {drafting ? 'Updating...' : 'Stop selling when OOS'}
                      </button>
                      <button onClick={async () => {
                        if (!confirm(`Move ${draftSelected.size} product(s) to Draft in Shopify?`)) return;
                        setDrafting(true);
                        try {
                          const res = await api.setProductStatus([...draftSelected], 'DRAFT');
                          onToast?.(`Moved ${res.updated} product(s) to Draft`, 'success');
                          setDraftSelected(new Set());
                        } catch (err) { onToast?.('Failed: ' + err.message, 'error'); }
                        setDrafting(false);
                      }} disabled={drafting} style={{
                        padding: '6px 12px', borderRadius: 6, border: 'none', cursor: 'pointer',
                        backgroundColor: '#ef4444', color: '#fff', fontWeight: 600, fontSize: 11,
                        opacity: drafting ? 0.6 : 1,
                      }}>
                        Move to Draft
                      </button>
                      <button onClick={async () => {
                        if (!confirm(`Tag ${draftSelected.size} product(s) as "Replacement Part"? They will be excluded from future pricelist comparisons.`)) return;
                        setDrafting(true);
                        try {
                          const res = await api.addTagToProducts([...draftSelected], 'Replacement Part');
                          onToast?.(`Tagged ${res.tagged} product(s) as Replacement Part`, 'success');
                          setDraftSelected(new Set());
                        } catch (err) { onToast?.('Failed: ' + err.message, 'error'); }
                        setDrafting(false);
                      }} disabled={drafting} style={{
                        padding: '6px 12px', borderRadius: 6, border: 'none', cursor: 'pointer',
                        backgroundColor: '#6366f1', color: '#fff', fontWeight: 600, fontSize: 11,
                        opacity: drafting ? 0.6 : 1,
                      }}>
                        Tag as Replacement Part
                      </button>
                      <button onClick={async () => {
                        if (!confirm(`Tag ${draftSelected.size} product(s) as "Discontinued"?\n\n• Products with 0 stock will be archived in Shopify\n• Products with stock will be set to stop selling when OOS\n• All will be excluded from future pricelist comparisons.`)) return;
                        setDrafting(true);
                        try {
                          // 1. Tag as Discontinued
                          const res = await api.addTagToProducts([...draftSelected], 'Discontinued');
                          onToast?.(`Tagged ${res.tagged} product(s) as Discontinued`, 'success');

                          // 2. Separate items by stock level
                          const selectedItems = result.in_shopify_only.filter(i => draftSelected.has(i.product_id));
                          const zeroStockIds = selectedItems.filter(i => !i.stock || i.stock <= 0).map(i => i.product_id);
                          const hasStockItems = selectedItems.filter(i => i.stock > 0 && i.variant_id);

                          // 3. Archive products with 0 stock
                          if (zeroStockIds.length > 0) {
                            try {
                              const archiveRes = await api.setProductStatus(zeroStockIds, 'ARCHIVED');
                              onToast?.(`Archived ${archiveRes.updated} product(s) with 0 stock`, 'success');
                            } catch (err) { onToast?.('Archive failed: ' + err.message, 'error'); }
                          }

                          // 4. Set DENY inventory policy on products with stock
                          if (hasStockItems.length > 0) {
                            try {
                              const denyRes = await api.setInventoryPolicy(
                                hasStockItems.map(i => ({ product_id: i.product_id, variant_id: i.variant_id, sku: i.sku })),
                                'DENY'
                              );
                              onToast?.(`Set ${denyRes.updated} product(s) to stop selling when OOS`, 'success');
                            } catch (err) { onToast?.('Inventory policy update failed: ' + err.message, 'error'); }
                          }

                          setDraftSelected(new Set());
                        } catch (err) { onToast?.('Failed: ' + err.message, 'error'); }
                        setDrafting(false);
                      }} disabled={drafting} style={{
                        padding: '6px 12px', borderRadius: 6, border: 'none', cursor: 'pointer',
                        backgroundColor: '#dc2626', color: '#fff', fontWeight: 600, fontSize: 11,
                        opacity: drafting ? 0.6 : 1,
                      }}>
                        Tag as Discontinued
                      </button>
                    </div>
                  )}
                  </div>
                </div>
                <div style={{ maxHeight: 200, overflowY: 'auto' }}>
                  <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
                    <thead>
                      <tr>
                        <th style={{ padding: '4px 6px', borderBottom: '1px solid var(--border)', width: 30, position: 'sticky', top: 0, backgroundColor: 'var(--card-bg)', zIndex: 1 }}>
                          <input type="checkbox"
                            checked={result.in_shopify_only.filter(i => i.product_id).length > 0 && draftSelected.size === result.in_shopify_only.filter(i => i.product_id).length}
                            onChange={() => {
                              const ids = result.in_shopify_only.filter(i => i.product_id).map(i => i.product_id);
                              setDraftSelected(prev => prev.size === ids.length ? new Set() : new Set(ids));
                            }}
                            style={{ cursor: 'pointer' }} />
                        </th>
                        <th style={{ padding: '4px 6px', borderBottom: '1px solid var(--border)', fontSize: 11, textAlign: 'center', width: 35, position: 'sticky', top: 0, backgroundColor: 'var(--card-bg)', zIndex: 1 }} title="Stop selling when out of stock">OOS</th>
                        <th style={{ padding: '4px 6px', borderBottom: '1px solid var(--border)', fontSize: 11, textAlign: 'center', width: 35, position: 'sticky', top: 0, backgroundColor: 'var(--card-bg)', zIndex: 1 }} title="Discontinued">🚫</th>
                        {['SKU', 'Product', 'Stock', 'Price', 'Cost'].map(h => (
                          <th key={h} style={{ padding: '4px 8px', borderBottom: '1px solid var(--border)', textAlign: 'left', fontSize: 11, position: 'sticky', top: 0, backgroundColor: 'var(--card-bg)', zIndex: 1 }}>{h}</th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {result.in_shopify_only.map((item, i) => (
                        <tr key={i}>
                          <td style={{ padding: '4px 6px', textAlign: 'center' }}>
                            {item.product_id ? (
                              <input type="checkbox" checked={draftSelected.has(item.product_id)}
                                onChange={() => setDraftSelected(prev => {
                                  const next = new Set(prev);
                                  if (next.has(item.product_id)) next.delete(item.product_id); else next.add(item.product_id);
                                  return next;
                                })}
                                style={{ cursor: 'pointer' }} />
                            ) : null}
                          </td>
                          <td style={{ padding: '4px 6px', textAlign: 'center' }}>
                            {item.inventory_policy === 'DENY' && <span title="Will stop selling when out of stock" style={{ color: '#22c55e', fontSize: 13 }}>✓</span>}
                          </td>
                          <td style={{ padding: '4px 6px', textAlign: 'center' }}>
                            {((item.tags || '').toLowerCase().includes('discontinued')) && <span title="Discontinued" style={{ color: '#ef4444', fontSize: 13 }}>🚫</span>}
                          </td>
                          <td style={{ padding: '4px 8px' }}>
                            <EditableSkuCell
                              item={item}
                              onToast={onToast}
                              onChanged={async () => {
                                try {
                                  const fresh = await api.recomparePricelist(vendor.vendor);
                                  setResult(fresh);
                                } catch (err) {
                                  onToast?.('Re-compare after rename failed: ' + err.message, 'error');
                                }
                              }}
                            />
                          </td>
                          <td style={{ padding: '4px 8px', maxWidth: 350, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{item.title}</td>
                          <td style={{ padding: '4px 8px' }}>{item.stock}</td>
                          <td style={{ padding: '4px 8px' }}>{fmt(item.price)}</td>
                          <td style={{ padding: '4px 8px' }}>{fmt(item.cost)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            )}

            <div style={{ display: 'flex', gap: 8, marginTop: 4 }}>
              <button onClick={() => { setResult(null); onDone(); }} style={{
                padding: '8px 16px', borderRadius: 6, border: '1px solid var(--border)',
                cursor: 'pointer', backgroundColor: 'transparent', color: 'var(--text)', fontSize: 13,
              }}>Close</button>
              <button onClick={exportCsv} style={{
                padding: '8px 16px', borderRadius: 6, border: 'none',
                cursor: 'pointer', backgroundColor: '#6366f1', color: '#fff', fontWeight: 600, fontSize: 13,
              }}>Export CSV</button>
            </div>
          </>
        )}
      </div>
    </div>
  );
}

// --- Inline editable cell helpers ---

const cellInput = {
  padding: '4px 6px', borderRadius: 4, border: '1px solid var(--border)',
  backgroundColor: 'var(--input-bg)', color: 'var(--text)', fontSize: 12, width: '100%',
};

// --- Create Draft Product Modal ---
// Standalone draft creator launched from a vendor row (independent of any
// pricelist upload). The user pastes a vendor product URL and optionally
// supplies pricing; the backend's create-draft endpoint AI-enriches the
// description from the URL using the vendor's saved scrape config.
function CreateDraftModal({ vendor, onClose, onToast }) {
  const [productUrl, setProductUrl] = useState('');
  const [sku, setSku] = useState('');
  const [description, setDescription] = useState('');
  const [costForeign, setCostForeign] = useState('');
  const [costCad, setCostCad] = useState('');
  const [salePriceCad, setSalePriceCad] = useState('');
  const [mapCad, setMapCad] = useState('');
  const [barcode, setBarcode] = useState('');
  const [submitting, setSubmitting] = useState(false);

  const currency = vendor?.invoice_currency || 'USD';

  const inputStyle = {
    padding: '6px 10px', borderRadius: 6, border: '1px solid var(--border)',
    backgroundColor: 'var(--input-bg)', color: 'var(--text)', fontSize: 13, width: '100%',
    boxSizing: 'border-box',
  };
  const labelStyle = { fontSize: 11, color: 'var(--text-muted)', marginBottom: 4, display: 'block' };

  const handleSubmit = async () => {
    if (!sku.trim()) {
      onToast?.('SKU is required', 'error');
      return;
    }
    setSubmitting(true);
    try {
      const res = await api.createDraftProduct({
        vendor: vendor.vendor,
        sku: sku.trim(),
        description: description.trim(),
        cost_foreign: parseFloat(costForeign) || 0,
        cost_cad: parseFloat(costCad) || 0,
        sale_price_cad: parseFloat(salePriceCad) || 0,
        map_cad: parseFloat(mapCad) || null,
        barcode: barcode.trim() || null,
        product_url: productUrl.trim(),
      });
      let msg = 'Draft created: ' + (res.title || sku);
      if (res.enriched) msg += ' (AI enriched)';
      if (res.publications_published > 0) msg += ` · published to ${res.publications_published} channel${res.publications_published === 1 ? '' : 's'}`;
      if (res.publication_error) msg += ' | Sales channels NOT set: ' + res.publication_error;
      if (res.pricing_warning) msg += ' | ' + res.pricing_warning;
      const isError = res.pricing_warning || res.publication_error;
      onToast?.(msg, isError ? 'error' : 'success');
      onClose();
    } catch (err) {
      onToast?.('Draft failed: ' + err.message, 'error');
    }
    setSubmitting(false);
  };

  return (
    <div style={{
      position: 'fixed', top: 0, left: 0, right: 0, bottom: 0,
      backgroundColor: 'rgba(0,0,0,0.5)', zIndex: 1000,
      display: 'flex', alignItems: 'center', justifyContent: 'center',
    }} onClick={onClose}>
      <div style={{
        backgroundColor: 'var(--card-bg)', borderRadius: 12, padding: 24,
        width: 520, maxHeight: '85vh', overflowY: 'auto',
        boxShadow: '0 8px 32px rgba(0,0,0,0.3)',
      }} onClick={e => e.stopPropagation()}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 4 }}>
          <div style={{ fontSize: 16, fontWeight: 600 }}>
            Create Draft Product — {vendor.vendor}
          </div>
          <button onClick={onClose}
            style={{ border: 'none', background: 'none', cursor: 'pointer', fontSize: 18, color: 'var(--text-muted)' }}>✕</button>
        </div>
        <div style={{ fontSize: 11, color: 'var(--text-muted)', marginBottom: 16 }}>
          Pricing fields are optional. If you paste a product URL, the backend
          will pull the title and description using this vendor's scrape config.
        </div>

        <div style={{ marginBottom: 12 }}>
          <label style={labelStyle}>Vendor Product URL</label>
          <input type="url" value={productUrl}
            onChange={e => setProductUrl(e.target.value)}
            placeholder="https://vendor.com/products/..."
            style={inputStyle}
            autoFocus />
        </div>

        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12, marginBottom: 12 }}>
          <div>
            <label style={labelStyle}>SKU <span style={{ color: '#ef4444' }}>*</span></label>
            <input type="text" value={sku}
              onChange={e => setSku(e.target.value)}
              placeholder="e.g. ABC-123"
              style={inputStyle} />
          </div>
          <div>
            <label style={labelStyle}>Description</label>
            <input type="text" value={description}
              onChange={e => setDescription(e.target.value)}
              placeholder="(optional — AI will fill from URL)"
              style={inputStyle} />
          </div>
        </div>

        <div style={{ fontSize: 11, color: 'var(--text-muted)', marginTop: 4, marginBottom: 6, fontWeight: 600 }}>
          Pricing (optional)
        </div>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr 1fr', gap: 12, marginBottom: 12 }}>
          <div>
            <label style={labelStyle}>Cost ({currency})</label>
            <input type="number" step="0.01" min="0" value={costForeign}
              onChange={e => setCostForeign(e.target.value)}
              placeholder="0.00"
              style={inputStyle} />
          </div>
          <div>
            <label style={labelStyle}>Cost (CAD)</label>
            <input type="number" step="0.01" min="0" value={costCad}
              onChange={e => setCostCad(e.target.value)}
              placeholder="0.00"
              style={inputStyle} />
          </div>
          <div>
            <label style={labelStyle}>Sale Price (CAD)</label>
            <input type="number" step="0.01" min="0" value={salePriceCad}
              onChange={e => setSalePriceCad(e.target.value)}
              placeholder="0.00"
              style={inputStyle} />
          </div>
          <div>
            <label style={labelStyle} title="Vendor's Canadian MAP. Used as the listing price when set, overriding Sale Price.">CAD MAP</label>
            <input type="number" step="0.01" min="0" value={mapCad}
              onChange={e => setMapCad(e.target.value)}
              placeholder="0.00"
              style={inputStyle} />
          </div>
        </div>

        <div style={{ marginBottom: 20 }}>
          <label style={labelStyle}>Barcode / UPC (optional)</label>
          <input type="text" value={barcode}
            onChange={e => setBarcode(e.target.value)}
            placeholder="e.g. 810098970754"
            style={inputStyle} />
        </div>

        <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8 }}>
          <button onClick={onClose} disabled={submitting}
            style={{
              padding: '8px 16px', borderRadius: 6, border: '1px solid var(--border)',
              cursor: 'pointer', backgroundColor: 'transparent', color: 'var(--text)', fontSize: 13,
            }}>Cancel</button>
          <button onClick={handleSubmit} disabled={submitting || !sku.trim()}
            style={{
              padding: '8px 16px', borderRadius: 6, border: 'none',
              cursor: submitting || !sku.trim() ? 'not-allowed' : 'pointer',
              backgroundColor: '#22c55e', color: '#fff', fontWeight: 600, fontSize: 13,
              opacity: submitting || !sku.trim() ? 0.6 : 1,
            }}>{submitting ? 'Creating...' : 'Create Draft'}</button>
        </div>
      </div>
    </div>
  );
}

// --- Vendor Sale Upload Modal ---
// Two-step flow: pick a CSV/XLSX → detect columns → user maps SKU + sale-cost
// columns and supplies the sale window (or per-row date columns), then
// uploads. Below the form, lists current/future/expired sale rows for this
// vendor with a delete button per row.
function VendorSaleModal({ vendor, onClose, onToast }) {
  const [file, setFile] = useState(null);
  const [columns, setColumns] = useState([]);
  const [detecting, setDetecting] = useState(false);

  const [skuColumn, setSkuColumn] = useState('');
  const [saleCostColumn, setSaleCostColumn] = useState('');
  const [startsAtColumn, setStartsAtColumn] = useState('');
  const [endsAtColumn, setEndsAtColumn] = useState('');
  const [defaultStartsAt, setDefaultStartsAt] = useState('');
  const [defaultEndsAt, setDefaultEndsAt] = useState('');
  const [saleCurrency, setSaleCurrency] = useState(vendor?.invoice_currency || 'USD');
  const [submitting, setSubmitting] = useState(false);

  const [sales, setSales] = useState([]);
  const [loadingSales, setLoadingSales] = useState(false);

  const inputStyle = {
    padding: '6px 10px', borderRadius: 6, border: '1px solid var(--border)',
    backgroundColor: 'var(--input-bg)', color: 'var(--text)', fontSize: 13, width: '100%',
    boxSizing: 'border-box',
  };
  const labelStyle = { fontSize: 11, color: 'var(--text-muted)', marginBottom: 4, display: 'block' };

  const loadSales = async () => {
    setLoadingSales(true);
    try {
      const res = await api.listVendorSales(vendor.vendor);
      setSales(res.sales || []);
    } catch (err) {
      onToast?.('Failed to load sales: ' + err.message, 'error');
    }
    setLoadingSales(false);
  };

  useEffect(() => { loadSales(); }, [vendor?.vendor]);

  const onFileChange = async (f) => {
    setFile(f);
    setColumns([]);
    setSkuColumn(''); setSaleCostColumn('');
    setStartsAtColumn(''); setEndsAtColumn('');
    if (!f) return;
    setDetecting(true);
    try {
      const res = await api.detectPricelistColumns(f);  // shared endpoint — detects any CSV/XLSX
      setColumns(res.columns || []);
    } catch (err) {
      onToast?.('Could not read columns: ' + err.message, 'error');
    }
    setDetecting(false);
  };

  const handleUpload = async () => {
    if (!file) { onToast?.('Pick a file first', 'error'); return; }
    if (!skuColumn || !saleCostColumn) { onToast?.('SKU and sale cost columns are required', 'error'); return; }
    const usingColumnDates = !!(startsAtColumn && endsAtColumn);
    if (!usingColumnDates && (!defaultStartsAt || !defaultEndsAt)) {
      onToast?.('Provide either both date columns OR both default dates', 'error'); return;
    }
    setSubmitting(true);
    try {
      const res = await api.uploadVendorSale(vendor.vendor, file, {
        skuColumn,
        saleCostColumn,
        saleCurrency,
        startsAtColumn: usingColumnDates ? startsAtColumn : undefined,
        endsAtColumn: usingColumnDates ? endsAtColumn : undefined,
        defaultStartsAt: !usingColumnDates ? defaultStartsAt : undefined,
        defaultEndsAt: !usingColumnDates ? defaultEndsAt : undefined,
      });
      onToast?.(`Sale uploaded: ${res.inserted} item(s) added (${res.skipped} skipped)`, 'success');
      setFile(null); setColumns([]);
      setSkuColumn(''); setSaleCostColumn('');
      setStartsAtColumn(''); setEndsAtColumn('');
      setDefaultStartsAt(''); setDefaultEndsAt('');
      loadSales();
    } catch (err) {
      onToast?.('Upload failed: ' + err.message, 'error');
    }
    setSubmitting(false);
  };

  const handleDelete = async (saleId) => {
    if (!confirm('Delete this sale row?')) return;
    try {
      await api.deleteVendorSale(vendor.vendor, saleId);
      loadSales();
    } catch (err) {
      onToast?.('Delete failed: ' + err.message, 'error');
    }
  };

  const handleClearExpired = async () => {
    if (!confirm('Delete all expired sale rows for ' + vendor.vendor + '?')) return;
    try {
      const res = await api.clearVendorSales(vendor.vendor, true);
      onToast?.(`Removed ${res.deleted} expired row(s)`, 'success');
      loadSales();
    } catch (err) {
      onToast?.('Clear failed: ' + err.message, 'error');
    }
  };

  // Classify each sale row by its date window relative to now
  const now = new Date();
  const classify = (s) => {
    const start = new Date(s.starts_at);
    const end = new Date(s.ends_at);
    if (end < now) return 'expired';
    if (start > now) return 'future';
    return 'active';
  };
  const fmtDt = (iso) => {
    if (!iso) return '—';
    try { return new Date(iso).toLocaleString(); } catch { return iso; }
  };

  return (
    <div style={{
      position: 'fixed', top: 0, left: 0, right: 0, bottom: 0,
      backgroundColor: 'rgba(0,0,0,0.5)', zIndex: 1000,
      display: 'flex', alignItems: 'center', justifyContent: 'center',
    }} onClick={onClose}>
      <div style={{
        backgroundColor: 'var(--card-bg)', borderRadius: 12, padding: 24,
        width: 720, maxHeight: '90vh', overflowY: 'auto',
        boxShadow: '0 8px 32px rgba(0,0,0,0.3)',
      }} onClick={e => e.stopPropagation()}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 4 }}>
          <div style={{ fontSize: 16, fontWeight: 600 }}>Vendor Sales — {vendor.vendor}</div>
          <button onClick={onClose}
            style={{ border: 'none', background: 'none', cursor: 'pointer', fontSize: 18, color: 'var(--text-muted)' }}>✕</button>
        </div>
        <div style={{ fontSize: 11, color: 'var(--text-muted)', marginBottom: 18 }}>
          Upload a CSV / XLSX of SKUs on temporary promo pricing. Active sales are
          used as the default unit cost when adding items to a PO and feed into the
          replenishment forecast profit calculation.
        </div>

        {/* ── Upload form ─────────────────────────────────── */}
        <div style={{ border: '1px solid var(--border)', borderRadius: 8, padding: 14, marginBottom: 16 }}>
          <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 10 }}>Upload sale list</div>
          <div style={{ marginBottom: 10 }}>
            <label style={labelStyle}>CSV or XLSX file</label>
            <input type="file" accept=".csv,.xlsx,.xls"
              onChange={e => onFileChange(e.target.files?.[0] || null)} />
            {detecting && <span style={{ fontSize: 11, color: 'var(--text-muted)', marginLeft: 8 }}>Reading columns…</span>}
          </div>

          {columns.length > 0 && (
            <>
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 12, marginBottom: 10 }}>
                <div>
                  <label style={labelStyle}>SKU column <span style={{ color: '#ef4444' }}>*</span></label>
                  <select value={skuColumn} onChange={e => setSkuColumn(e.target.value)} style={inputStyle}>
                    <option value="">— pick —</option>
                    {columns.map(c => <option key={c} value={c}>{c}</option>)}
                  </select>
                </div>
                <div>
                  <label style={labelStyle}>Sale cost column <span style={{ color: '#ef4444' }}>*</span></label>
                  <select value={saleCostColumn} onChange={e => setSaleCostColumn(e.target.value)} style={inputStyle}>
                    <option value="">— pick —</option>
                    {columns.map(c => <option key={c} value={c}>{c}</option>)}
                  </select>
                </div>
                <div>
                  <label style={labelStyle}>Sale currency</label>
                  <select value={saleCurrency} onChange={e => setSaleCurrency(e.target.value)} style={inputStyle}>
                    <option value="USD">USD</option>
                    <option value="CAD">CAD</option>
                    <option value="EUR">EUR</option>
                    <option value="GBP">GBP</option>
                    <option value="CNY">CNY</option>
                  </select>
                </div>
              </div>

              <div style={{ fontSize: 11, color: 'var(--text-muted)', marginBottom: 6, fontWeight: 600 }}>
                Sale window — pick ONE option below
              </div>

              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12, marginBottom: 10 }}>
                <div>
                  <label style={labelStyle}>Start date column (per-row)</label>
                  <select value={startsAtColumn} onChange={e => setStartsAtColumn(e.target.value)} style={inputStyle}>
                    <option value="">— none —</option>
                    {columns.map(c => <option key={c} value={c}>{c}</option>)}
                  </select>
                </div>
                <div>
                  <label style={labelStyle}>End date column (per-row)</label>
                  <select value={endsAtColumn} onChange={e => setEndsAtColumn(e.target.value)} style={inputStyle}>
                    <option value="">— none —</option>
                    {columns.map(c => <option key={c} value={c}>{c}</option>)}
                  </select>
                </div>
              </div>

              <div style={{ textAlign: 'center', fontSize: 10, color: 'var(--text-muted)', margin: '4px 0 8px' }}>— OR —</div>

              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12, marginBottom: 12 }}>
                <div>
                  <label style={labelStyle}>Default start (whole sheet)</label>
                  <input type="datetime-local" value={defaultStartsAt}
                    onChange={e => setDefaultStartsAt(e.target.value)}
                    style={inputStyle} />
                </div>
                <div>
                  <label style={labelStyle}>Default end (whole sheet)</label>
                  <input type="datetime-local" value={defaultEndsAt}
                    onChange={e => setDefaultEndsAt(e.target.value)}
                    style={inputStyle} />
                </div>
              </div>

              <div style={{ display: 'flex', justifyContent: 'flex-end' }}>
                <button onClick={handleUpload} disabled={submitting || !skuColumn || !saleCostColumn}
                  style={{
                    padding: '7px 16px', borderRadius: 6, border: 'none',
                    cursor: (submitting || !skuColumn || !saleCostColumn) ? 'not-allowed' : 'pointer',
                    backgroundColor: '#22c55e', color: '#fff', fontWeight: 600, fontSize: 13,
                    opacity: (submitting || !skuColumn || !saleCostColumn) ? 0.6 : 1,
                  }}>
                  {submitting ? 'Uploading…' : 'Upload sale list'}
                </button>
              </div>
            </>
          )}
        </div>

        {/* ── Existing sales list ────────────────────────────── */}
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
          <div style={{ fontSize: 13, fontWeight: 600 }}>
            All sales for {vendor.vendor} ({sales.length})
          </div>
          {sales.some(s => classify(s) === 'expired') && (
            <button onClick={handleClearExpired}
              style={{
                padding: '4px 10px', borderRadius: 4, border: '1px solid var(--border)',
                cursor: 'pointer', backgroundColor: 'transparent', color: '#dc2626', fontSize: 11,
              }}>
              Clear expired
            </button>
          )}
        </div>
        <div style={{ maxHeight: 280, overflowY: 'auto', border: '1px solid var(--border)', borderRadius: 6 }}>
          {loadingSales ? (
            <div style={{ padding: 14, fontSize: 12, color: 'var(--text-muted)' }}>Loading…</div>
          ) : sales.length === 0 ? (
            <div style={{ padding: 14, fontSize: 12, color: 'var(--text-muted)' }}>No sales uploaded yet.</div>
          ) : (
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 11 }}>
              <thead>
                <tr style={{ backgroundColor: 'var(--card-bg)', position: 'sticky', top: 0 }}>
                  <th style={{ padding: '6px 8px', textAlign: 'left', borderBottom: '1px solid var(--border)' }}>SKU</th>
                  <th style={{ padding: '6px 8px', textAlign: 'right', borderBottom: '1px solid var(--border)' }}>Sale cost</th>
                  <th style={{ padding: '6px 8px', textAlign: 'left', borderBottom: '1px solid var(--border)' }}>Starts</th>
                  <th style={{ padding: '6px 8px', textAlign: 'left', borderBottom: '1px solid var(--border)' }}>Ends</th>
                  <th style={{ padding: '6px 8px', textAlign: 'center', borderBottom: '1px solid var(--border)' }}>Status</th>
                  <th style={{ padding: '6px 8px', borderBottom: '1px solid var(--border)' }}></th>
                </tr>
              </thead>
              <tbody>
                {sales.map(s => {
                  const status = classify(s);
                  const color = status === 'active' ? '#16a34a' : status === 'future' ? '#6366f1' : '#9ca3af';
                  const bg = status === 'active' ? '#dcfce7' : status === 'future' ? '#eef2ff' : '#f3f4f6';
                  return (
                    <tr key={s.id} style={{ borderBottom: '1px solid var(--border)', opacity: status === 'expired' ? 0.6 : 1 }}>
                      <td style={{ padding: '5px 8px', fontFamily: 'monospace' }}>{s.supplier_sku}</td>
                      <td style={{ padding: '5px 8px', textAlign: 'right' }}>{s.sale_currency} {Number(s.sale_cost).toFixed(2)}</td>
                      <td style={{ padding: '5px 8px' }}>{fmtDt(s.starts_at)}</td>
                      <td style={{ padding: '5px 8px' }}>{fmtDt(s.ends_at)}</td>
                      <td style={{ padding: '5px 8px', textAlign: 'center' }}>
                        <span style={{
                          padding: '2px 6px', borderRadius: 8, backgroundColor: bg, color, fontWeight: 600, fontSize: 9, textTransform: 'uppercase',
                        }}>{status}</span>
                      </td>
                      <td style={{ padding: '5px 8px', textAlign: 'right' }}>
                        <button onClick={() => handleDelete(s.id)}
                          title="Delete this sale row"
                          style={{ border: 'none', background: 'none', cursor: 'pointer', color: '#dc2626', fontSize: 12 }}>
                          ✕
                        </button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          )}
        </div>
      </div>
    </div>
  );
}

function InlineVendorRow({ vendor, onSave, onPricelist, onRecompare, onScrapeConfig, onSkuMappings, onCreateDraft, onUploadSale, rowBg }) {
  const [saving, setSaving] = useState(false);
  const [local, setLocal] = useState({ ...vendor });

  // Auto-save on change for dropdowns and toggles
  const saveField = async (updates) => {
    const merged = { ...local, ...updates };
    setLocal(merged);
    setSaving(true);
    try {
      await api.upsertVendor({
        vendor: merged.vendor,
        lead_time_days: merged.lead_time_days,
        min_order_value: merged.min_order_value || null,
        notes: merged.notes || null,
        invoice_currency: merged.invoice_currency,
        enforces_map: merged.enforces_map,
        default_markup_pct: merged.default_markup_pct != null && merged.default_markup_pct !== '' ? parseFloat(merged.default_markup_pct) : null,
        website_url: merged.website_url || null,
        pricelist_release_date: merged.pricelist_release_date || null,
        requires_barcode_labels: !!merged.requires_barcode_labels,
        po_reminder: merged.po_reminder || null,
        // Typing a reminder re-arms it, so an edited message always shows
        // again even if it had previously been dismissed.
        po_reminder_active: merged.po_reminder_active !== false,
      });
      onSave();
    } catch (err) {
      console.error('Save failed:', err);
    }
    setSaving(false);
  };

  return (
    <tr style={{ backgroundColor: rowBg, opacity: saving ? 0.6 : 1 }}>
      <td style={{ padding: '6px 10px', fontWeight: 600, whiteSpace: 'nowrap' }}>{vendor.vendor}</td>
      <td style={{ padding: '4px 6px', textAlign: 'center' }}>
        <select value={local.invoice_currency} onChange={e => saveField({ invoice_currency: e.target.value })}
          style={{ ...cellInput, width: 65, textAlign: 'center' }}>
          <option value="CAD">CAD</option>
          <option value="USD">USD</option>
          <option value="EUR">EUR</option>
          <option value="GBP">GBP</option>
          <option value="CNY">CNY</option>
        </select>
      </td>
      <td style={{ padding: '4px 6px', textAlign: 'center' }}>
        <input type="checkbox" checked={local.enforces_map}
          onChange={e => saveField({ enforces_map: e.target.checked })}
          style={{ cursor: 'pointer', width: 16, height: 16 }} />
      </td>
      <td style={{ padding: '4px 6px', textAlign: 'center' }}
          title="Auto-print barcode labels on receive. Enable for vendors whose products arrive without scannable UPC/EAN stickers.">
        <input type="checkbox" checked={!!local.requires_barcode_labels}
          onChange={e => saveField({ requires_barcode_labels: e.target.checked })}
          style={{ cursor: 'pointer', width: 16, height: 16 }} />
      </td>
      <td style={{ padding: '4px 6px' }}
          title="Shown as a popup whenever a PO is created for this vendor — e.g. 'Don't forget the T-ring adapters'. Leave blank for no reminder.">
        <div style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
          <input type="text" placeholder="-"
            value={local.po_reminder || ''}
            onChange={e => setLocal(l => ({ ...l, po_reminder: e.target.value }))}
            onBlur={() => saveField({ po_reminder: local.po_reminder })}
            onKeyDown={e => e.key === 'Enter' && e.target.blur()}
            style={{ ...cellInput, width: 150 }} />
          {local.po_reminder && local.po_reminder_active === false && (
            <span title="Reminder is dismissed — click to re-enable"
              onClick={() => saveField({ po_reminder_active: true })}
              style={{ cursor: 'pointer', fontSize: 11, color: 'var(--text-muted)',
                       border: '1px solid var(--border)', borderRadius: 4, padding: '1px 5px' }}>
              off
            </span>
          )}
        </div>
      </td>
      <td style={{ padding: '4px 6px', textAlign: 'right' }}>
        <input type="number" step="1" placeholder="-"
          value={local.default_markup_pct != null ? local.default_markup_pct : ''}
          onChange={e => setLocal(l => ({ ...l, default_markup_pct: e.target.value }))}
          onBlur={() => saveField({ default_markup_pct: local.default_markup_pct })}
          onKeyDown={e => e.key === 'Enter' && e.target.blur()}
          disabled={local.enforces_map}
          style={{ ...cellInput, width: 55, textAlign: 'right', opacity: local.enforces_map ? 0.3 : 1 }} />
      </td>
      <td style={{ padding: '4px 6px', textAlign: 'right' }}>
        <input type="number" step="1" min="1"
          value={local.lead_time_days}
          onChange={e => setLocal(l => ({ ...l, lead_time_days: parseInt(e.target.value) || 14 }))}
          onBlur={() => saveField({ lead_time_days: local.lead_time_days })}
          onKeyDown={e => e.key === 'Enter' && e.target.blur()}
          style={{ ...cellInput, width: 45, textAlign: 'right' }} />
      </td>
      <td style={{ padding: '6px 10px', textAlign: 'right' }}>{vendor.total_skus}</td>
      <td style={{ padding: '6px 10px', textAlign: 'right' }}>{fmt(vendor.inventory_value)}</td>
      <td style={{ padding: '6px 10px', textAlign: 'right' }}>{fmt(vendor.revenue_365d)}</td>
      <td style={{ padding: '4px 6px' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
          <input type="url" placeholder="https://..."
            value={local.website_url || ''}
            onChange={e => setLocal(l => ({ ...l, website_url: e.target.value }))}
            onBlur={() => saveField({ website_url: local.website_url })}
            onKeyDown={e => e.key === 'Enter' && e.target.blur()}
            style={{ ...cellInput, width: 160, fontSize: 11 }} />
          {local.website_url && (
            <a href={local.website_url} target="_blank" rel="noopener noreferrer"
              style={{ color: '#6366f1', fontSize: 13, flexShrink: 0 }}
              onClick={e => e.stopPropagation()}>
              ↗
            </a>
          )}
        </div>
      </td>
      <td style={{ padding: '4px 6px', textAlign: 'center' }}>
        {/* Vendor-published release date for this pricelist (e.g. the
            "April 2026" of a Sky-Watcher monthly pricelist). Distinct from
            our upload date — surfaced here so the user can see at a glance
            when the supplier last issued a new list.
            Inline-editable; saves on blur. */}
        <input type="date"
          value={local.pricelist_release_date || ''}
          onChange={e => setLocal(l => ({ ...l, pricelist_release_date: e.target.value }))}
          onBlur={() => saveField({ pricelist_release_date: local.pricelist_release_date || null })}
          onKeyDown={e => e.key === 'Enter' && e.target.blur()}
          title="Vendor's published pricelist release date"
          style={{
            ...cellInput,
            width: 130,
            textAlign: 'center',
            color: local.pricelist_release_date ? 'var(--text)' : 'var(--text-muted)',
          }} />
      </td>
      <td style={{ padding: '6px 10px', textAlign: 'center' }}>
        {vendor.pricelist_uploaded_at ? (
          <div style={{ display: 'flex', alignItems: 'center', gap: 6, justifyContent: 'center' }}>
            <span style={{ fontSize: 11, color: 'var(--text-muted)' }}>
              {new Date(vendor.pricelist_uploaded_at).toLocaleDateString()}
            </span>
            <button onClick={() => api.downloadPricelist(vendor.vendor).catch(() => {})}
              title="Download stored pricelist"
              style={{ border: 'none', background: 'none', cursor: 'pointer', fontSize: 12, color: '#6366f1', padding: 0 }}>
              ⬇
            </button>
          </div>
        ) : <span style={{ color: 'var(--text-muted)' }}>-</span>}
      </td>
      <td style={{ padding: '4px 10px', textAlign: 'center', whiteSpace: 'nowrap' }}>
        <button onClick={() => onPricelist(vendor)} style={{
          padding: '4px 10px', borderRadius: 4, border: '1px solid var(--border)',
          cursor: 'pointer', backgroundColor: 'transparent', color: 'var(--text)', fontSize: 11, marginRight: 4,
        }}>Upload</button>
        {vendor.pricelist_uploaded_at && (
          <button onClick={() => onRecompare(vendor)} title="Re-compare stored pricelist against current Shopify data" style={{
            padding: '4px 10px', borderRadius: 4, border: '1px solid var(--border)',
            cursor: 'pointer', backgroundColor: 'transparent', color: '#6366f1', fontSize: 11,
          }}>Compare</button>
        )}
        <button onClick={() => onScrapeConfig?.(vendor)} title="Configure website scraping for draft product enrichment" style={{
          padding: '4px 10px', borderRadius: 4, border: '1px solid var(--border)',
          cursor: 'pointer', backgroundColor: 'transparent', color: '#8b5cf6', fontSize: 11, marginLeft: 4,
        }}>🔍</button>
        <button onClick={() => onSkuMappings?.(vendor)} title="Manage vendor SKU → TC SKU mappings" style={{
          padding: '4px 10px', borderRadius: 4, border: '1px solid var(--border)',
          cursor: 'pointer', backgroundColor: 'transparent', color: '#6366f1', fontSize: 11, marginLeft: 4,
        }}>🔗</button>
        <button onClick={() => onCreateDraft?.(vendor)} title="Create a new Shopify draft product from a vendor URL" style={{
          padding: '4px 10px', borderRadius: 4, border: '1px solid var(--border)',
          cursor: 'pointer', backgroundColor: 'transparent', color: '#22c55e', fontSize: 11, marginLeft: 4,
        }}>📝</button>
        <button onClick={() => onUploadSale?.(vendor)} title="Upload vendor sale list / promotional pricing" style={{
          padding: '4px 10px', borderRadius: 4, border: '1px solid var(--border)',
          cursor: 'pointer', backgroundColor: 'transparent', color: '#f59e0b', fontSize: 11, marginLeft: 4,
        }}>🏷️</button>
      </td>
    </tr>
  );
}

// --- MAIN PAGE ---

export default function VendorManagementPage({ onToast }) {
  const [vendors, setVendors] = useState([]);
  const [loading, setLoading] = useState(true);
  const [pricelistVendor, setPricelistVendor] = useState(null);
  const [preloadedResult, setPreloadedResult] = useState(null);
  const [sortField, setSortField] = useState('inventory_value');
  const [sortDir, setSortDir] = useState('desc');
  const [scrapeConfigVendor, setScrapeConfigVendor] = useState(null);
  const [skuMappingsVendor, setSkuMappingsVendor] = useState(null);
  const [createDraftVendor, setCreateDraftVendor] = useState(null);
  const [saleVendor, setSaleVendor] = useState(null);

  const loadVendors = async () => {
    setLoading(true);
    try {
      const data = await api.fetchVendors();
      setVendors(data);
    } catch (err) {
      onToast?.('Failed to load vendors: ' + err.message, 'error');
    }
    setLoading(false);
  };

  useEffect(() => { loadVendors(); }, []);

  const handleRecompare = async (vendor) => {
    onToast?.('Re-comparing pricelist...', 'success');
    try {
      const result = await api.recomparePricelist(vendor.vendor);
      setPreloadedResult(result);
      setPricelistVendor(vendor);
    } catch (err) {
      onToast?.('Re-compare failed: ' + err.message, 'error');
    }
  };

  const sorted = useMemo(() => {
    return [...vendors].sort((a, b) => {
      const av = a[sortField] ?? 0;
      const bv = b[sortField] ?? 0;
      if (typeof av === 'string') return sortDir === 'asc' ? av.localeCompare(bv) : bv.localeCompare(av);
      return sortDir === 'asc' ? (av > bv ? 1 : -1) : (av < bv ? 1 : -1);
    });
  }, [vendors, sortField, sortDir]);

  const toggleSort = (field) => {
    if (sortField === field) setSortDir(d => d === 'asc' ? 'desc' : 'asc');
    else { setSortField(field); setSortDir('desc'); }
  };

  if (loading) {
    return <div style={{ padding: 40, textAlign: 'center', color: 'var(--text-muted)' }}>Loading vendors...</div>;
  }

  return (
    <div style={{ padding: 24, maxWidth: 1600 }}>
      <div style={{ marginBottom: 20 }}>
        <h1 style={{ fontSize: 20, fontWeight: 700, margin: 0 }}>Vendor Management</h1>
        <div style={{ fontSize: 12, color: 'var(--text-muted)', marginTop: 4 }}>
          Edit settings directly in the table. Changes save automatically.
        </div>
      </div>

      <FxRatePanel onToast={onToast} />

      <div style={{
        backgroundColor: 'var(--card-bg)', border: '1px solid var(--border)',
        borderRadius: 8, overflow: 'hidden',
      }}>
        <div style={{ overflowX: 'auto' }}>
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
            <thead>
              <tr>
                {[
                  { key: 'vendor', label: 'Vendor', align: 'left' },
                  { key: 'invoice_currency', label: 'Currency', align: 'center' },
                  { key: 'enforces_map', label: 'MAP', align: 'center' },
                  { key: 'requires_barcode_labels', label: 'Print Labels', align: 'center' },
                  { key: 'po_reminder', label: 'PO Reminder', align: 'left' },
                  { key: 'default_markup_pct', label: 'Markup %', align: 'right' },
                  { key: 'lead_time_days', label: 'Lead Time', align: 'right' },
                  { key: 'total_skus', label: 'SKUs', align: 'right' },
                  { key: 'inventory_value', label: 'Inv Value', align: 'right' },
                  { key: 'revenue_365d', label: 'Revenue', align: 'right' },
                  { key: 'website_url', label: 'Website', align: 'left' },
                  { key: 'pricelist_release_date', label: 'PL Released', align: 'center' },
                  { key: 'pricelist_uploaded_at', label: 'Pricelist', align: 'center' },
                  { key: '_actions', label: '', align: 'center' },
                ].map(col => (
                  <th key={col.key} onClick={() => col.key !== '_actions' && toggleSort(col.key)} style={{
                    padding: '8px 10px', fontSize: 11, fontWeight: 600, color: 'var(--text-muted)',
                    textAlign: col.align, whiteSpace: 'nowrap', borderBottom: '2px solid var(--border)',
                    cursor: col.key !== '_actions' ? 'pointer' : 'default',
                    backgroundColor: sortField === col.key ? 'var(--hover-bg)' : 'transparent',
                  }}>
                    {col.label} {sortField === col.key ? (sortDir === 'asc' ? '↑' : '↓') : ''}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {sorted.map((v, i) => (
                <InlineVendorRow
                  key={v.vendor}
                  vendor={v}
                  rowBg={i % 2 === 0 ? 'transparent' : 'var(--row-alt)'}
                  onSave={() => {}}
                  onPricelist={(vendor) => { setPreloadedResult(null); setPricelistVendor(vendor); }}
                  onRecompare={handleRecompare}
                  onScrapeConfig={(vendor) => setScrapeConfigVendor(prev => prev?.vendor === vendor.vendor ? null : vendor)}
                  onSkuMappings={(vendor) => setSkuMappingsVendor(prev => prev?.vendor === vendor.vendor ? null : vendor)}
                  onCreateDraft={(vendor) => setCreateDraftVendor(vendor)}
                  onUploadSale={(vendor) => setSaleVendor(vendor)}
                />
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {pricelistVendor && (
        <PricelistModal
          vendor={pricelistVendor}
          initialResult={preloadedResult}
          onDone={() => { setPricelistVendor(null); setPreloadedResult(null); loadVendors(); }}
          onClose={() => { setPricelistVendor(null); setPreloadedResult(null); }}
          onToast={onToast}
        />
      )}

      {scrapeConfigVendor && (
        <ScrapeConfigEditor vendor={scrapeConfigVendor.vendor} />
      )}

      {skuMappingsVendor && (
        <SkuMappingsPanel
          vendor={skuMappingsVendor.vendor}
          onToast={onToast}
          onClose={() => setSkuMappingsVendor(null)}
        />
      )}

      {createDraftVendor && (
        <CreateDraftModal
          vendor={createDraftVendor}
          onClose={() => setCreateDraftVendor(null)}
          onToast={onToast}
        />
      )}

      {saleVendor && (
        <VendorSaleModal
          vendor={saleVendor}
          onClose={() => setSaleVendor(null)}
          onToast={onToast}
        />
      )}
    </div>
  );
}
