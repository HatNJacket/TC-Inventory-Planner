import { useState, useEffect, useMemo } from 'react';
import * as api from './api';

const fmt = (n) => new Intl.NumberFormat('en-CA', {
  style: 'currency', currency: 'CAD', minimumFractionDigits: 0, maximumFractionDigits: 0,
}).format(n);
const fmtFull = (n) => new Intl.NumberFormat('en-CA', {
  style: 'currency', currency: 'CAD', minimumFractionDigits: 2,
}).format(n);
const fmtNum = (n) => new Intl.NumberFormat('en-CA').format(n);
const fmtPct = (n) => `${n.toFixed(1)}%`;

const ACTION_COLORS = {
  liquidate: { bg: '#fee2e2', fg: '#991b1b', label: 'Liquidate' },
  clearance: { bg: '#fef3c7', fg: '#92400e', label: 'Clearance' },
  stop_buying: { bg: '#fecaca', fg: '#b91c1c', label: 'Stop Buying' },
  reduce_buying: { bg: '#fed7aa', fg: '#9a3412', label: 'Reduce' },
  review_pricing: { bg: '#e0e7ff', fg: '#3730a3', label: 'Review Pricing' },
  increase_buying: { bg: '#d1fae5', fg: '#065f46', label: 'Buy More' },
  maintain: { bg: '#f3f4f6', fg: '#374151', label: 'Maintain' },
  no_stock: { bg: '#f9fafb', fg: '#9ca3af', label: 'No Stock' },
};

const AGING_COLORS = {
  '0-30': '#22c55e',
  '31-90': '#eab308',
  '91-365': '#f97316',
  '365+': '#ef4444',
  'no_stock': '#d1d5db',
};

// ─── SUMMARY CARDS ──────────────────────────────────────────────

function SummaryCards({ summary }) {
  if (!summary) return null;

  const reductionPct = summary.total_inventory_value > 0
    ? (summary.reduction_needed / summary.total_inventory_value * 100) : 0;

  const cards = [
    {
      label: 'Total Inventory',
      value: fmt(summary.total_inventory_value),
      sub: `Target: ${fmt(summary.target_inventory_value)}`,
      color: summary.reduction_needed > 0 ? '#ef4444' : '#22c55e',
    },
    {
      label: 'Reduction Needed',
      value: fmt(summary.reduction_needed),
      sub: `${reductionPct.toFixed(0)}% of current`,
      color: '#f97316',
    },
    {
      label: 'Dead Stock',
      value: fmt(summary.dead_stock_value),
      sub: `${summary.dead_stock_pct}% of inventory`,
      color: '#ef4444',
    },
    {
      label: 'Inventory Turns',
      value: summary.inventory_turns.toFixed(1) + 'x',
      sub: `${fmt(summary.revenue_365d)} revenue / yr`,
      color: '#6366f1',
    },
    {
      label: 'Gross Margin',
      value: fmtPct(summary.gross_margin_pct),
      sub: fmt(summary.revenue_365d - summary.cogs_365d),
      color: '#22c55e',
    },
    {
      label: 'On Order',
      value: fmt(summary.on_order_value),
      sub: 'incoming purchases',
      color: '#3b82f6',
    },
  ];

  return (
    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))', gap: 12, marginBottom: 24 }}>
      {cards.map((c, i) => (
        <div key={i} style={{
          backgroundColor: 'var(--card-bg)', border: '1px solid var(--border)',
          borderRadius: 8, padding: '16px 20px', borderLeft: `4px solid ${c.color}`,
        }}>
          <div style={{ fontSize: 12, color: 'var(--text-muted)', marginBottom: 4 }}>{c.label}</div>
          <div style={{ fontSize: 22, fontWeight: 700, color: 'var(--text)' }}>{c.value}</div>
          <div style={{ fontSize: 11, color: 'var(--text-muted)', marginTop: 2 }}>{c.sub}</div>
        </div>
      ))}
    </div>
  );
}

// ─── AGING BAR ──────────────────────────────────────────────────

function AgingBar({ summary }) {
  if (!summary?.aging_buckets) return null;
  const buckets = summary.aging_buckets;
  const total = Object.values(buckets).reduce((a, b) => a + b, 0);
  if (total === 0) return null;

  const segments = [
    { key: '0-30', label: '0-30 days', value: buckets['0-30'] },
    { key: '31-90', label: '31-90 days', value: buckets['31-90'] },
    { key: '91-365', label: '91-365 days', value: buckets['91-365'] },
    { key: '365+', label: '365+ days', value: buckets['365+'] },
  ];

  return (
    <div style={{
      backgroundColor: 'var(--card-bg)', border: '1px solid var(--border)',
      borderRadius: 8, padding: 20, marginBottom: 24,
    }}>
      <div style={{ fontSize: 14, fontWeight: 600, marginBottom: 12 }}>Inventory Age Distribution</div>
      <div style={{ display: 'flex', height: 32, borderRadius: 6, overflow: 'hidden', marginBottom: 12 }}>
        {segments.map(s => {
          const pct = s.value / total * 100;
          if (pct < 1) return null;
          return (
            <div key={s.key} style={{
              width: `${pct}%`, backgroundColor: AGING_COLORS[s.key],
              display: 'flex', alignItems: 'center', justifyContent: 'center',
              fontSize: 11, fontWeight: 600, color: '#fff',
              textShadow: '0 1px 2px rgba(0,0,0,0.3)',
            }}>
              {pct > 8 ? `${pct.toFixed(0)}%` : ''}
            </div>
          );
        })}
      </div>
      <div style={{ display: 'flex', gap: 24, flexWrap: 'wrap' }}>
        {segments.map(s => (
          <div key={s.key} style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
            <div style={{ width: 12, height: 12, borderRadius: 3, backgroundColor: AGING_COLORS[s.key] }} />
            <span style={{ fontSize: 12, color: 'var(--text-muted)' }}>
              {s.label}: {fmt(s.value)} ({(s.value / total * 100).toFixed(0)}%)
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}

// ─── BRAND SCORECARDS TABLE ─────────────────────────────────────

function BrandTable({ brands, onSelectVendor, selectedVendor }) {
  const [sortField, setSortField] = useState('inventory_value');
  const [sortDir, setSortDir] = useState('desc');

  const sorted = useMemo(() => {
    if (!brands) return [];
    return [...brands].sort((a, b) => {
      const av = a[sortField] ?? 0;
      const bv = b[sortField] ?? 0;
      return sortDir === 'asc' ? (av > bv ? 1 : -1) : (av < bv ? 1 : -1);
    });
  }, [brands, sortField, sortDir]);

  const toggleSort = (field) => {
    if (sortField === field) setSortDir(d => d === 'asc' ? 'desc' : 'asc');
    else { setSortField(field); setSortDir('desc'); }
  };

  const columns = [
    { key: 'vendor', label: 'Brand', align: 'left' },
    { key: 'inventory_value', label: 'Inv Value', align: 'right' },
    { key: 'inventory_share_pct', label: 'Inv %', align: 'right' },
    { key: 'revenue_365d', label: 'Revenue 365d', align: 'right' },
    { key: 'gross_margin_pct', label: 'Margin %', align: 'right' },
    { key: 'capital_efficiency', label: 'Turns', align: 'right' },
    { key: 'sell_through_pct', label: 'Sell-Thru', align: 'right' },
    { key: 'avg_doi', label: 'Avg DOI', align: 'right' },
    { key: 'dead_skus', label: 'Dead SKUs', align: 'right' },
    { key: 'active_skus', label: 'Active', align: 'right' },
    { key: 'total_skus', label: 'Total', align: 'right' },
    { key: 'trend', label: 'Trend', align: 'center' },
  ];

  const headerStyle = (col) => ({
    padding: '8px 10px', fontSize: 11, fontWeight: 600, color: 'var(--text-muted)',
    textAlign: col.align, cursor: 'pointer', whiteSpace: 'nowrap',
    borderBottom: '2px solid var(--border)',
    backgroundColor: sortField === col.key ? 'var(--hover-bg)' : 'transparent',
  });

  const trendIcon = (t) => t === 'growing' ? '📈' : t === 'declining' ? '📉' : '➡️';

  return (
    <div style={{
      backgroundColor: 'var(--card-bg)', border: '1px solid var(--border)',
      borderRadius: 8, overflow: 'hidden', marginBottom: 24,
    }}>
      <div style={{ padding: '16px 20px', borderBottom: '1px solid var(--border)', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <div style={{ fontSize: 14, fontWeight: 600 }}>Brand Scorecards</div>
        <div style={{ fontSize: 12, color: 'var(--text-muted)' }}>{sorted.length} brands</div>
      </div>
      <div style={{ overflowX: 'auto' }}>
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
          <thead>
            <tr>
              {columns.map(col => (
                <th key={col.key} onClick={() => toggleSort(col.key)} style={headerStyle(col)}>
                  {col.label} {sortField === col.key ? (sortDir === 'asc' ? '↑' : '↓') : ''}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {sorted.map((b, i) => (
              <tr key={b.vendor}
                onClick={() => onSelectVendor(selectedVendor === b.vendor ? null : b.vendor)}
                style={{
                  cursor: 'pointer',
                  backgroundColor: selectedVendor === b.vendor ? 'var(--hover-bg)' : (i % 2 === 0 ? 'transparent' : 'var(--row-alt)'),
                }}>
                <td style={{ padding: '8px 10px', fontWeight: 600 }}>{b.vendor}</td>
                <td style={{ padding: '8px 10px', textAlign: 'right' }}>{fmt(b.inventory_value)}</td>
                <td style={{ padding: '8px 10px', textAlign: 'right' }}>{fmtPct(b.inventory_share_pct)}</td>
                <td style={{ padding: '8px 10px', textAlign: 'right' }}>{fmt(b.revenue_365d)}</td>
                <td style={{ padding: '8px 10px', textAlign: 'right',
                  color: b.gross_margin_pct >= 20 ? '#16a34a' : b.gross_margin_pct >= 15 ? '#ca8a04' : '#dc2626',
                }}>{fmtPct(b.gross_margin_pct)}</td>
                <td style={{ padding: '8px 10px', textAlign: 'right',
                  color: b.capital_efficiency >= 3 ? '#16a34a' : b.capital_efficiency >= 1.5 ? '#ca8a04' : '#dc2626',
                }}>{b.capital_efficiency.toFixed(1)}x</td>
                <td style={{ padding: '8px 10px', textAlign: 'right' }}>{fmtPct(b.sell_through_pct)}</td>
                <td style={{ padding: '8px 10px', textAlign: 'right',
                  color: b.avg_doi > 180 ? '#dc2626' : b.avg_doi > 90 ? '#ca8a04' : '#16a34a',
                }}>{b.avg_doi}</td>
                <td style={{ padding: '8px 10px', textAlign: 'right',
                  color: b.dead_skus > 0 ? '#dc2626' : 'var(--text-muted)',
                  fontWeight: b.dead_skus > 0 ? 600 : 400,
                }}>{b.dead_skus}</td>
                <td style={{ padding: '8px 10px', textAlign: 'right' }}>{b.active_skus}</td>
                <td style={{ padding: '8px 10px', textAlign: 'right' }}>{b.total_skus}</td>
                <td style={{ padding: '8px 10px', textAlign: 'center' }}>{trendIcon(b.trend)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

// ─── SKU DETAIL TABLE ───────────────────────────────────────────

function SkuTable({ vendor, onToast }) {
  const [skus, setSkus] = useState([]);
  const [loading, setLoading] = useState(false);
  const [sortField, setSortField] = useState('inventory_value');
  const [sortDir, setSortDir] = useState('desc');
  const [agingFilter, setAgingFilter] = useState(null);
  const [actionFilter, setActionFilter] = useState(null);
  const [search, setSearch] = useState('');

  useEffect(() => {
    if (!vendor) return;
    setLoading(true);
    api.fetchSkuDetails(vendor).then(data => {
      setSkus(data);
      setLoading(false);
    }).catch(err => {
      onToast?.('Failed to load SKU details', 'error');
      setLoading(false);
    });
  }, [vendor]);

  const filtered = useMemo(() => {
    let items = skus;
    if (agingFilter) items = items.filter(s => s.aging_bucket === agingFilter);
    if (actionFilter) items = items.filter(s => s.action === actionFilter);
    if (search) {
      const q = search.toLowerCase();
      items = items.filter(s =>
        s.sku.toLowerCase().includes(q) || s.product_title.toLowerCase().includes(q)
      );
    }
    return [...items].sort((a, b) => {
      const av = a[sortField] ?? 0;
      const bv = b[sortField] ?? 0;
      return sortDir === 'asc' ? (av > bv ? 1 : -1) : (av < bv ? 1 : -1);
    });
  }, [skus, sortField, sortDir, agingFilter, actionFilter, search]);

  const toggleSort = (field) => {
    if (sortField === field) setSortDir(d => d === 'asc' ? 'desc' : 'asc');
    else { setSortField(field); setSortDir('desc'); }
  };

  // Count by action for filter buttons
  const actionCounts = useMemo(() => {
    const counts = {};
    skus.forEach(s => { counts[s.action] = (counts[s.action] || 0) + 1; });
    return counts;
  }, [skus]);

  if (!vendor) return null;

  const ActionBadge = ({ action }) => {
    const info = ACTION_COLORS[action] || { bg: '#f3f4f6', fg: '#374151', label: action };
    return (
      <span style={{
        display: 'inline-block', padding: '2px 8px', borderRadius: 4,
        fontSize: 11, fontWeight: 600, backgroundColor: info.bg, color: info.fg,
      }}>
        {info.label}
      </span>
    );
  };

  return (
    <div style={{
      backgroundColor: 'var(--card-bg)', border: '1px solid var(--border)',
      borderRadius: 8, overflow: 'hidden', marginBottom: 24,
    }}>
      <div style={{ padding: '16px 20px', borderBottom: '1px solid var(--border)' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 }}>
          <div style={{ fontSize: 14, fontWeight: 600 }}>
            {vendor} — SKU Detail ({filtered.length} items)
          </div>
        </div>
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center' }}>
          <input
            type="text" placeholder="Search SKU or title..."
            value={search} onChange={e => setSearch(e.target.value)}
            style={{
              padding: '6px 12px', borderRadius: 6, border: '1px solid var(--border)',
              backgroundColor: 'var(--input-bg)', color: 'var(--text)', fontSize: 12, width: 200,
            }}
          />
          {Object.entries(ACTION_COLORS).map(([key, info]) => {
            const count = actionCounts[key] || 0;
            if (count === 0) return null;
            return (
              <button key={key} onClick={() => setActionFilter(actionFilter === key ? null : key)}
                style={{
                  padding: '4px 10px', borderRadius: 4, border: '1px solid var(--border)',
                  fontSize: 11, cursor: 'pointer',
                  backgroundColor: actionFilter === key ? info.bg : 'transparent',
                  color: actionFilter === key ? info.fg : 'var(--text-muted)',
                  fontWeight: actionFilter === key ? 600 : 400,
                }}>
                {info.label} ({count})
              </button>
            );
          })}
        </div>
      </div>
      {loading ? (
        <div style={{ padding: 40, textAlign: 'center', color: 'var(--text-muted)' }}>Loading...</div>
      ) : (
        <div style={{ overflowX: 'auto', maxHeight: 600, overflowY: 'auto' }}>
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
            <thead>
              <tr>
                {[
                  { key: 'sku', label: 'SKU', align: 'left' },
                  { key: 'product_title', label: 'Product', align: 'left' },
                  { key: 'price', label: 'Price', align: 'right' },
                  { key: 'cost', label: 'Cost', align: 'right' },
                  { key: 'margin_pct', label: 'Margin', align: 'right' },
                  { key: 'current_stock', label: 'Stock', align: 'right' },
                  { key: 'inventory_value', label: 'Inv Value', align: 'right' },
                  { key: 'sold_365d', label: '365d', align: 'right' },
                  { key: 'sold_90d', label: '90d', align: 'right' },
                  { key: 'sold_30d', label: '30d', align: 'right' },
                  { key: 'doi', label: 'DOI', align: 'right' },
                  { key: 'sell_through_pct', label: 'Sell-Thru', align: 'right' },
                  { key: 'action', label: 'Action', align: 'center' },
                ].map(col => (
                  <th key={col.key} onClick={() => toggleSort(col.key)} style={{
                    padding: '8px 8px', fontSize: 11, fontWeight: 600, color: 'var(--text-muted)',
                    textAlign: col.align, cursor: 'pointer', whiteSpace: 'nowrap',
                    borderBottom: '2px solid var(--border)', position: 'sticky', top: 0,
                    backgroundColor: 'var(--card-bg)',
                  }}>
                    {col.label} {sortField === col.key ? (sortDir === 'asc' ? '↑' : '↓') : ''}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {filtered.map((s, i) => (
                <tr key={s.sku} style={{ backgroundColor: i % 2 === 0 ? 'transparent' : 'var(--row-alt)' }}>
                  <td style={{ padding: '6px 8px', fontFamily: 'monospace', fontSize: 11 }}>{s.sku}</td>
                  <td style={{ padding: '6px 8px', maxWidth: 250, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{s.product_title}</td>
                  <td style={{ padding: '6px 8px', textAlign: 'right' }}>{fmtFull(s.price)}</td>
                  <td style={{ padding: '6px 8px', textAlign: 'right' }}>{fmtFull(s.cost)}</td>
                  <td style={{ padding: '6px 8px', textAlign: 'right',
                    color: s.margin_pct >= 20 ? '#16a34a' : s.margin_pct >= 15 ? '#ca8a04' : '#dc2626',
                  }}>{fmtPct(s.margin_pct)}</td>
                  <td style={{ padding: '6px 8px', textAlign: 'right' }}>{s.current_stock}</td>
                  <td style={{ padding: '6px 8px', textAlign: 'right' }}>{fmt(s.inventory_value)}</td>
                  <td style={{ padding: '6px 8px', textAlign: 'right' }}>{s.sold_365d}</td>
                  <td style={{ padding: '6px 8px', textAlign: 'right' }}>{s.sold_90d}</td>
                  <td style={{ padding: '6px 8px', textAlign: 'right' }}>{s.sold_30d}</td>
                  <td style={{ padding: '6px 8px', textAlign: 'right',
                    color: s.doi > 365 ? '#dc2626' : s.doi > 180 ? '#f97316' : s.doi > 90 ? '#ca8a04' : '#16a34a',
                    fontWeight: s.doi > 180 ? 600 : 400,
                  }}>{s.doi}</td>
                  <td style={{ padding: '6px 8px', textAlign: 'right' }}>{fmtPct(s.sell_through_pct)}</td>
                  <td style={{ padding: '6px 8px', textAlign: 'center' }}><ActionBadge action={s.action} /></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

// ─── ACTION LISTS ───────────────────────────────────────────────

function ActionLists({ actions, onToast, onRefresh }) {
  const [activeTab, setActiveTab] = useState('stop_buying');
  const [selected, setSelected] = useState(new Set());
  const [tagging, setTagging] = useState(false);

  if (!actions) return null;

  const tabs = [
    { id: 'stop_buying', label: `Stop Buying (${actions.stop_buying?.length || 0})`, color: '#ef4444' },
    { id: 'liquidate', label: `Liquidate (${actions.liquidate?.length || 0})`, color: '#dc2626' },
    { id: 'increase_buying', label: `Buy More (${actions.increase_buying?.length || 0})`, color: '#22c55e' },
    { id: 'no_cost', label: `No Cost (${actions.no_cost?.length || 0})`, color: '#f97316' },
    { id: 'pricelist_mismatch', label: `Pricelist Mismatch (${actions.pricelist_mismatch?.length || 0})`, color: '#8b5cf6' },
    { id: 'margin_outliers', label: `Margin Outliers (${actions.margin_outliers?.length || 0})`, color: '#ec4899' },
    { id: 'oos_stop_selling', label: `OOS Stop-Selling (${actions.oos_stop_selling?.length || 0})`, color: '#94a3b8' },
    { id: 'replacement_parts', label: `Replacement Parts (${actions.replacement_parts?.length || 0})`, color: '#0ea5e9' },
    { id: 'discontinued', label: `Discontinued (${actions.discontinued?.length || 0})`, color: '#64748b' },
  ];

  const items = actions[activeTab] || [];
  // Tabs that allow bulk tagging actions need checkboxes
  const showCheckboxes = ['stop_buying', 'liquidate', 'oos_stop_selling',
                          'replacement_parts', 'discontinued'].includes(activeTab);
  const isNoCost = activeTab === 'no_cost';
  const isMismatch = activeTab === 'pricelist_mismatch';
  const isMargin = activeTab === 'margin_outliers';
  const isReplacementParts = activeTab === 'replacement_parts';
  const isDiscontinued = activeTab === 'discontinued';
  const isOosStop = activeTab === 'oos_stop_selling';

  const hasTag = (item) => ((item.tags || '').toLowerCase()).includes('special order');

  const handleSelectAll = () => {
    if (selected.size === items.length) setSelected(new Set());
    else setSelected(new Set(items.filter(i => i.product_id).map(i => i.product_id)));
  };

  const toggleItem = (productId) => {
    setSelected(prev => {
      const next = new Set(prev);
      if (next.has(productId)) next.delete(productId); else next.add(productId);
      return next;
    });
  };

  // Context-aware bulk tag handler — figures out the right tag/action
  // based on the active tab. The Discontinued tab supports REMOVING the
  // tag (for items mistakenly marked); the others ADD a tag.
  const bulkTagHandler = async (action) => {
    if (selected.size === 0) return;
    setTagging(true);
    try {
      let result, msg;
      if (action === 'special_order') {
        result = await api.addTagToProducts([...selected], 'Special Order');
        msg = `Tagged ${result.tagged} product(s) as "Special Order"`;
      } else if (action === 'discontinued') {
        result = await api.addTagToProducts([...selected], 'Discontinued');
        msg = `Tagged ${result.tagged} product(s) as "Discontinued"`;
      } else if (action === 'replacement_part') {
        result = await api.addTagToProducts([...selected], 'Replacement Part');
        msg = `Tagged ${result.tagged} product(s) as "Replacement Part"`;
      } else if (action === 'untag_discontinued') {
        result = await api.removeTagFromProducts([...selected], 'Discontinued');
        msg = `Removed "Discontinued" from ${result.tagged} product(s)`;
      } else if (action === 'untag_replacement_part') {
        result = await api.removeTagFromProducts([...selected], 'Replacement Part');
        msg = `Removed "Replacement Part" from ${result.tagged} product(s)`;
      } else {
        return;
      }
      onToast?.(msg, 'success');
      setSelected(new Set());
      if (onRefresh) onRefresh();
    } catch (err) { onToast?.('Tagging failed: ' + err.message, 'error'); }
    setTagging(false);
  };

  const handleTabChange = (id) => { setActiveTab(id); setSelected(new Set()); };

  const thStyle = (align) => ({
    padding: '8px 10px', fontSize: 11, fontWeight: 600, color: 'var(--text-muted)',
    textAlign: align, borderBottom: '2px solid var(--border)',
    position: 'sticky', top: 0, backgroundColor: 'var(--card-bg)',
  });

  const btnStyle = (bg, disabled) => ({
    padding: '6px 14px', borderRadius: 6, border: 'none', cursor: disabled ? 'wait' : 'pointer',
    fontSize: 12, fontWeight: 600,
    backgroundColor: bg, color: '#fff', opacity: disabled ? 0.6 : 1,
  });

  return (
    <div style={{ backgroundColor: 'var(--card-bg)', border: '1px solid var(--border)', borderRadius: 8, overflow: 'hidden', marginBottom: 24 }}>
      <div style={{ padding: '16px 20px', borderBottom: '1px solid var(--border)', display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center' }}>
        {tabs.map(t => (
          <button key={t.id} onClick={() => handleTabChange(t.id)}
            style={{ padding: '6px 14px', borderRadius: 6, border: 'none', cursor: 'pointer', fontSize: 12, fontWeight: 600,
              backgroundColor: activeTab === t.id ? t.color : 'var(--hover-bg)', color: activeTab === t.id ? '#fff' : 'var(--text-muted)' }}>
            {t.label}
          </button>
        ))}
        {showCheckboxes && selected.size > 0 && (
          <>
            <div style={{ flex: 1 }} />
            <span style={{ fontSize: 12, color: 'var(--text-muted)' }}>{selected.size} selected</span>
            {/* Stop Buying & Liquidate: tag as Special Order (existing) */}
            {(activeTab === 'stop_buying' || activeTab === 'liquidate') && (
              <button onClick={() => bulkTagHandler('special_order')} disabled={tagging}
                style={btnStyle('#6366f1', tagging)}>
                {tagging ? 'Tagging…' : '🏷️ Tag "Special Order"'}
              </button>
            )}
            {/* OOS Stop-Selling: bulk tag as Discontinued */}
            {isOosStop && (
              <button onClick={() => bulkTagHandler('discontinued')} disabled={tagging}
                style={btnStyle('#64748b', tagging)}
                title="Tag selected items as Discontinued in Shopify">
                {tagging ? 'Tagging…' : '🏷️ Tag "Discontinued"'}
              </button>
            )}
            {/* Replacement Parts: untag for items wrongly classified */}
            {isReplacementParts && (
              <button onClick={() => bulkTagHandler('untag_replacement_part')} disabled={tagging}
                style={btnStyle('#0ea5e9', tagging)}
                title="Remove the Replacement Part tag (returns these items to normal replenishment)">
                {tagging ? 'Removing tag…' : '✕ Remove "Replacement Part" tag'}
              </button>
            )}
            {/* Discontinued: untag for items wrongly classified */}
            {isDiscontinued && (
              <button onClick={() => bulkTagHandler('untag_discontinued')} disabled={tagging}
                style={btnStyle('#64748b', tagging)}
                title="Remove the Discontinued tag (returns these items to normal replenishment)">
                {tagging ? 'Removing tag…' : '✕ Remove "Discontinued" tag'}
              </button>
            )}
          </>
        )}
      </div>
      <div style={{ overflowX: 'auto', maxHeight: 500, overflowY: 'auto' }}>
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
          <thead>
            <tr>
              {showCheckboxes && <th style={{ ...thStyle('center'), width: 30, padding: '8px 6px' }}>
                <input type="checkbox" checked={items.length > 0 && selected.size === items.length} onChange={handleSelectAll} style={{ cursor: 'pointer' }} />
              </th>}
              {showCheckboxes && <th style={{ ...thStyle('center'), width: 30 }} title="Special Order tag">SO</th>}
              <th style={thStyle('left')}>SKU</th>
              <th style={thStyle('left')}>Product</th>
              <th style={thStyle('left')}>Vendor</th>
              <th style={thStyle('right')}>Price</th>
              <th style={thStyle('right')}>Cost</th>
              {isMismatch && <><th style={thStyle('right')}>Pricelist Cost</th><th style={thStyle('right')}>Diff</th></>}
              {isMargin && <th style={thStyle('right')}>Margin %</th>}
              <th style={thStyle('right')}>Stock</th>
              <th style={thStyle('right')}>365d Sold</th>
              {!isMismatch && <th style={thStyle('right')}>DOI</th>}
              <th style={thStyle('right')} title="Customers waiting for back-in-stock. +N = suggested added order quantity (60% dampener)">Waiters</th>
            </tr>
          </thead>
          <tbody>
            {items.length === 0 && (
              <tr><td colSpan={20} style={{ padding: 20, textAlign: 'center', color: 'var(--text-muted)' }}>
                {isMismatch ? 'No mismatches found. Upload vendor pricelists to compare.' : 'No items in this list.'}
              </td></tr>
            )}
            {items.map((item, i) => {
              const marginPct = item.margin_pct != null ? item.margin_pct : (item.price > 0 ? ((item.price - item.cost) / item.price * 100) : 0);
              const tagged = hasTag(item);
              return (
                <tr key={item.sku} style={{ backgroundColor: i % 2 === 0 ? 'transparent' : 'var(--row-alt)' }}>
                  {showCheckboxes && <td style={{ padding: '4px 6px', textAlign: 'center' }}>
                    <input type="checkbox" checked={selected.has(item.product_id)} onChange={() => toggleItem(item.product_id)} style={{ cursor: 'pointer' }} />
                  </td>}
                  {showCheckboxes && <td style={{ padding: '4px 6px', textAlign: 'center' }}>
                    {tagged && <span title="Special Order" style={{ color: '#22c55e', fontSize: 14 }}>✓</span>}
                  </td>}
                  <td style={{ padding: '6px 10px', fontFamily: 'monospace', fontSize: 11 }}>{item.sku}</td>
                  <td style={{ padding: '6px 10px', maxWidth: 250, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{item.product_title}</td>
                  <td style={{ padding: '6px 10px' }}>{item.vendor}</td>
                  <td style={{ padding: '6px 10px', textAlign: 'right' }}>{fmtFull(item.price)}</td>
                  <td style={{ padding: '6px 10px', textAlign: 'right', color: isNoCost ? '#ef4444' : 'var(--text)', fontWeight: isNoCost ? 600 : 400 }}>{fmtFull(item.cost)}</td>
                  {isMismatch && <>
                    <td style={{ padding: '6px 10px', textAlign: 'right', fontWeight: 600 }}>{fmtFull(item.supplier_cost)}{item.supplier_currency !== 'CAD' ? ` ${item.supplier_currency}` : ''}</td>
                    <td style={{ padding: '6px 10px', textAlign: 'right', color: item.cost_diff > 0 ? '#ef4444' : '#22c55e', fontWeight: 600 }}>{fmtFull(item.cost_diff)}</td>
                  </>}
                  {isMargin && <td style={{ padding: '6px 10px', textAlign: 'right', fontWeight: 600, color: marginPct < 20 ? '#ef4444' : marginPct > 100 ? '#8b5cf6' : '#16a34a' }}>{marginPct.toFixed(1)}%</td>}
                  <td style={{ padding: '6px 10px', textAlign: 'right' }}>{item.current_stock}</td>
                  <td style={{ padding: '6px 10px', textAlign: 'right' }}>{item.total_sold_365d}</td>
                  {!isMismatch && <td style={{ padding: '6px 10px', textAlign: 'right', color: item.days_of_stock > 365 ? '#dc2626' : item.days_of_stock > 180 ? '#f97316' : '#16a34a', fontWeight: 600 }}>{Math.round(item.days_of_stock || 0)}</td>}
                  <td style={{ padding: '6px 10px', textAlign: 'right' }}>
                    {item.waiter_count > 0 ? (
                      <span title={`${item.waiter_count} customers waiting · +${item.demand_boost} suggested (60% dampener)`}
                        style={{ padding: '2px 8px', borderRadius: 10, backgroundColor: '#eef2ff', color: '#6366f1', fontWeight: 700, fontSize: 11 }}>
                        {item.waiter_count} (+{item.demand_boost})
                      </span>
                    ) : (
                      <span style={{ color: 'var(--text-muted)', fontSize: 11 }}>—</span>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}

// ─── VENDOR PURCHASING ANALYSIS ─────────────────────────────────

const ALIGNMENT_BADGES = {
  over_buying: { bg: '#fee2e2', fg: '#991b1b', label: 'Over-buying' },
  balanced: { bg: '#d1fae5', fg: '#065f46', label: 'Balanced' },
  under_buying: { bg: '#fef3c7', fg: '#92400e', label: 'Under-buying' },
  no_data: { bg: '#f3f4f6', fg: '#6b7280', label: 'No data' },
};

function VendorPurchasingTable({ purchasing, onToast }) {
  const [sortField, setSortField] = useState('risk_score');
  const [sortDir, setSortDir] = useState('desc');

  if (!purchasing || !purchasing.vendors) return null;

  const months = purchasing.months || [];
  const vendors = purchasing.vendors;

  const sorted = useMemo(() => {
    return [...vendors].sort((a, b) => {
      const av = a[sortField] ?? 0;
      const bv = b[sortField] ?? 0;
      return sortDir === 'asc' ? (av > bv ? 1 : -1) : (av < bv ? 1 : -1);
    });
  }, [vendors, sortField, sortDir]);

  const toggleSort = (field) => {
    if (sortField === field) setSortDir(d => d === 'asc' ? 'desc' : 'asc');
    else { setSortField(field); setSortDir('desc'); }
  };

  // Mini sparkline component
  const Sparkline = ({ values, width = 120, height = 24 }) => {
    if (!values || values.length < 2) return null;
    const max = Math.max(...values, 1);
    const points = values.map((v, i) =>
      `${(i / (values.length - 1)) * width},${height - (v / max) * (height - 2)}`
    ).join(' ');
    return (
      <svg width={width} height={height} style={{ display: 'block' }}>
        <polyline points={points} fill="none" stroke="#6366f1" strokeWidth="1.5" />
      </svg>
    );
  };

  return (
    <div style={{
      backgroundColor: 'var(--card-bg)', border: '1px solid var(--border)',
      borderRadius: 8, overflow: 'hidden', marginBottom: 24,
    }}>
      <div style={{ padding: '16px 20px', borderBottom: '1px solid var(--border)', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <div>
          <div style={{ fontSize: 14, fontWeight: 600 }}>Vendor Purchasing Analysis</div>
          <div style={{ fontSize: 11, color: 'var(--text-muted)', marginTop: 2 }}>
            Spend vs. inventory health — {months.length} months ({months[0]} to {months[months.length - 1]})
          </div>
        </div>
        <div style={{ display: 'flex', gap: 12 }}>
          {Object.entries(ALIGNMENT_BADGES).filter(([k]) => k !== 'no_data').map(([key, info]) => {
            const count = vendors.filter(v => v.alignment === key).length;
            return (
              <span key={key} style={{ fontSize: 11, color: info.fg, backgroundColor: info.bg, padding: '2px 8px', borderRadius: 4, fontWeight: 600 }}>
                {info.label}: {count}
              </span>
            );
          })}
        </div>
      </div>
      <div style={{ overflowX: 'auto' }}>
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
          <thead>
            <tr>
              {[
                { key: 'vendor', label: 'Vendor', align: 'left' },
                { key: 'total_spend', label: '12m Spend', align: 'right' },
                { key: 'avg_monthly_spend', label: 'Avg/Month', align: 'right' },
                { key: 'monthly_spend', label: 'Trend', align: 'center' },
                { key: 'inventory_value', label: 'Inv Value', align: 'right' },
                { key: 'revenue_365d', label: 'Revenue', align: 'right' },
                { key: 'margin_pct', label: 'Margin', align: 'right' },
                { key: 'avg_doi', label: 'DOI', align: 'right' },
                { key: 'capital_efficiency', label: 'Turns', align: 'right' },
                { key: 'dead_stock_value', label: 'Dead $', align: 'right' },
                { key: 'dead_skus', label: 'Dead SKUs', align: 'right' },
                { key: 'alignment', label: 'Status', align: 'center' },
                { key: 'risk_score', label: 'Risk', align: 'center' },
              ].map(col => (
                <th key={col.key} onClick={() => col.key !== 'monthly_spend' && toggleSort(col.key)} style={{
                  padding: '8px 10px', fontSize: 11, fontWeight: 600, color: 'var(--text-muted)',
                  textAlign: col.align, cursor: col.key !== 'monthly_spend' ? 'pointer' : 'default',
                  whiteSpace: 'nowrap', borderBottom: '2px solid var(--border)',
                  backgroundColor: sortField === col.key ? 'var(--hover-bg)' : 'transparent',
                }}>
                  {col.label} {sortField === col.key ? (sortDir === 'asc' ? '↑' : '↓') : ''}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {sorted.map((v, i) => {
              const badge = ALIGNMENT_BADGES[v.alignment] || ALIGNMENT_BADGES.no_data;
              const riskColor = v.risk_score >= 5 ? '#dc2626' : v.risk_score >= 3 ? '#f97316' : v.risk_score >= 1 ? '#ca8a04' : '#22c55e';
              return (
                <tr key={v.vendor} style={{ backgroundColor: i % 2 === 0 ? 'transparent' : 'var(--row-alt)' }}>
                  <td style={{ padding: '8px 10px', fontWeight: 600 }}>{v.vendor}</td>
                  <td style={{ padding: '8px 10px', textAlign: 'right' }}>{fmt(v.total_spend)}</td>
                  <td style={{ padding: '8px 10px', textAlign: 'right' }}>{fmt(v.avg_monthly_spend)}</td>
                  <td style={{ padding: '8px 10px', textAlign: 'center' }}>
                    <Sparkline values={v.monthly_spend} />
                  </td>
                  <td style={{ padding: '8px 10px', textAlign: 'right' }}>{fmt(v.inventory_value)}</td>
                  <td style={{ padding: '8px 10px', textAlign: 'right' }}>{fmt(v.revenue_365d)}</td>
                  <td style={{ padding: '8px 10px', textAlign: 'right',
                    color: v.margin_pct >= 20 ? '#16a34a' : v.margin_pct >= 15 ? '#ca8a04' : '#dc2626',
                  }}>{fmtPct(v.margin_pct)}</td>
                  <td style={{ padding: '8px 10px', textAlign: 'right',
                    color: v.avg_doi > 180 ? '#dc2626' : v.avg_doi > 90 ? '#ca8a04' : '#16a34a',
                  }}>{v.avg_doi}</td>
                  <td style={{ padding: '8px 10px', textAlign: 'right',
                    color: v.capital_efficiency >= 3 ? '#16a34a' : v.capital_efficiency >= 1.5 ? '#ca8a04' : '#dc2626',
                  }}>{v.capital_efficiency.toFixed(1)}x</td>
                  <td style={{ padding: '8px 10px', textAlign: 'right',
                    color: v.dead_stock_value > 0 ? '#dc2626' : 'var(--text-muted)',
                  }}>{fmt(v.dead_stock_value)}</td>
                  <td style={{ padding: '8px 10px', textAlign: 'right',
                    color: v.dead_skus > 0 ? '#dc2626' : 'var(--text-muted)',
                    fontWeight: v.dead_skus > 0 ? 600 : 400,
                  }}>{v.dead_skus}</td>
                  <td style={{ padding: '8px 10px', textAlign: 'center' }}>
                    <span style={{
                      display: 'inline-block', padding: '2px 8px', borderRadius: 4,
                      fontSize: 11, fontWeight: 600, backgroundColor: badge.bg, color: badge.fg,
                    }}>
                      {badge.label}
                    </span>
                  </td>
                  <td style={{ padding: '8px 10px', textAlign: 'center' }}>
                    <span style={{
                      display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
                      width: 24, height: 24, borderRadius: '50%', fontSize: 12, fontWeight: 700,
                      backgroundColor: riskColor + '20', color: riskColor,
                    }}>
                      {v.risk_score}
                    </span>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}

// ─── TREND CHART ────────────────────────────────────────────────

function TrendChart({ trend, onCapture, capturing }) {
  if (!trend || !trend.overall || trend.overall.length === 0) {
    return (
      <div style={{
        backgroundColor: 'var(--card-bg)', border: '1px solid var(--border)',
        borderRadius: 8, padding: 20, marginBottom: 24, textAlign: 'center',
      }}>
        <div style={{ fontSize: 14, fontWeight: 600, marginBottom: 8 }}>Inventory Value Trend</div>
        <p style={{ color: 'var(--text-muted)', fontSize: 13, marginBottom: 16 }}>
          No trend data yet. Capture your first snapshot to start tracking.
        </p>
        <button onClick={onCapture} disabled={capturing} style={{
          padding: '8px 20px', borderRadius: 6, border: 'none', cursor: 'pointer',
          backgroundColor: '#6366f1', color: '#fff', fontWeight: 600, fontSize: 13,
          opacity: capturing ? 0.6 : 1,
        }}>
          {capturing ? 'Capturing...' : '📸 Capture First Snapshot'}
        </button>
      </div>
    );
  }

  const data = trend.overall;
  const target = trend.target || 1000000;
  const delta = trend.delta;

  // Chart dimensions
  const W = 900, H = 220, PAD_L = 70, PAD_R = 20, PAD_T = 10, PAD_B = 30;
  const chartW = W - PAD_L - PAD_R;
  const chartH = H - PAD_T - PAD_B;

  const values = data.map(d => d.total);
  const minVal = Math.min(...values, target) * 0.95;
  const maxVal = Math.max(...values) * 1.05;
  const range = maxVal - minVal || 1;

  const xScale = (i) => PAD_L + (i / (data.length - 1 || 1)) * chartW;
  const yScale = (v) => PAD_T + chartH - ((v - minVal) / range) * chartH;

  // Main line
  const linePath = data.map((d, i) => `${i === 0 ? 'M' : 'L'}${xScale(i).toFixed(1)},${yScale(d.total).toFixed(1)}`).join(' ');

  // Dead stock area
  const deadPath = data.map((d, i) => `${i === 0 ? 'M' : 'L'}${xScale(i).toFixed(1)},${yScale(d.dead).toFixed(1)}`).join(' ');

  // Target line
  const targetY = yScale(target);

  // X-axis labels (show ~6 dates)
  const labelStep = Math.max(1, Math.floor(data.length / 6));
  const xLabels = data.filter((_, i) => i % labelStep === 0 || i === data.length - 1);

  // Y-axis labels
  const yTicks = 5;
  const yLabels = Array.from({ length: yTicks }, (_, i) => minVal + (range * i / (yTicks - 1)));

  return (
    <div style={{
      backgroundColor: 'var(--card-bg)', border: '1px solid var(--border)',
      borderRadius: 8, padding: 20, marginBottom: 24,
    }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
        <div>
          <div style={{ fontSize: 14, fontWeight: 600 }}>Inventory Value Trend</div>
          {delta && (
            <div style={{ fontSize: 12, color: 'var(--text-muted)', marginTop: 2 }}>
              {delta.days} days: {' '}
              <span style={{ color: delta.total_change <= 0 ? '#16a34a' : '#dc2626', fontWeight: 600 }}>
                {delta.total_change <= 0 ? '↓' : '↑'} {fmt(Math.abs(delta.total_change))} ({delta.total_pct_change}%)
              </span>
            </div>
          )}
        </div>
        <button onClick={onCapture} disabled={capturing} style={{
          padding: '6px 14px', borderRadius: 6, border: '1px solid var(--border)',
          cursor: 'pointer', backgroundColor: 'transparent', color: 'var(--text)',
          fontSize: 12, opacity: capturing ? 0.6 : 1,
        }}>
          {capturing ? 'Capturing...' : '📸 Capture Snapshot'}
        </button>
      </div>

      <svg viewBox={`0 0 ${W} ${H}`} style={{ width: '100%', maxHeight: 240 }}>
        {/* Grid lines */}
        {yLabels.map((v, i) => (
          <g key={i}>
            <line x1={PAD_L} y1={yScale(v)} x2={W - PAD_R} y2={yScale(v)}
              stroke="var(--border)" strokeWidth="0.5" strokeDasharray="4,4" />
            <text x={PAD_L - 8} y={yScale(v) + 4} textAnchor="end"
              fill="var(--text-muted)" fontSize="10">
              {(v / 1000).toFixed(0)}K
            </text>
          </g>
        ))}

        {/* Target line */}
        <line x1={PAD_L} y1={targetY} x2={W - PAD_R} y2={targetY}
          stroke="#22c55e" strokeWidth="1.5" strokeDasharray="6,4" />
        <text x={W - PAD_R + 4} y={targetY + 4} fill="#22c55e" fontSize="10" fontWeight="600">
          Target
        </text>

        {/* Dead stock line */}
        {data.length > 1 && (
          <path d={deadPath} fill="none" stroke="#ef4444" strokeWidth="1.5" opacity="0.5" strokeDasharray="3,3" />
        )}

        {/* Main inventory line */}
        <path d={linePath} fill="none" stroke="#6366f1" strokeWidth="2.5" />

        {/* Data points */}
        {data.map((d, i) => (
          <circle key={i} cx={xScale(i)} cy={yScale(d.total)} r="3" fill="#6366f1" />
        ))}

        {/* X-axis labels */}
        {xLabels.map((d, i) => {
          const idx = data.indexOf(d);
          return (
            <text key={i} x={xScale(idx)} y={H - 4} textAnchor="middle"
              fill="var(--text-muted)" fontSize="10">
              {d.date.slice(5)} {/* MM-DD */}
            </text>
          );
        })}
      </svg>

      <div style={{ display: 'flex', gap: 20, marginTop: 8, justifyContent: 'center' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
          <div style={{ width: 16, height: 3, backgroundColor: '#6366f1', borderRadius: 2 }} />
          <span style={{ fontSize: 11, color: 'var(--text-muted)' }}>Total Inventory</span>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
          <div style={{ width: 16, height: 3, backgroundColor: '#ef4444', borderRadius: 2, opacity: 0.5 }} />
          <span style={{ fontSize: 11, color: 'var(--text-muted)' }}>Dead Stock</span>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
          <div style={{ width: 16, height: 3, backgroundColor: '#22c55e', borderRadius: 2 }} />
          <span style={{ fontSize: 11, color: 'var(--text-muted)' }}>$1M Target</span>
        </div>
      </div>
    </div>
  );
}

// ─── MAIN PAGE ──────────────────────────────────────────────────

const VIEW_TITLES = {
  intelligence: 'Inventory Intelligence',
  brands: 'Brand Analysis',
  purchasing: 'Vendor Purchasing',
  actions: 'Action Lists',
};

export default function AnalyticsPage({ view = 'intelligence', onToast }) {
  const [summary, setSummary] = useState(null);
  const [brands, setBrands] = useState(null);
  const [actions, setActions] = useState(null);
  const [trend, setTrend] = useState(null);
  const [purchasing, setPurchasing] = useState(null);
  const [selectedVendor, setSelectedVendor] = useState(null);
  const [loading, setLoading] = useState(true);
  const [capturing, setCapturing] = useState(false);

  useEffect(() => {
    setLoading(true);
    setSelectedVendor(null);

    // Only fetch data needed for this view
    const fetches = [];
    if (view === 'intelligence') {
      fetches.push(
        api.fetchAnalyticsSummary().then(s => setSummary(s)),
        api.fetchInventoryTrend(90).then(t => setTrend(t)),
      );
    } else if (view === 'brands') {
      fetches.push(
        api.fetchAnalyticsSummary().then(s => setSummary(s)),
        api.fetchBrandScorecards().then(b => setBrands(b)),
      );
    } else if (view === 'purchasing') {
      fetches.push(
        api.fetchVendorPurchasing(12).then(p => setPurchasing(p)),
      );
    } else if (view === 'actions') {
      fetches.push(
        api.fetchActionLists().then(a => setActions(a)),
      );
    }

    Promise.all(fetches)
      .then(() => setLoading(false))
      .catch(err => {
        onToast?.('Failed to load: ' + err.message, 'error');
        setLoading(false);
      });
  }, [view]);

  const handleCapture = async () => {
    setCapturing(true);
    try {
      await api.captureSnapshot();
      onToast?.('Snapshot captured successfully', 'success');
      const t = await api.fetchInventoryTrend(90);
      setTrend(t);
    } catch (err) {
      onToast?.('Failed to capture snapshot: ' + err.message, 'error');
    }
    setCapturing(false);
  };

  if (loading) {
    return (
      <div style={{ padding: 40, textAlign: 'center', color: 'var(--text-muted)' }}>
        Loading {VIEW_TITLES[view] || 'analytics'}...
      </div>
    );
  }

  return (
    <div style={{ padding: 24, maxWidth: 1600 }}>
      <div style={{ marginBottom: 20 }}>
        <h1 style={{ fontSize: 20, fontWeight: 700, margin: 0 }}>{VIEW_TITLES[view] || 'Analytics'}</h1>
      </div>

      {view === 'intelligence' && (
        <>
          <SummaryCards summary={summary} />
          <TrendChart trend={trend} onCapture={handleCapture} capturing={capturing} />
        </>
      )}

      {view === 'brands' && (
        <>
          <AgingBar summary={summary} />
          <BrandTable brands={brands} onSelectVendor={setSelectedVendor} selectedVendor={selectedVendor} />
          {selectedVendor && <SkuTable vendor={selectedVendor} onToast={onToast} />}
        </>
      )}

      {view === 'purchasing' && (
        <VendorPurchasingTable purchasing={purchasing} onToast={onToast} />
      )}

      {view === 'actions' && (
        <ActionLists actions={actions} onToast={onToast}
          onRefresh={() => api.fetchActionLists().then(a => setActions(a))} />
      )}
    </div>
  );
}
