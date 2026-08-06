import { useState, useEffect, useCallback, Fragment } from 'react';
import * as api from './api';

const fmt = (n) => new Intl.NumberFormat('en-CA', { style: 'currency', currency: 'CAD', minimumFractionDigits: 2 }).format(n);
const fmtDate = (d) => { if (!d) return '—'; try { return new Date(d).toLocaleDateString('en-CA'); } catch { return d; } };

const s = {
  card: { backgroundColor: 'var(--card-bg, #fff)', border: '1px solid var(--border)', borderRadius: 8, padding: '16px 20px', marginBottom: 16 },
  label: { fontSize: 11, fontWeight: 600, color: 'var(--text-muted)', display: 'block', marginBottom: 3 },
  input: { padding: '6px 10px', borderRadius: 6, border: '1px solid var(--border)', fontSize: 13, width: '100%', boxSizing: 'border-box' },
  select: { padding: '6px 10px', borderRadius: 6, border: '1px solid var(--border)', fontSize: 13 },
  btn: (variant) => ({
    padding: '7px 16px', borderRadius: 6, border: 'none', cursor: 'pointer', fontWeight: 600, fontSize: 12,
    backgroundColor: variant === 'primary' ? 'var(--green, #1a7e5a)' : variant === 'danger' ? '#ef4444' : variant === 'ghost' ? 'transparent' : '#e2e8f0',
    color: variant === 'primary' || variant === 'danger' ? '#fff' : 'var(--text)',
    ...(variant === 'ghost' ? { border: '1px solid var(--border)' } : {}),
  }),
  th: { padding: '8px 10px', fontSize: 11, fontWeight: 600, color: 'var(--text-muted)', textAlign: 'left', borderBottom: '2px solid var(--border)', position: 'sticky', top: 0, backgroundColor: 'var(--card-bg, #fff)', zIndex: 1 },
  td: { padding: '8px 10px', fontSize: 13, borderBottom: '1px solid var(--border)' },
};

const TYPE_BADGES = {
  product: { bg: '#dbeafe', color: '#1e40af', label: 'Product' },
  cash: { bg: '#d1fae5', color: '#065f46', label: 'Cash' },
};

const emptyItem = () => ({ type: 'product', sku: '', title: '', value: '', _lookingUp: false });

export default function DonationsPage({ onToast }) {
  const [donations, setDonations] = useState([]);
  const [loading, setLoading] = useState(true);
  const [showForm, setShowForm] = useState(false);
  const [saving, setSaving] = useState(false);
  const [editId, setEditId] = useState(null);
  const [expandedId, setExpandedId] = useState(null);

  // Form state — header + line items
  const [form, setForm] = useState({
    date: new Date().toISOString().slice(0, 10),
    donated_to: '', comments: '',
  });
  const [lineItems, setLineItems] = useState([emptyItem()]);

  const fetchDonations = useCallback(async () => {
    setLoading(true);
    try {
      const data = await api.listDonations();
      setDonations(data.donations || []);
    } catch (e) { onToast?.('Failed to load donations: ' + e.message, 'error'); }
    setLoading(false);
  }, [onToast]);

  useEffect(() => { fetchDonations(); }, [fetchDonations]);

  const resetForm = () => {
    setForm({ date: new Date().toISOString().slice(0, 10), donated_to: '', comments: '' });
    setLineItems([emptyItem()]);
    setEditId(null);
    setShowForm(false);
  };

  const updateLineItem = (idx, field, value) => {
    setLineItems(prev => prev.map((item, i) => i === idx ? { ...item, [field]: value } : item));
  };

  const addLineItem = () => setLineItems(prev => [...prev, emptyItem()]);

  const removeLineItem = (idx) => {
    if (lineItems.length === 1) return;
    setLineItems(prev => prev.filter((_, i) => i !== idx));
  };

  const handleSkuLookup = async (idx) => {
    const sku = lineItems[idx].sku.trim();
    if (!sku) return;
    updateLineItem(idx, '_lookingUp', true);
    try {
      const data = await api.lookupSku(sku);
      if (data.found) {
        setLineItems(prev => prev.map((item, i) => i === idx ? {
          ...item,
          title: data.product_title || '',
          value: data.cost ? String(data.cost) : item.value,
          _lookingUp: false,
        } : item));
        onToast?.(`Found: ${data.product_title}`, 'success');
      } else {
        updateLineItem(idx, '_lookingUp', false);
        onToast?.('SKU not found in Shopify', 'error');
      }
    } catch (e) {
      updateLineItem(idx, '_lookingUp', false);
      onToast?.('Lookup failed: ' + e.message, 'error');
    }
  };

  const handleSave = async () => {
    if (!form.donated_to.trim()) { onToast?.('Please enter who the donation is to', 'error'); return; }
    const validItems = lineItems.filter(item =>
      (item.type === 'product' && (item.sku.trim() || item.title.trim())) ||
      (item.type === 'cash' && item.title.trim())
    );
    if (validItems.length === 0) { onToast?.('Please add at least one line item', 'error'); return; }

    setSaving(true);
    try {
      const payload = {
        ...form,
        items: validItems.map(item => ({
          type: item.type,
          sku: item.type === 'product' ? item.sku.trim() : null,
          title: item.title.trim(),
          value: parseFloat(item.value) || 0,
        })),
      };
      if (editId) {
        await api.updateDonation(editId, payload);
        onToast?.('Donation updated', 'success');
      } else {
        await api.createDonation(payload);
        onToast?.('Donation recorded', 'success');
      }
      resetForm();
      await fetchDonations();
    } catch (e) { onToast?.('Save failed: ' + e.message, 'error'); }
    setSaving(false);
  };

  const handleEdit = (d) => {
    setForm({
      date: d.date ? d.date.slice(0, 10) : '',
      donated_to: d.donated_to || '',
      comments: d.comments || '',
    });
    if (d.items && d.items.length > 0) {
      setLineItems(d.items.map(item => ({
        type: item.type || 'product',
        sku: item.sku || '',
        title: item.title || '',
        value: item.value ? String(item.value) : '',
        _lookingUp: false,
      })));
    } else {
      // Legacy single-item record
      setLineItems([{
        type: d.type || 'product', sku: d.sku || '',
        title: d.title || '', value: d.value ? String(d.value) : '',
        _lookingUp: false,
      }]);
    }
    setEditId(d.id);
    setShowForm(true);
  };

  const handleDelete = async (id) => {
    if (!window.confirm('Delete this donation record and all its line items?')) return;
    try {
      await api.deleteDonation(id);
      onToast?.('Donation deleted', 'success');
      await fetchDonations();
    } catch (e) { onToast?.('Delete failed: ' + e.message, 'error'); }
  };

  const totalValue = donations.reduce((sum, d) => sum + (d.total_value || d.value || 0), 0);
  const totalItems = donations.reduce((sum, d) => sum + (d.item_count || 1), 0);

  return (
    <div style={{ padding: '24px 28px', overflow: 'auto', height: '100vh' }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 20 }}>
        <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>Donations</h1>
        <button style={s.btn('primary')} onClick={() => { resetForm(); setShowForm(true); }}>+ Add Donation</button>
      </div>

      {/* Summary cards */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 12, marginBottom: 20 }}>
        <div style={s.card}>
          <div style={{ fontSize: 11, color: 'var(--text-muted)', fontWeight: 600 }}>Donations</div>
          <div style={{ fontSize: 22, fontWeight: 700, marginTop: 4 }}>{donations.length}</div>
          <div style={{ fontSize: 11, color: 'var(--text-muted)', marginTop: 2 }}>{totalItems} total line items</div>
        </div>
        <div style={s.card}>
          <div style={{ fontSize: 11, color: 'var(--text-muted)', fontWeight: 600 }}>Total Value</div>
          <div style={{ fontSize: 22, fontWeight: 700, marginTop: 4 }}>{fmt(totalValue)}</div>
        </div>
        <div style={s.card}>
          <div style={{ fontSize: 11, color: 'var(--text-muted)', fontWeight: 600 }}>This Year</div>
          <div style={{ fontSize: 22, fontWeight: 700, marginTop: 4 }}>
            {fmt(donations.filter(d => d.date && d.date.startsWith(String(new Date().getFullYear()))).reduce((sum, d) => sum + (d.total_value || d.value || 0), 0))}
          </div>
        </div>
      </div>

      {/* Add/Edit form */}
      {showForm && (
        <div style={s.card}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
            <div style={{ fontSize: 15, fontWeight: 700 }}>{editId ? 'Edit Donation' : 'New Donation'}</div>
            <button onClick={resetForm} style={{ border: 'none', background: 'none', cursor: 'pointer', fontSize: 16, color: 'var(--text-muted)' }}>✕</button>
          </div>

          {/* Header fields */}
          <div style={{ display: 'grid', gridTemplateColumns: '160px 1fr 1fr', gap: 12, marginBottom: 16 }}>
            <div>
              <label style={s.label}>Date</label>
              <input type="date" style={s.input} value={form.date}
                onChange={e => setForm(f => ({ ...f, date: e.target.value }))} />
            </div>
            <div>
              <label style={s.label}>Donated To *</label>
              <input style={s.input} value={form.donated_to}
                onChange={e => setForm(f => ({ ...f, donated_to: e.target.value }))}
                placeholder="e.g. Local astronomy club" />
            </div>
            <div>
              <label style={s.label}>Comments</label>
              <input style={s.input} value={form.comments}
                onChange={e => setForm(f => ({ ...f, comments: e.target.value }))}
                placeholder="Optional notes..." />
            </div>
          </div>

          {/* Line items */}
          <div style={{ fontSize: 13, fontWeight: 700, marginBottom: 8 }}>Line Items</div>
          <div style={{ border: '1px solid var(--border)', borderRadius: 6, overflow: 'hidden', marginBottom: 12 }}>
            <table style={{ width: '100%', borderCollapse: 'collapse' }}>
              <thead>
                <tr style={{ backgroundColor: 'var(--row-alt, #fafbfc)' }}>
                  <th style={{ ...s.th, width: 100, borderBottom: '1px solid var(--border)' }}>Type</th>
                  <th style={{ ...s.th, width: 140, borderBottom: '1px solid var(--border)' }}>SKU</th>
                  <th style={{ ...s.th, borderBottom: '1px solid var(--border)' }}>Title / Description</th>
                  <th style={{ ...s.th, width: 120, borderBottom: '1px solid var(--border)' }}>Value (CAD)</th>
                  <th style={{ ...s.th, width: 40, borderBottom: '1px solid var(--border)' }}></th>
                </tr>
              </thead>
              <tbody>
                {lineItems.map((item, idx) => (
                  <tr key={idx}>
                    <td style={{ padding: '6px 8px', borderBottom: '1px solid var(--border)' }}>
                      <select style={{ ...s.select, width: '100%' }} value={item.type}
                        onChange={e => updateLineItem(idx, 'type', e.target.value)}>
                        <option value="product">📦 Product</option>
                        <option value="cash">💵 Cash</option>
                      </select>
                    </td>
                    <td style={{ padding: '6px 8px', borderBottom: '1px solid var(--border)' }}>
                      {item.type === 'product' ? (
                        <div style={{ display: 'flex', gap: 3 }}>
                          <input style={{ ...s.input, flex: 1, fontSize: 12 }} value={item.sku}
                            onChange={e => updateLineItem(idx, 'sku', e.target.value)}
                            onKeyDown={e => e.key === 'Enter' && handleSkuLookup(idx)}
                            placeholder="SKU" />
                          <button onClick={() => handleSkuLookup(idx)} disabled={item._lookingUp || !item.sku.trim()}
                            style={{ ...s.btn('primary'), padding: '3px 6px', fontSize: 10 }}>
                            {item._lookingUp ? '...' : '🔍'}
                          </button>
                        </div>
                      ) : (
                        <span style={{ fontSize: 11, color: 'var(--text-muted)', padding: '0 8px' }}>—</span>
                      )}
                    </td>
                    <td style={{ padding: '6px 8px', borderBottom: '1px solid var(--border)' }}>
                      <input style={{ ...s.input, fontSize: 12 }} value={item.title}
                        onChange={e => updateLineItem(idx, 'title', e.target.value)}
                        placeholder={item.type === 'product' ? 'Auto-filled from SKU' : 'Description'} />
                    </td>
                    <td style={{ padding: '6px 8px', borderBottom: '1px solid var(--border)' }}>
                      <input type="number" step="0.01" min="0" style={{ ...s.input, fontSize: 12, textAlign: 'right' }}
                        value={item.value} onChange={e => updateLineItem(idx, 'value', e.target.value)}
                        placeholder="0.00" />
                    </td>
                    <td style={{ padding: '6px 8px', borderBottom: '1px solid var(--border)', textAlign: 'center' }}>
                      {lineItems.length > 1 && (
                        <button onClick={() => removeLineItem(idx)} title="Remove"
                          style={{ border: 'none', background: 'none', cursor: 'pointer', fontSize: 14, color: '#ef4444' }}>✕</button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
              <tfoot>
                <tr>
                  <td colSpan={3} style={{ padding: '6px 8px' }}>
                    <button onClick={addLineItem} style={{ ...s.btn('ghost'), fontSize: 11, padding: '4px 12px' }}>+ Add Line Item</button>
                  </td>
                  <td style={{ padding: '6px 8px', textAlign: 'right', fontWeight: 700, fontSize: 13 }}>
                    {fmt(lineItems.reduce((sum, item) => sum + (parseFloat(item.value) || 0), 0))}
                  </td>
                  <td />
                </tr>
              </tfoot>
            </table>
          </div>

          <div style={{ display: 'flex', gap: 8 }}>
            <button style={s.btn('primary')} onClick={handleSave} disabled={saving}>
              {saving ? 'Saving...' : editId ? 'Update Donation' : 'Save Donation'}
            </button>
            <button style={s.btn()} onClick={resetForm}>Cancel</button>
          </div>
        </div>
      )}

      {/* Donations table */}
      <div style={s.card}>
        {loading ? (
          <div style={{ padding: 40, textAlign: 'center', color: 'var(--text-muted)' }}>Loading...</div>
        ) : donations.length === 0 ? (
          <div style={{ padding: 40, textAlign: 'center', color: 'var(--text-muted)' }}>No donations recorded yet. Click "+ Add Donation" to get started.</div>
        ) : (
          <div style={{ maxHeight: 600, overflow: 'auto' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse' }}>
              <thead>
                <tr>
                  <th style={{ ...s.th, width: 20 }}></th>
                  <th style={s.th}>Date</th>
                  <th style={s.th}>Donated To</th>
                  <th style={{ ...s.th, textAlign: 'center' }}>Items</th>
                  <th style={{ ...s.th, textAlign: 'right' }}>Total Value</th>
                  <th style={s.th}>Comments</th>
                  <th style={{ ...s.th, width: 70 }}></th>
                </tr>
              </thead>
              <tbody>
                {donations.map((d, i) => {
                  const isExpanded = expandedId === d.id;
                  const itemCount = d.item_count || (d.items?.length) || 1;
                  const totalVal = d.total_value ?? d.value ?? 0;
                  return (
                    <Fragment key={d.id}>
                      <tr style={{ backgroundColor: i % 2 === 0 ? 'transparent' : 'var(--row-alt, #fafbfc)', cursor: 'pointer' }}
                          onClick={() => setExpandedId(isExpanded ? null : d.id)}>
                        <td style={{ ...s.td, textAlign: 'center', fontSize: 10, color: 'var(--text-muted)' }}>{isExpanded ? '▼' : '▶'}</td>
                        <td style={{ ...s.td, whiteSpace: 'nowrap', fontWeight: 500 }}>{fmtDate(d.date)}</td>
                        <td style={{ ...s.td, fontWeight: 500 }}>{d.donated_to}</td>
                        <td style={{ ...s.td, textAlign: 'center' }}>
                          <span style={{ padding: '2px 8px', borderRadius: 10, fontSize: 11, fontWeight: 600, backgroundColor: '#f0f1f3' }}>{itemCount}</span>
                        </td>
                        <td style={{ ...s.td, textAlign: 'right', fontWeight: 600 }}>{fmt(totalVal)}</td>
                        <td style={{ ...s.td, fontSize: 11, color: 'var(--text-muted)', maxWidth: 200, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{d.comments || ''}</td>
                        <td style={{ ...s.td, textAlign: 'center' }} onClick={e => e.stopPropagation()}>
                          <button onClick={() => handleEdit(d)} title="Edit" style={{ border: 'none', background: 'none', cursor: 'pointer', fontSize: 13, marginRight: 4 }}>✏️</button>
                          <button onClick={() => handleDelete(d.id)} title="Delete" style={{ border: 'none', background: 'none', cursor: 'pointer', fontSize: 13 }}>🗑</button>
                        </td>
                      </tr>
                      {isExpanded && d.items?.length > 0 && d.items.map((item, j) => {
                        const badge = TYPE_BADGES[item.type] || TYPE_BADGES.product;
                        return (
                          <tr key={`${d.id}-${j}`} style={{ backgroundColor: '#f8fafc' }}>
                            <td style={s.td} />
                            <td style={{ ...s.td, paddingLeft: 20 }}>
                              <span style={{ padding: '2px 8px', borderRadius: 4, fontSize: 10, fontWeight: 600, backgroundColor: badge.bg, color: badge.color }}>{badge.label}</span>
                            </td>
                            <td colSpan={2} style={{ ...s.td, fontSize: 12 }}>
                              {item.type === 'product' && item.sku && <span style={{ fontFamily: 'monospace', fontSize: 11, color: 'var(--text-muted)', marginRight: 8 }}>{item.sku}</span>}
                              {item.title}
                            </td>
                            <td style={{ ...s.td, textAlign: 'right' }}>{fmt(item.value || 0)}</td>
                            <td colSpan={2} />
                          </tr>
                        );
                      })}
                    </Fragment>
                  );
                })}
              </tbody>
              <tfoot>
                <tr style={{ borderTop: '2px solid var(--border)' }}>
                  <td />
                  <td colSpan={3} style={{ ...s.td, fontWeight: 700, fontSize: 12 }}>{donations.length} donations · {totalItems} items</td>
                  <td style={{ ...s.td, textAlign: 'right', fontWeight: 700 }}>{fmt(totalValue)}</td>
                  <td colSpan={2} />
                </tr>
              </tfoot>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
