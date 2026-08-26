import { useState, useEffect, useCallback } from 'react';
import * as api from './api';

const fmt = (n) => new Intl.NumberFormat('en-CA', { style: 'currency', currency: 'CAD', minimumFractionDigits: 2 }).format(n);
const fmtNum = (n) => new Intl.NumberFormat('en-CA').format(n);


// Dates of the open unfulfilled orders that put this SKU into backorder.
// Each is a link straight to the order in Shopify admin — the usual next
// step is to look at the customer, not just the date.
function BackorderDates({ orders }) {
  const list = Array.isArray(orders) ? orders : [];
  if (!list.length) {
    return (
      <td style={{ padding: '12px', color: 'var(--text-muted)', fontSize: 12 }}
          title="No open unfulfilled order currently holds this SKU — the negative stock may predate the current orders, or be a counting error.">
        —
      </td>
    );
  }

  const fmtDate = (iso) => {
    const d = new Date(iso);
    if (isNaN(d)) return '?';
    return d.toLocaleDateString('en-CA', { month: 'short', day: 'numeric' });
  };
  const daysAgo = (iso) => {
    const d = new Date(iso);
    if (isNaN(d)) return null;
    return Math.floor((Date.now() - d.getTime()) / 86400000);
  };

  const shown = list.slice(0, 3);
  const rest = list.length - shown.length;

  return (
    <td style={{ padding: '12px', whiteSpace: 'nowrap', fontSize: 12 }}>
      {shown.map((o, i) => {
        const age = daysAgo(o.created_at);
        // Anything waiting over a month is worth the eye-catch.
        const colour = age != null && age >= 30 ? '#e74c3c'
          : age != null && age >= 14 ? '#e67e22' : 'var(--green)';
        return (
          <div key={o.order_number + i}>
            <a href={o.admin_url} target="_blank" rel="noopener noreferrer"
               title={`${o.order_number} — ${o.qty} unit${o.qty === 1 ? '' : 's'} unfulfilled`
                      + (age != null ? `, ${age} day${age === 1 ? '' : 's'} ago` : '')}
               style={{ color: colour, textDecoration: 'none', fontWeight: 500 }}>
              {fmtDate(o.created_at)}
              <span style={{ color: 'var(--text-muted)', fontWeight: 400 }}>
                {' '}{o.order_number}
              </span>
              {o.qty > 1 && (
                <span style={{ color: 'var(--text-muted)', fontWeight: 400 }}> ×{o.qty}</span>
              )}
            </a>
          </div>
        );
      })}
      {rest > 0 && (
        <div style={{ color: 'var(--text-muted)' }}
             title={list.slice(3).map(o => `${o.order_number} (${o.qty})`).join(', ')}>
          +{rest} more
        </div>
      )}
    </td>
  );
}


export default function BackordersPage({ onToast, onNavigate }) {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [vendorFilter, setVendorFilter] = useState('');
  const [searchTerm, setSearchTerm] = useState('');
  const [sortField, setSortField] = useState('current_stock');
  const [sortDir, setSortDir] = useState('asc');
  // PO creation from selected backorder items
  const [selected, setSelected] = useState(new Set());
  const [creatingOrder, setCreatingOrder] = useState(false);
  const [vendorSettings, setVendorSettings] = useState([]);

  useEffect(() => {
    // Vendor settings for lead-time → expected date on created POs
    api.getVendors().then(res => setVendorSettings(res.vendors || [])).catch(() => {});
  }, []);

  const fetchData = useCallback(async () => {
    setLoading(true);
    try {
      const result = await api.getBackorders();
      setData(result);
    } catch (err) {
      onToast('Failed to load backorders: ' + err.message, 'error');
    }
    setLoading(false);
  }, [onToast]);

  useEffect(() => { fetchData(); }, [fetchData]);

  const handleRefreshInventory = async () => {
    setRefreshing(true);
    try {
      const result = await api.refreshInventoryOnly();
      onToast(`Inventory refreshed: ${fmtNum(result.rows_updated)} variants updated`);
      fetchData();
    } catch (err) {
      onToast('Refresh failed: ' + err.message, 'error');
    }
    setRefreshing(false);
  };

  const handleSort = (field) => {
    if (sortField === field) setSortDir(d => d === 'asc' ? 'desc' : 'asc');
    else { setSortField(field); setSortDir('asc'); }
  };
  const sortArrow = (field) => sortField === field ? (sortDir === 'asc' ? ' \u2191' : ' \u2193') : '';

  const toggleSelect = (sku) => setSelected(prev => {
    const n = new Set(prev);
    if (n.has(sku)) n.delete(sku); else n.add(sku);
    return n;
  });

  // Create a PO from the selected backorder items. Quantity per line is the
  // SHORTFALL (uncovered backordered units = |net_position|) \u2014 exactly what's
  // needed to cover customer backorders not already on an open PO.
  const handleCreateOrder = async () => {
    if (selected.size === 0 || creatingOrder) return;
    const selectedItems = (data?.items || []).filter(i => selected.has(i.sku));
    if (selectedItems.length === 0) { onToast('No matching items for selection', 'error'); return; }

    // These POs are single-vendor by design \u2014 block mixed selections.
    const vendorSet = new Set(selectedItems.map(i => i.vendor || 'Unknown'));
    if (vendorSet.size > 1) {
      onToast(`Selection spans ${vendorSet.size} vendors (${[...vendorSet].slice(0, 3).join(', ')}\u2026). Select items from a single vendor.`, 'error');
      return;
    }
    const orderVendor = [...vendorSet][0];

    setCreatingOrder(true);
    try {
      const leadTime = vendorSettings.find(v => v.name === orderVendor)?.lead_time_days || 14;
      const expectedDate = new Date(Date.now() + leadTime * 86400000).toISOString().split('T')[0];
      await api.createStockOrder({
        vendor: orderVendor,
        expectedDate,
        notes: 'Created from Backorders page',
        items: selectedItems.map(i => ({
          product_title: i.product_title,
          variant_title: i.variant_title || '',
          sku: i.sku,
          barcode: i.barcode || '',
          ordered_qty: Math.max(1, Math.abs(i.net_position || 0)),
          unit_cost: i.cost || 0,
          unit_price: i.price || 0,
        })),
      });
      onToast(`Stock order created for ${orderVendor} with ${selectedItems.length} backordered item(s)`, 'success');
      setSelected(new Set());
      if (onNavigate) onNavigate('stockorders');
      else fetchData();
    } catch (err) {
      onToast(`Failed to create order: ${err.message}`, 'error');
    }
    setCreatingOrder(false);
  };

  const items = data?.items || [];
  const summary = data?.summary || {};

  // Client-side filtering and sorting
  const vendors = [...new Set(items.map(i => i.vendor).filter(Boolean))].sort();
  const filtered = items.filter(i => {
    if (vendorFilter && i.vendor !== vendorFilter) return false;
    if (searchTerm) {
      const t = searchTerm.toLowerCase();
      if (!(i.product_title || '').toLowerCase().includes(t) && !(i.sku || '').toLowerCase().includes(t)) return false;
    }
    return true;
  }).sort((a, b) => {
    let av = a[sortField] ?? 0, bv = b[sortField] ?? 0;
    if (typeof av === 'string') av = av.toLowerCase();
    if (typeof bv === 'string') bv = bv.toLowerCase();
    return sortDir === 'asc' ? (av < bv ? -1 : av > bv ? 1 : 0) : (av > bv ? -1 : av < bv ? 1 : 0);
  });

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100vh' }}>
      {/* Header */}
      <div style={{
        padding: '14px 24px', borderBottom: '1px solid var(--border)',
        backgroundColor: 'var(--white)', display: 'flex', alignItems: 'center', gap: 12,
      }}>
        <h1 style={{ fontSize: 20, fontWeight: 700, margin: 0 }}>Backorders</h1>
        <span style={{
          fontSize: 12, color: 'var(--text-light)', backgroundColor: '#f0f1f3',
          padding: '4px 10px', borderRadius: 4,
        }}>
          Uncovered customer backorders
        </span>

        <select
          value={vendorFilter}
          onChange={e => { setVendorFilter(e.target.value); setSelected(new Set()); }}
          style={{
            padding: '6px 12px', borderRadius: 6, border: '1px solid var(--border)',
            fontSize: 13, color: vendorFilter ? 'var(--green)' : 'var(--text)',
            fontWeight: vendorFilter ? 600 : 400,
          }}
        >
          <option value="">All Vendors</option>
          {vendors.map(v => <option key={v} value={v}>{v}</option>)}
        </select>

        <div style={{
          display: 'flex', alignItems: 'center', gap: 8,
          padding: '6px 14px', borderRadius: 6, border: '1px solid var(--border)',
          backgroundColor: '#fafafa', maxWidth: 300, flex: 1,
        }}>
          <span style={{ color: 'var(--text-muted)', fontSize: 13 }}>{'\uD83D\uDD0D'}</span>
          <input
            type="text" placeholder="Search products or SKUs..."
            value={searchTerm} onChange={e => setSearchTerm(e.target.value)}
            style={{ border: 'none', outline: 'none', flex: 1, fontSize: 13, backgroundColor: 'transparent' }}
          />
        </div>

        <button
          onClick={handleRefreshInventory}
          disabled={refreshing}
          title="Quick inventory refresh from Shopify (faster than full refresh)"
          style={{
            padding: '6px 14px', borderRadius: 6, border: '1px solid var(--border)',
            backgroundColor: 'transparent', fontSize: 13, display: 'flex', alignItems: 'center', gap: 6,
            cursor: 'pointer',
          }}
        >
          <span style={{ display: 'inline-block', animation: refreshing ? 'spin 1s linear infinite' : 'none' }}>{'\uD83D\uDD04'}</span>
          {refreshing ? 'Refreshing...' : 'Refresh inventory'}
        </button>

        <button
          onClick={handleCreateOrder}
          disabled={selected.size === 0 || creatingOrder}
          title="Create a purchase order from the checked items (single vendor). Quantities = uncovered shortfall."
          style={{
            padding: '6px 16px', borderRadius: 6, border: 'none', fontWeight: 600, fontSize: 13,
            backgroundColor: (selected.size > 0 && !creatingOrder) ? 'var(--green)' : '#ccc',
            color: '#fff', cursor: (selected.size > 0 && !creatingOrder) ? 'pointer' : 'not-allowed',
            display: 'flex', alignItems: 'center', gap: 6, whiteSpace: 'nowrap',
          }}
        >
          {creatingOrder ? 'Creating order\u2026' : `\uFF0B Create PO${selected.size > 0 ? ` (${selected.size})` : ''}`}
        </button>
      </div>

      {/* Summary cards */}
      {!loading && items.length > 0 && (
        <div style={{
          padding: '12px 24px', borderBottom: '1px solid var(--border)',
          backgroundColor: '#fafbfc', display: 'flex', gap: 32, fontSize: 13,
        }}>
          <div>
            <span style={{ color: 'var(--text-light)' }}>Uncovered items: </span>
            <strong style={{ color: '#e74c3c' }}>{filtered.length}</strong>
          </div>
          <div>
            <span style={{ color: 'var(--text-light)' }}>Total backordered units: </span>
            <strong style={{ color: '#e74c3c' }}>{fmtNum(summary.total_backordered_units || 0)}</strong>
          </div>
          <div>
            <span style={{ color: 'var(--text-light)' }}>Uncovered units: </span>
            <strong style={{ color: '#e74c3c' }}>{fmtNum(summary.total_uncovered_units || 0)}</strong>
          </div>
          <div>
            <span style={{ color: 'var(--text-light)' }}>Uncovered retail value: </span>
            <strong style={{ color: '#e74c3c' }}>{fmt(summary.total_retail_value || 0)}</strong>
          </div>
        </div>
      )}

      {/* Table */}
      <div style={{ flex: 1, overflow: 'auto', backgroundColor: 'var(--bg)' }}>
        {loading ? (
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: 300, gap: 12 }}>
            <div className="spinner" />
            <span style={{ color: 'var(--text-light)' }}>Loading backorders...</span>
          </div>
        ) : filtered.length === 0 ? (
          <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', height: 300, gap: 12 }}>
            <span style={{ fontSize: 40 }}>{'\u2705'}</span>
            <span style={{ color: 'var(--text-light)', fontSize: 15 }}>
              {items.length === 0 ? 'No uncovered backorders' : 'No matching items'}
            </span>
            {items.length === 0 && (
              <span style={{ color: 'var(--text-muted)', fontSize: 13 }}>
                All customer backorders are covered by open stock orders
              </span>
            )}
          </div>
        ) : (
          <table style={{ width: '100%', borderCollapse: 'collapse', backgroundColor: 'var(--white)', fontSize: 13 }}>
            <thead style={{ position: 'sticky', top: 0, backgroundColor: 'var(--white)', zIndex: 10 }}>
              <tr style={{ borderBottom: '2px solid var(--border)' }}>
                <th style={{ padding: '10px 12px', width: 36, textAlign: 'center' }}>
                  <input
                    type="checkbox"
                    title="Select all visible items"
                    checked={filtered.length > 0 && filtered.every(i => selected.has(i.sku))}
                    ref={el => { if (el) el.indeterminate = filtered.some(i => selected.has(i.sku)) && !filtered.every(i => selected.has(i.sku)); }}
                    onChange={e => {
                      if (e.target.checked) setSelected(new Set(filtered.map(i => i.sku)));
                      else setSelected(new Set());
                    }}
                  />
                </th>
                <th onClick={() => handleSort('product_title')} style={{ padding: '10px 12px', textAlign: 'left', color: 'var(--text-light)', fontWeight: 500, cursor: 'pointer' }}>Product{sortArrow('product_title')}</th>
                <th onClick={() => handleSort('sku')} style={{ padding: '10px 12px', textAlign: 'left', color: 'var(--text-light)', fontWeight: 500, cursor: 'pointer' }}>SKU{sortArrow('sku')}</th>
                <th onClick={() => handleSort('vendor')} style={{ padding: '10px 12px', textAlign: 'left', color: 'var(--text-light)', fontWeight: 500, cursor: 'pointer' }}>Vendor{sortArrow('vendor')}</th>
                <th onClick={() => handleSort('current_stock')} style={{ padding: '10px 12px', textAlign: 'right', color: 'var(--text-light)', fontWeight: 500, cursor: 'pointer' }}>Available{sortArrow('current_stock')}</th>
                <th onClick={() => handleSort('on_order')} style={{ padding: '10px 12px', textAlign: 'right', color: 'var(--text-light)', fontWeight: 500, cursor: 'pointer' }}>On order{sortArrow('on_order')}</th>
                <th onClick={() => handleSort('net_position')} style={{ padding: '10px 12px', textAlign: 'right', color: 'var(--text-light)', fontWeight: 500, cursor: 'pointer' }}>Net position{sortArrow('net_position')}</th>
                <th style={{ padding: '10px 12px', textAlign: 'right', color: 'var(--text-light)', fontWeight: 500 }}>Shortfall</th>
                <th style={{ padding: '10px 12px', textAlign: 'left', color: 'var(--text-light)', fontWeight: 500 }}
                    title="Open unfulfilled orders holding this SKU. Click a date to open the order in Shopify.">Ordered</th>
                <th onClick={() => handleSort('price')} style={{ padding: '10px 12px', textAlign: 'right', color: 'var(--text-light)', fontWeight: 500, cursor: 'pointer' }}>Price{sortArrow('price')}</th>
                <th onClick={() => handleSort('seasonal_monthly_velocity')} style={{ padding: '10px 12px', textAlign: 'right', color: 'var(--text-light)', fontWeight: 500, cursor: 'pointer' }}>Velocity/mo{sortArrow('seasonal_monthly_velocity')}</th>
                <th onClick={() => handleSort('total_sold_365d')} style={{ padding: '10px 12px', textAlign: 'right', color: 'var(--text-light)', fontWeight: 500, cursor: 'pointer' }}>365d sales{sortArrow('total_sold_365d')}</th>
              </tr>
            </thead>
            <tbody>
              {filtered.map((item, i) => {
                const shortfall = Math.abs(item.net_position || 0);
                return (
                  <tr key={item.sku || i} style={{ borderBottom: '1px solid var(--border)', backgroundColor: selected.has(item.sku) ? '#f0f9f4' : 'transparent' }}
                    onMouseEnter={e => { if (!selected.has(item.sku)) e.currentTarget.style.backgroundColor = '#fafbfc'; }}
                    onMouseLeave={e => { e.currentTarget.style.backgroundColor = selected.has(item.sku) ? '#f0f9f4' : 'transparent'; }}>
                    <td style={{ padding: '12px', textAlign: 'center' }}>
                      <input
                        type="checkbox"
                        checked={selected.has(item.sku)}
                        onChange={() => toggleSelect(item.sku)}
                      />
                    </td>
                    <td style={{ padding: '12px' }}>
                      <div style={{ fontWeight: 500 }}>{item.product_title}</div>
                      {item.variant_title && item.variant_title !== 'Default Title' && (
                        <div style={{ fontSize: 11, color: 'var(--text-muted)' }}>{item.variant_title}</div>
                      )}
                    </td>
                    <td style={{ padding: '12px', color: 'var(--text-light)', fontFamily: 'monospace', fontSize: 12 }}>{item.sku}</td>
                    <td style={{ padding: '12px' }}>{item.vendor}</td>
                    <td style={{ padding: '12px', textAlign: 'right', fontWeight: 700, color: '#e74c3c' }}>
                      {item.current_stock}
                    </td>
                    <td style={{ padding: '12px', textAlign: 'right', color: item.on_order > 0 ? 'var(--green)' : 'var(--text-muted)' }}>
                      {item.on_order}
                    </td>
                    <td style={{ padding: '12px', textAlign: 'right', fontWeight: 600, color: item.net_position < 0 ? '#e74c3c' : 'var(--green)' }}>
                      {item.net_position}
                    </td>
                    <td style={{
                      padding: '12px', textAlign: 'right', fontWeight: 700,
                      color: '#fff', fontSize: 12,
                    }}>
                      <span style={{
                        backgroundColor: shortfall >= 5 ? '#e74c3c' : shortfall >= 2 ? '#e67e22' : '#f39c12',
                        padding: '2px 8px', borderRadius: 10,
                      }}>
                        {shortfall}
                      </span>
                    </td>
                    <BackorderDates orders={item.backorder_orders} />
                    <td style={{ padding: '12px', textAlign: 'right' }}>{fmt(item.price || 0)}</td>
                    <td style={{ padding: '12px', textAlign: 'right' }}>
                      {(item.seasonal_monthly_velocity || 0).toFixed(2)}
                    </td>
                    <td style={{ padding: '12px', textAlign: 'right' }}>{item.total_sold_365d || 0}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </div>

      {/* Bottom bar */}
      <div style={{
        padding: '10px 24px', borderTop: '1px solid var(--border)',
        backgroundColor: 'var(--white)', fontSize: 12, color: 'var(--text-muted)',
      }}>
        Data from velocity cache. Use "Refresh inventory" for a quick stock update, or full Refresh on the Replenishment page for a complete recalculation.
      </div>
    </div>
  );
}
