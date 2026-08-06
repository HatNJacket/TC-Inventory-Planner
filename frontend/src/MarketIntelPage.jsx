import { useState, useEffect, useMemo } from 'react';
import * as api from './api';

const fmt = (n) => new Intl.NumberFormat('en-CA', {
  style: 'currency', currency: 'CAD', minimumFractionDigits: 2,
}).format(n);
const fmtPct = (n) => `${n > 0 ? '+' : ''}${n.toFixed(1)}%`;

// ─── SUMMARY CARDS (clickable filters) ──────────────────────────

function MarketSummary({ summary, priceFilter, onFilterChange }) {
  if (!summary || summary.total_entries === 0) return null;

  const cards = [
    { key: null, label: 'Products Tracked', value: summary.tracked_skus, color: '#6366f1' },
    { key: 'we_cheaper', label: 'We Are Cheaper', value: summary.we_are_cheaper, color: '#22c55e' },
    { key: 'they_cheaper', label: 'They Are Cheaper', value: summary.they_are_cheaper, color: '#ef4444' },
    { key: 'same', label: 'Same Price', value: summary.same_price, color: '#6b7280' },
    { key: null, label: 'Avg Price Gap', value: fmtPct(summary.avg_price_diff_pct), color: summary.avg_price_diff_pct >= 0 ? '#22c55e' : '#ef4444', noClick: true },
  ];

  return (
    <div style={{ marginBottom: 24 }}>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(160px, 1fr))', gap: 12, marginBottom: 16 }}>
        {cards.map((c, i) => {
          const isClickable = c.key != null;
          const isActive = isClickable && priceFilter === c.key;
          return (
            <div key={i}
              onClick={() => isClickable && onFilterChange(isActive ? null : c.key)}
              style={{
                backgroundColor: 'var(--card-bg)',
                border: isActive ? `2px solid ${c.color}` : '1px solid var(--border)',
                borderRadius: 8, padding: '14px 18px', borderLeft: `4px solid ${c.color}`,
                cursor: isClickable ? 'pointer' : 'default',
                opacity: priceFilter && !isActive && isClickable ? 0.5 : 1,
                transition: 'all 0.15s',
              }}>
              <div style={{ fontSize: 11, color: 'var(--text-muted)', marginBottom: 4 }}>{c.label}</div>
              <div style={{ fontSize: 20, fontWeight: 700 }}>{c.value}</div>
            </div>
          );
        })}
      </div>

      {summary.by_competitor?.length > 0 && (
        <div style={{
          backgroundColor: 'var(--card-bg)', border: '1px solid var(--border)',
          borderRadius: 8, padding: 16,
        }}>
          <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 10 }}>By Competitor</div>
          <div style={{ display: 'flex', gap: 16, flexWrap: 'wrap' }}>
            {summary.by_competitor.map(c => (
              <div key={c.competitor} style={{
                padding: '8px 14px', borderRadius: 6, border: '1px solid var(--border)', fontSize: 12,
              }}>
                <div style={{ fontWeight: 600, marginBottom: 4 }}>{c.competitor}</div>
                <div style={{ color: 'var(--text-muted)' }}>
                  {c.entries} products • Avg gap: <span style={{
                    color: c.avg_diff_pct >= 0 ? '#22c55e' : '#ef4444', fontWeight: 600,
                  }}>{fmtPct(c.avg_diff_pct)}</span>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

// ─── PRICE ENTRY MODAL ──────────────────────────────────────────

function PriceEntryModal({ product, competitors, existingPrices, onSave, onClose }) {
  const [prices, setPrices] = useState({});
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    const initial = {};
    competitors.forEach(c => {
      const existing = existingPrices.find(p => p.competitor === c && p.sku === product.sku);
      initial[c] = {
        price: existing?.competitor_price ?? '',
        url: existing?.competitor_url ?? '',
        in_stock: existing?.competitor_in_stock ?? null,
        notes: existing?.notes ?? '',
      };
    });
    setPrices(initial);
  }, [product, competitors, existingPrices]);

  const handleSave = async () => {
    setSaving(true);
    try {
      for (const competitor of competitors) {
        const data = prices[competitor];
        if (data.price === '' && !data.url && data.in_stock === null) continue;
        await api.upsertCompetitorPrice({
          sku: product.sku, product_title: product.product_title, competitor,
          competitor_price: data.price !== '' ? parseFloat(data.price) : null,
          competitor_url: data.url || null, competitor_in_stock: data.in_stock,
          our_price: product.our_price, notes: data.notes || null,
        });
      }
      onSave();
    } catch (err) { alert('Failed to save: ' + err.message); }
    setSaving(false);
  };

  return (
    <div style={{ position: 'fixed', top: 0, left: 0, right: 0, bottom: 0, backgroundColor: 'rgba(0,0,0,0.5)', zIndex: 1000, display: 'flex', alignItems: 'center', justifyContent: 'center' }} onClick={onClose}>
      <div style={{ backgroundColor: 'var(--card-bg)', borderRadius: 12, padding: 24, width: 600, maxHeight: '80vh', overflowY: 'auto', boxShadow: '0 8px 32px rgba(0,0,0,0.3)' }} onClick={e => e.stopPropagation()}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
          <div>
            <div style={{ fontSize: 15, fontWeight: 600 }}>{product.product_title}</div>
            <div style={{ fontSize: 12, color: 'var(--text-muted)', marginTop: 2 }}>
              SKU: {product.sku} • Our price: {fmt(product.our_price)} • Vendor: {product.vendor}
            </div>
          </div>
          <button onClick={onClose} style={{ border: 'none', background: 'none', cursor: 'pointer', fontSize: 18, color: 'var(--text-muted)' }}>✕</button>
        </div>

        {competitors.map(comp => {
          const data = prices[comp] || {};
          const diff = data.price !== '' && data.price !== undefined ? parseFloat(data.price) - product.our_price : null;
          return (
            <div key={comp} style={{ padding: 12, marginBottom: 8, borderRadius: 8, border: '1px solid var(--border)', backgroundColor: 'var(--hover-bg)' }}>
              <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 8 }}>{comp}</div>
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8 }}>
                <div>
                  <label style={{ fontSize: 11, color: 'var(--text-muted)' }}>Price</label>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                    <input type="number" step="0.01" placeholder="0.00" value={data.price ?? ''}
                      onChange={e => setPrices(p => ({ ...p, [comp]: { ...p[comp], price: e.target.value } }))}
                      style={{ padding: '6px 10px', borderRadius: 6, border: '1px solid var(--border)', backgroundColor: 'var(--input-bg)', color: 'var(--text)', fontSize: 13, width: '100%' }} />
                    {diff !== null && <span style={{ fontSize: 11, fontWeight: 600, whiteSpace: 'nowrap', color: diff > 0 ? '#22c55e' : diff < 0 ? '#ef4444' : '#6b7280' }}>{diff > 0 ? '+' : ''}{fmt(diff)}</span>}
                  </div>
                </div>
                <div>
                  <label style={{ fontSize: 11, color: 'var(--text-muted)' }}>In Stock?</label>
                  <div style={{ display: 'flex', gap: 6, marginTop: 4 }}>
                    {[{ val: true, label: 'Yes', color: '#22c55e' }, { val: false, label: 'No', color: '#ef4444' }, { val: null, label: '?', color: '#6b7280' }].map(opt => (
                      <button key={String(opt.val)} onClick={() => setPrices(p => ({ ...p, [comp]: { ...p[comp], in_stock: opt.val } }))}
                        style={{ padding: '4px 12px', borderRadius: 4, fontSize: 11, fontWeight: 600, cursor: 'pointer', border: '1px solid var(--border)',
                          backgroundColor: data.in_stock === opt.val ? opt.color + '20' : 'transparent', color: data.in_stock === opt.val ? opt.color : 'var(--text-muted)' }}>{opt.label}</button>
                    ))}
                  </div>
                </div>
              </div>
              <div style={{ marginTop: 6 }}>
                <label style={{ fontSize: 11, color: 'var(--text-muted)' }}>URL</label>
                <input type="url" placeholder="https://..." value={data.url ?? ''}
                  onChange={e => setPrices(p => ({ ...p, [comp]: { ...p[comp], url: e.target.value } }))}
                  style={{ padding: '4px 10px', borderRadius: 6, border: '1px solid var(--border)', backgroundColor: 'var(--input-bg)', color: 'var(--text)', fontSize: 12, width: '100%' }} />
              </div>
            </div>
          );
        })}

        <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8, marginTop: 16 }}>
          <button onClick={onClose} style={{ padding: '8px 16px', borderRadius: 6, border: '1px solid var(--border)', cursor: 'pointer', backgroundColor: 'transparent', color: 'var(--text)', fontSize: 13 }}>Cancel</button>
          <button onClick={handleSave} disabled={saving} style={{ padding: '8px 20px', borderRadius: 6, border: 'none', cursor: 'pointer', backgroundColor: '#6366f1', color: '#fff', fontWeight: 600, fontSize: 13, opacity: saving ? 0.6 : 1 }}>
            {saving ? 'Saving...' : 'Save Prices'}
          </button>
        </div>
      </div>
    </div>
  );
}

// ─── MAIN PAGE ──────────────────────────────────────────────────

export default function MarketIntelPage({ onToast }) {
  const [products, setProducts] = useState([]);
  const [competitors, setCompetitors] = useState([]);
  const [prices, setPricesState] = useState([]);
  const [summary, setSummary] = useState(null);
  const [loading, setLoading] = useState(true);
  const [vendorFilter, setVendorFilter] = useState('');
  const [competitorFilter, setCompetitorFilter] = useState('');
  const [priceFilter, setPriceFilter] = useState(null);
  const [search, setSearch] = useState('');
  const [editProduct, setEditProduct] = useState(null);
  const [sortField, setSortField] = useState('revenue_365d');
  const [sortDir, setSortDir] = useState('desc');

  const loadData = async () => {
    setLoading(true);
    try {
      const [prod, comp, pr, sum] = await Promise.all([
        api.fetchMarketProducts(vendorFilter || null, 200),
        api.fetchCompetitors(),
        api.fetchCompetitorPrices(),
        api.fetchMarketSummary(),
      ]);
      setProducts(prod); setCompetitors(comp); setPricesState(pr); setSummary(sum);
    } catch (err) { onToast?.('Failed to load market data: ' + err.message, 'error'); }
    setLoading(false);
  };

  useEffect(() => { loadData(); }, [vendorFilter]);

  const vendors = useMemo(() => [...new Set(products.map(p => p.vendor))].sort(), [products]);

  const priceLookup = useMemo(() => {
    const lookup = {};
    prices.forEach(p => { if (!lookup[p.sku]) lookup[p.sku] = {}; lookup[p.sku][p.competitor] = p; });
    return lookup;
  }, [prices]);

  const filtered = useMemo(() => {
    let items = products;
    if (search) {
      const q = search.toLowerCase();
      items = items.filter(p => p.sku.toLowerCase().includes(q) || p.product_title.toLowerCase().includes(q));
    }
    if (priceFilter) {
      items = items.filter(p => {
        const entries = Object.values(priceLookup[p.sku] || {}).filter(cp => cp?.price_diff != null);
        if (entries.length === 0) return false;
        if (priceFilter === 'we_cheaper') return entries.some(cp => cp.price_diff > 0);
        if (priceFilter === 'they_cheaper') return entries.some(cp => cp.price_diff < 0);
        if (priceFilter === 'same') return entries.some(cp => cp.price_diff === 0);
        return true;
      });
    }
    if (competitorFilter) {
      items = items.filter(p => (priceLookup[p.sku] || {})[competitorFilter]?.competitor_price != null);
    }
    return [...items].sort((a, b) => {
      const av = a[sortField] ?? 0, bv = b[sortField] ?? 0;
      return sortDir === 'asc' ? (av > bv ? 1 : -1) : (av < bv ? 1 : -1);
    });
  }, [products, search, sortField, sortDir, priceFilter, competitorFilter, priceLookup]);

  const toggleSort = (field) => {
    if (sortField === field) setSortDir(d => d === 'asc' ? 'desc' : 'asc');
    else { setSortField(field); setSortDir('desc'); }
  };

  if (loading) return <div style={{ padding: 40, textAlign: 'center', color: 'var(--text-muted)' }}>Loading market intelligence...</div>;

  return (
    <div style={{ padding: 24, maxWidth: 1600 }}>
      <div style={{ marginBottom: 20 }}>
        <h1 style={{ fontSize: 20, fontWeight: 700, margin: 0 }}>Market Intelligence</h1>
        <div style={{ fontSize: 12, color: 'var(--text-muted)', marginTop: 4 }}>Competitor pricing • {competitors.join(' • ')}</div>
      </div>

      <MarketSummary summary={summary} priceFilter={priceFilter} onFilterChange={setPriceFilter} />

      <div style={{ backgroundColor: 'var(--card-bg)', border: '1px solid var(--border)', borderRadius: 8, overflow: 'hidden' }}>
        <div style={{ padding: '16px 20px', borderBottom: '1px solid var(--border)', display: 'flex', gap: 12, alignItems: 'center', flexWrap: 'wrap' }}>
          <input type="text" placeholder="Search SKU or product..." value={search} onChange={e => setSearch(e.target.value)}
            style={{ padding: '6px 12px', borderRadius: 6, border: '1px solid var(--border)', backgroundColor: 'var(--input-bg)', color: 'var(--text)', fontSize: 12, width: 200 }} />
          <select value={vendorFilter} onChange={e => setVendorFilter(e.target.value)}
            style={{ padding: '6px 10px', borderRadius: 6, border: '1px solid var(--border)', backgroundColor: 'var(--input-bg)', color: 'var(--text)', fontSize: 12 }}>
            <option value="">All Vendors</option>
            {vendors.map(v => <option key={v} value={v}>{v}</option>)}
          </select>
          <select value={competitorFilter} onChange={e => setCompetitorFilter(e.target.value)}
            style={{ padding: '6px 10px', borderRadius: 6, border: '1px solid var(--border)', backgroundColor: 'var(--input-bg)', color: 'var(--text)', fontSize: 12 }}>
            <option value="">All Competitors</option>
            {competitors.map(c => <option key={c} value={c}>{c}</option>)}
          </select>
          {(priceFilter || competitorFilter) && (
            <button onClick={() => { setPriceFilter(null); setCompetitorFilter(''); }}
              style={{ padding: '4px 10px', borderRadius: 4, border: '1px solid var(--border)', cursor: 'pointer', backgroundColor: 'transparent', color: 'var(--text-muted)', fontSize: 11 }}>
              ✕ Clear filters
            </button>
          )}
          <div style={{ flex: 1 }} />
          <div style={{ fontSize: 12, color: 'var(--text-muted)' }}>{filtered.length} products</div>
        </div>

        <div style={{ overflowX: 'auto', maxHeight: 700, overflowY: 'auto' }}>
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
            <thead>
              <tr>
                {[
                  { key: 'sku', label: 'SKU', align: 'left' },
                  { key: 'product_title', label: 'Product', align: 'left' },
                  { key: 'vendor', label: 'Vendor', align: 'left' },
                  { key: 'revenue_365d', label: 'Revenue', align: 'right' },
                  { key: 'sold_365d', label: 'Sold', align: 'right' },
                  { key: 'our_price', label: 'Our Price', align: 'right' },
                  ...competitors.map(c => ({ key: `comp_${c}`, label: c.split(' ')[0], align: 'right' })),
                  { key: 'status', label: 'Status', align: 'center' },
                ].map(col => (
                  <th key={col.key} onClick={() => !col.key.startsWith('comp_') && col.key !== 'status' && toggleSort(col.key)}
                    style={{
                      padding: '8px 8px', fontSize: 11, fontWeight: 600, color: 'var(--text-muted)',
                      textAlign: col.align, whiteSpace: 'nowrap', borderBottom: '2px solid var(--border)',
                      position: 'sticky', top: 0, backgroundColor: 'var(--card-bg)',
                      cursor: !col.key.startsWith('comp_') && col.key !== 'status' ? 'pointer' : 'default',
                      borderRight: col.key === 'our_price' ? '2px solid var(--border)' : undefined,
                    }}>
                    {col.label} {sortField === col.key ? (sortDir === 'asc' ? '↑' : '↓') : ''}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {filtered.map((p, i) => {
                const compPrices = priceLookup[p.sku] || {};
                const hasAnyPrice = Object.keys(compPrices).length > 0;
                const diffs = Object.values(compPrices).filter(cp => cp?.price_diff != null).map(cp => cp.price_diff);
                let status = 'untracked';
                if (diffs.length > 0) {
                  const avg = diffs.reduce((a, b) => a + b, 0) / diffs.length;
                  status = avg > 5 ? 'competitive' : avg < -5 ? 'underpriced' : 'at_market';
                }
                const sty = { competitive: { bg: '#d1fae5', fg: '#065f46', label: 'Competitive' }, at_market: { bg: '#f3f4f6', fg: '#374151', label: 'At Market' }, underpriced: { bg: '#fee2e2', fg: '#991b1b', label: 'Underpriced' }, untracked: { bg: 'transparent', fg: 'var(--text-muted)', label: '—' } }[status];

                return (
                  <tr key={p.sku} onClick={() => setEditProduct(p)} style={{ cursor: 'pointer', backgroundColor: i % 2 === 0 ? 'transparent' : 'var(--row-alt)' }}>
                    <td style={{ padding: '6px 8px', fontFamily: 'monospace', fontSize: 11 }}>{p.sku}</td>
                    <td style={{ padding: '6px 8px', maxWidth: 280, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{p.product_title}</td>
                    <td style={{ padding: '6px 8px' }}>{p.vendor}</td>
                    <td style={{ padding: '6px 8px', textAlign: 'right' }}>{fmt(p.revenue_365d)}</td>
                    <td style={{ padding: '6px 8px', textAlign: 'right' }}>{p.sold_365d}</td>
                    <td style={{ padding: '6px 8px', textAlign: 'right', fontWeight: 600, borderRight: '2px solid var(--border)' }}>{fmt(p.our_price)}</td>

                    {competitors.map(c => {
                      const cp = compPrices[c];
                      if (!cp || cp.competitor_price == null) {
                        return <td key={c} style={{ padding: '6px 8px', textAlign: 'right', color: 'var(--text-muted)' }}>—</td>;
                      }
                      const priceEl = <div>{fmt(cp.competitor_price)}</div>;
                      return (
                        <td key={c} style={{ padding: '6px 8px', textAlign: 'right' }}>
                          {cp.competitor_url ? (
                            <a href={cp.competitor_url} target="_blank" rel="noopener noreferrer"
                              onClick={e => e.stopPropagation()}
                              style={{ color: '#6366f1', textDecoration: 'none', fontWeight: 500 }}
                              title={`View on ${c}`}>
                              {fmt(cp.competitor_price)}
                            </a>
                          ) : priceEl}
                          <div style={{ fontSize: 10, fontWeight: 600, color: cp.price_diff > 0 ? '#22c55e' : cp.price_diff < 0 ? '#ef4444' : '#6b7280' }}>
                            {cp.price_diff > 0 ? '+' : ''}{fmt(cp.price_diff)}
                          </div>
                        </td>
                      );
                    })}

                    <td style={{ padding: '6px 8px', textAlign: 'center' }}>
                      {sty.label !== '—' ? (
                        <span style={{ display: 'inline-block', padding: '2px 8px', borderRadius: 4, fontSize: 10, fontWeight: 600, backgroundColor: sty.bg, color: sty.fg }}>{sty.label}</span>
                      ) : <span style={{ color: 'var(--text-muted)' }}>—</span>}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </div>

      {editProduct && (
        <PriceEntryModal product={editProduct} competitors={competitors} existingPrices={prices}
          onSave={() => { setEditProduct(null); loadData(); onToast?.('Competitor prices saved', 'success'); }}
          onClose={() => setEditProduct(null)} />
      )}
    </div>
  );
}
