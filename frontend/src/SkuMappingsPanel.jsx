import { useState, useEffect, useCallback } from 'react';
import * as api from './api';

const s = {
  card: { backgroundColor: 'var(--card-bg, #fff)', border: '1px solid var(--border)', borderRadius: 8, padding: 16, marginTop: 12 },
  label: { fontSize: 11, fontWeight: 600, color: 'var(--text-muted)', display: 'block', marginBottom: 3 },
  input: { padding: '5px 8px', borderRadius: 4, border: '1px solid var(--border)', fontSize: 12, width: '100%', boxSizing: 'border-box' },
  btn: (variant) => ({
    padding: '5px 12px', borderRadius: 4, border: 'none', cursor: 'pointer', fontWeight: 600, fontSize: 11,
    backgroundColor: variant === 'primary' ? '#6366f1' : variant === 'danger' ? '#ef4444' : '#e2e8f0',
    color: variant === 'primary' || variant === 'danger' ? '#fff' : 'var(--text)',
  }),
  th: { padding: '6px 8px', fontSize: 10, fontWeight: 600, color: 'var(--text-muted)', textAlign: 'left', borderBottom: '1px solid var(--border)', position: 'sticky', top: 0, backgroundColor: 'var(--card-bg, #fff)', zIndex: 1 },
  td: { padding: '4px 8px', fontSize: 12, borderBottom: '1px solid var(--border)' },
};

export default function SkuMappingsPanel({ vendor, onToast, onClose }) {
  const [mappings, setMappings] = useState([]);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState('');
  const [editId, setEditId] = useState(null);
  const [editShopifySku, setEditShopifySku] = useState('');

  // Add new
  const [newVendorSku, setNewVendorSku] = useState('');
  const [newShopifySku, setNewShopifySku] = useState('');
  const [saving, setSaving] = useState(false);

  // Bulk import
  const [importFile, setImportFile] = useState(null);
  const [importing, setImporting] = useState(false);
  const [importResult, setImportResult] = useState(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const data = await api.listSkuMappings(vendor, search || null);
      setMappings(data.mappings || []);
    } catch (e) { onToast?.('Failed to load mappings: ' + e.message, 'error'); }
    setLoading(false);
  }, [vendor, search, onToast]);

  useEffect(() => { load(); }, [load]);

  const handleAdd = async () => {
    if (!newVendorSku.trim() || !newShopifySku.trim()) return;
    setSaving(true);
    try {
      await api.upsertSkuMapping(vendor, newVendorSku.trim(), newShopifySku.trim());
      onToast?.('Mapping saved', 'success');
      setNewVendorSku(''); setNewShopifySku('');
      await load();
    } catch (e) { onToast?.('Save failed: ' + e.message, 'error'); }
    setSaving(false);
  };

  const handleSaveEdit = async (m) => {
    if (!editShopifySku.trim()) return;
    try {
      await api.upsertSkuMapping(vendor, m.vendor_sku, editShopifySku.trim());
      onToast?.('Mapping updated', 'success');
      setEditId(null); setEditShopifySku('');
      await load();
    } catch (e) { onToast?.('Update failed: ' + e.message, 'error'); }
  };

  const handleDelete = async (m) => {
    if (!window.confirm(`Delete mapping: ${m.vendor_sku} → ${m.shopify_sku}?`)) return;
    try {
      await api.deleteSkuMapping(m.id);
      onToast?.('Mapping deleted', 'success');
      await load();
    } catch (e) { onToast?.('Delete failed: ' + e.message, 'error'); }
  };

  const handleBulkImport = async () => {
    if (!importFile) return;
    setImporting(true);
    setImportResult(null);
    try {
      const result = await api.bulkImportSkuMappings(vendor, importFile);
      setImportResult(result);
      onToast?.(`Import complete: ${result.added} added, ${result.updated} updated, ${result.skipped_count} skipped`, 'success');
      setImportFile(null);
      await load();
    } catch (e) { onToast?.('Import failed: ' + e.message, 'error'); }
    setImporting(false);
  };

  return (
    <div style={s.card}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 }}>
        <div style={{ fontSize: 14, fontWeight: 700 }}>
          🔗 SKU Mappings — {vendor}
          <span style={{ fontSize: 11, color: 'var(--text-muted)', fontWeight: 400, marginLeft: 8 }}>
            {mappings.length} {mappings.length === 1 ? 'mapping' : 'mappings'}
          </span>
        </div>
        {onClose && <button onClick={onClose} style={{ border: 'none', background: 'none', cursor: 'pointer', fontSize: 14, color: 'var(--text-muted)' }}>✕</button>}
      </div>

      <div style={{ fontSize: 11, color: 'var(--text-muted)', marginBottom: 10 }}>
        Map a vendor's SKU to your TC SKU when they don't match exactly. Saved mappings are applied automatically on future pricelist and invoice uploads.
      </div>

      {/* Add new mapping */}
      <div style={{ display: 'flex', gap: 6, marginBottom: 12, padding: 8, backgroundColor: 'var(--row-alt, #fafbfc)', borderRadius: 4 }}>
        <div style={{ flex: 1 }}>
          <label style={s.label}>Vendor SKU</label>
          <input style={s.input} value={newVendorSku}
            onChange={e => setNewVendorSku(e.target.value)}
            onKeyDown={e => e.key === 'Enter' && handleAdd()}
            placeholder="e.g. V-12345" />
        </div>
        <div style={{ fontSize: 16, alignSelf: 'center', color: 'var(--text-muted)', marginTop: 14 }}>→</div>
        <div style={{ flex: 1 }}>
          <label style={s.label}>TC SKU</label>
          <input style={s.input} value={newShopifySku}
            onChange={e => setNewShopifySku(e.target.value)}
            onKeyDown={e => e.key === 'Enter' && handleAdd()}
            placeholder="e.g. TC-12345" />
        </div>
        <div style={{ alignSelf: 'flex-end' }}>
          <button onClick={handleAdd} disabled={saving || !newVendorSku.trim() || !newShopifySku.trim()}
            style={s.btn('primary')}>
            {saving ? '...' : '+ Add'}
          </button>
        </div>
      </div>

      {/* Bulk import */}
      <details style={{ marginBottom: 12 }}>
        <summary style={{ fontSize: 11, color: 'var(--text-muted)', cursor: 'pointer', fontWeight: 600 }}>
          Bulk Import from CSV/XLSX
        </summary>
        <div style={{ padding: 8, marginTop: 4, backgroundColor: 'var(--row-alt, #fafbfc)', borderRadius: 4 }}>
          <div style={{ fontSize: 10, color: 'var(--text-muted)', marginBottom: 6 }}>
            File must contain columns named <code>vendor_sku</code> and <code>tc_sku</code> (or similar variations).
          </div>
          <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
            <input type="file" accept=".csv,.xlsx,.xls,.tsv"
              onChange={e => setImportFile(e.target.files[0])}
              style={{ fontSize: 11 }} />
            {importFile && (
              <button onClick={handleBulkImport} disabled={importing}
                style={s.btn('primary')}>
                {importing ? 'Importing...' : 'Import'}
              </button>
            )}
          </div>
          {importResult && (
            <div style={{ fontSize: 11, marginTop: 8, padding: 8, backgroundColor: '#fff', borderRadius: 4, border: '1px solid var(--border)' }}>
              <div style={{ fontWeight: 600, marginBottom: 4 }}>
                ✓ {importResult.added} added, {importResult.updated} updated, {importResult.skipped_count} skipped
              </div>
              {importResult.invalid_skus?.length > 0 && (
                <div style={{ color: '#ef4444' }}>
                  Skipped (TC SKU not found in inventory):{' '}
                  {importResult.invalid_skus.map(i => `${i.vendor_sku}→${i.tc_sku}`).join(', ')}
                </div>
              )}
            </div>
          )}
        </div>
      </details>

      {/* Search */}
      <div style={{ marginBottom: 8 }}>
        <input style={{ ...s.input, maxWidth: 300 }}
          placeholder="🔍 Search by vendor SKU or TC SKU..."
          value={search} onChange={e => setSearch(e.target.value)} />
      </div>

      {/* Mappings table */}
      {loading ? (
        <div style={{ padding: 20, textAlign: 'center', fontSize: 12, color: 'var(--text-muted)' }}>Loading...</div>
      ) : mappings.length === 0 ? (
        <div style={{ padding: 20, textAlign: 'center', fontSize: 12, color: 'var(--text-muted)' }}>
          {search ? 'No mappings match your search.' : 'No mappings yet. Add one above or bulk import to get started.'}
        </div>
      ) : (
        <div style={{ maxHeight: 400, overflow: 'auto', border: '1px solid var(--border)', borderRadius: 4 }}>
          <table style={{ width: '100%', borderCollapse: 'collapse' }}>
            <thead>
              <tr>
                <th style={{ ...s.th, width: '22%' }}>Vendor SKU</th>
                <th style={{ ...s.th, width: '22%' }}>→ TC SKU</th>
                <th style={s.th}>Product Title</th>
                <th style={{ ...s.th, width: 100 }}></th>
              </tr>
            </thead>
            <tbody>
              {mappings.map((m, i) => (
                <tr key={m.id} style={{ backgroundColor: i % 2 === 0 ? 'transparent' : 'var(--row-alt, #fafbfc)' }}>
                  <td style={{ ...s.td, fontFamily: 'monospace', fontSize: 11, fontWeight: 500 }}>{m.vendor_sku}</td>
                  <td style={s.td}>
                    {editId === m.id ? (
                      <input style={{ ...s.input, fontSize: 11 }} value={editShopifySku}
                        onChange={e => setEditShopifySku(e.target.value)}
                        onKeyDown={e => e.key === 'Enter' && handleSaveEdit(m)}
                        autoFocus />
                    ) : (
                      <span style={{ fontFamily: 'monospace', fontSize: 11, fontWeight: 500, color: '#6366f1' }}>{m.shopify_sku}</span>
                    )}
                  </td>
                  <td style={{ ...s.td, fontSize: 11, color: 'var(--text-muted)', maxWidth: 300, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                    {m.product_title}
                  </td>
                  <td style={{ ...s.td, textAlign: 'right' }}>
                    {editId === m.id ? (
                      <>
                        <button onClick={() => handleSaveEdit(m)} style={{ ...s.btn('primary'), padding: '3px 8px', fontSize: 10, marginRight: 3 }}>Save</button>
                        <button onClick={() => { setEditId(null); setEditShopifySku(''); }} style={{ ...s.btn(), padding: '3px 8px', fontSize: 10 }}>✕</button>
                      </>
                    ) : (
                      <>
                        <button onClick={() => { setEditId(m.id); setEditShopifySku(m.shopify_sku); }}
                          title="Edit TC SKU" style={{ border: 'none', background: 'none', cursor: 'pointer', fontSize: 12, marginRight: 3 }}>✏️</button>
                        <button onClick={() => handleDelete(m)} title="Delete mapping"
                          style={{ border: 'none', background: 'none', cursor: 'pointer', fontSize: 12 }}>🗑</button>
                      </>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
