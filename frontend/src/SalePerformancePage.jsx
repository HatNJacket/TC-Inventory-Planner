import { useState, useEffect, Fragment, useRef } from 'react';
import * as api from './api';

const fmt = (n) => new Intl.NumberFormat('en-CA', { style: 'currency', currency: 'CAD', minimumFractionDigits: 0, maximumFractionDigits: 0 }).format(n || 0);
const fmtFull = (n) => new Intl.NumberFormat('en-CA', { style: 'currency', currency: 'CAD', minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(n || 0);

const todayIso = () => new Date().toISOString().split('T')[0];
const daysAgoIso = (n) => new Date(Date.now() - n * 86400 * 1000).toISOString().split('T')[0];

const inputStyle = {
  padding: '6px 10px', borderRadius: 6, border: '1px solid var(--border)',
  backgroundColor: 'var(--input-bg)', color: 'var(--text)', fontSize: 12, width: '100%',
};


function MarginCell({ value }) {
  const color = value >= 20 ? '#22c55e' : value >= 10 ? '#f97316' : value >= 0 ? '#eab308' : '#ef4444';
  return <span style={{ color, fontWeight: 600 }}>{value}%</span>;
}




// --- MAIN PAGE ---
export default function SalePerformancePage({ onToast }) {
  const [startDate, setStartDate] = useState(daysAgoIso(90));
  const [endDate, setEndDate] = useState(todayIso());
  const [vendor, setVendor] = useState('');
  const [method, setMethod] = useState('revenue');
  const [sortBy, setSortBy] = useState('profit');
  const [limit, setLimit] = useState(25);
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);

  const load = async () => {
    setLoading(true);
    try {
      const res = await api.fetchSkuLeaderboard({
        startDate, endDate, vendor: vendor || undefined,
        method, sortBy, limit,
      });
      setData(res);
    } catch (err) {
      onToast?.('Failed to load leaderboard: ' + err.message, 'error');
    }
    setLoading(false);
  };

  useEffect(() => { load(); }, [startDate, endDate, vendor, method, sortBy, limit]);

  const totals = data?.totals || {};
  const skus = data?.skus || [];

  return (
    <div style={{ padding: 24, maxWidth: 1500 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 20 }}>
        <div>
          <h1 style={{ fontSize: 20, fontWeight: 700, margin: 0 }}>Sale Performance</h1>
          <div style={{ fontSize: 12, color: 'var(--text-muted)', marginTop: 4 }}>
            Fully-loaded margin per SKU — FIFO COGS plus allocated operating expenses.
          </div>
        </div>
        <div style={{ display: 'flex', gap: 8, alignItems: 'flex-end' }}>
          <div>
            <div style={{ fontSize: 10, color: 'var(--text-muted)', marginBottom: 2 }}>From</div>
            <input type="date" value={startDate} onChange={e => setStartDate(e.target.value)} style={{ ...inputStyle, width: 140 }} />
          </div>
          <div>
            <div style={{ fontSize: 10, color: 'var(--text-muted)', marginBottom: 2 }}>To</div>
            <input type="date" value={endDate} onChange={e => setEndDate(e.target.value)} style={{ ...inputStyle, width: 140 }} />
          </div>
          <div>
            <div style={{ fontSize: 10, color: 'var(--text-muted)', marginBottom: 2 }}>Vendor</div>
            <input value={vendor} onChange={e => setVendor(e.target.value)} placeholder="all" style={{ ...inputStyle, width: 120 }} />
          </div>
          <div>
            <div style={{ fontSize: 10, color: 'var(--text-muted)', marginBottom: 2 }}>Allocation</div>
            <select value={method} onChange={e => setMethod(e.target.value)} style={{ ...inputStyle, width: 120 }}>
              <option value="revenue">Revenue share</option>
              <option value="units">Units share</option>
            </select>
          </div>
        </div>
      </div>

      {/* Summary Cards */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(6, 1fr)', gap: 12, marginBottom: 24 }}>
        {[
          { label: 'SKUs', value: totals.sku_count, color: '#6366f1', isCurrency: false },
          { label: 'Revenue', value: totals.revenue, color: '#22c55e', isCurrency: true },
          { label: 'COGS (FIFO)', value: totals.cogs, color: '#f97316', isCurrency: true },
          { label: 'Overhead allocated', value: totals.allocated_overhead, color: '#ef4444', isCurrency: true },
          { label: 'Fully-loaded profit', value: totals.fully_loaded_profit, color: '#22c55e', isCurrency: true },
          { label: 'Margin %', value: totals.fully_loaded_margin_pct != null ? totals.fully_loaded_margin_pct + '%' : '—', color: '#8b5cf6', isCurrency: false },
        ].map((c, i) => (
          <div key={i} style={{ padding: 16, borderRadius: 8, border: '1px solid var(--border)',
            backgroundColor: 'var(--card-bg)', borderLeft: '4px solid ' + c.color }}>
            <div style={{ fontSize: 11, color: 'var(--text-muted)', marginBottom: 4 }}>{c.label}</div>
            <div style={{ fontSize: 22, fontWeight: 700 }}>{c.isCurrency ? fmt(c.value) : (c.value || 0)}</div>
          </div>
        ))}
      </div>

      {data?.overhead_by_category?.length > 0 && (
        <div style={{ marginBottom: 16, fontSize: 12, color: 'var(--text-muted)' }}>
          <strong style={{ color: 'var(--text)' }}>Overhead allocated this window:</strong>{' '}
          {data.overhead_by_category.map(c => c.category + ' ' + fmt(c.amount_cad)).join(' · ')}
        </div>
      )}
      {totals.unmatched_units > 0 && (
        <div style={{ marginBottom: 16, padding: '8px 12px', borderRadius: 6, backgroundColor: '#fef2f2',
          color: '#dc2626', fontSize: 12, border: '1px solid #fecaca' }}>
          ⚠ {totals.unmatched_units} units / {fmtFull(totals.unmatched_revenue)} of revenue couldn't be costed (no purchase lots covered them at order time). Margins below treat their COGS as $0 — load older invoices and re-run FIFO to fix.
        </div>
      )}

      <div style={{ marginBottom: 16, padding: '8px 12px', borderRadius: 6, fontSize: 12,
        backgroundColor: 'var(--hover-bg)', color: 'var(--text-muted)', border: '1px solid var(--border)' }}>
        Manage operating expenses and recurring templates on the <strong style={{ color: 'var(--text)' }}>COGS Tracker → Operating Expenses</strong> tab.
      </div>

      {/* Leaderboard */}
      <div style={{ backgroundColor: 'var(--card-bg)', border: '1px solid var(--border)', borderRadius: 8, overflow: 'hidden' }}>
        <div style={{ padding: '12px 16px', borderBottom: '1px solid var(--border)', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <div style={{ fontSize: 14, fontWeight: 600 }}>SKU Leaderboard</div>
          <div style={{ display: 'flex', gap: 8, alignItems: 'center', fontSize: 12 }}>
            <span style={{ color: 'var(--text-muted)' }}>Sort by</span>
            <select value={sortBy} onChange={e => setSortBy(e.target.value)} style={{
              padding: '4px 8px', borderRadius: 4, border: '1px solid var(--border)',
              backgroundColor: 'var(--input-bg)', color: 'var(--text)', fontSize: 12,
            }}>
              <option value="profit">Top profit (fully loaded)</option>
              <option value="loss">Worst profit / loss</option>
              <option value="margin">Best margin %</option>
              <option value="revenue">Top revenue</option>
              <option value="units">Top units</option>
            </select>
            <span style={{ color: 'var(--text-muted)' }}>Limit</span>
            <select value={limit} onChange={e => setLimit(Number(e.target.value))} style={{
              padding: '4px 8px', borderRadius: 4, border: '1px solid var(--border)',
              backgroundColor: 'var(--input-bg)', color: 'var(--text)', fontSize: 12,
            }}>
              <option value={10}>10</option>
              <option value={25}>25</option>
              <option value={50}>50</option>
              <option value={100}>100</option>
            </select>
          </div>
        </div>
        <div style={{ overflowX: 'auto' }}>
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
            <thead>
              <tr>
                {['#', 'SKU', 'Title', 'Vendor', 'Units', 'Sale lots', 'Revenue', 'COGS', 'Overhead', 'Gross profit', 'Loaded profit', 'Loaded margin %'].map(h => (
                  <th key={h} style={{ padding: '8px 10px', fontSize: 11, fontWeight: 600, color: 'var(--text-muted)',
                    textAlign: ['SKU', 'Title', 'Vendor', '#'].includes(h) ? 'left' : 'right',
                    borderBottom: '2px solid var(--border)' }}>{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {loading && (
                <tr><td colSpan={12} style={{ padding: 24, textAlign: 'center', color: 'var(--text-muted)' }}>Loading...</td></tr>
              )}
              {!loading && skus.length === 0 && (
                <tr><td colSpan={12} style={{ padding: 24, textAlign: 'center', color: 'var(--text-muted)' }}>
                  No matched sales in this window. Run FIFO match on the COGS Tracker page first.
                </td></tr>
              )}
              {skus.map((s, i) => (
                <tr key={s.sku} style={{ backgroundColor: i % 2 === 0 ? 'transparent' : 'var(--row-alt)' }}>
                  <td style={{ padding: '6px 10px', color: 'var(--text-muted)' }}>{i + 1}</td>
                  <td style={{ padding: '6px 10px', fontFamily: 'monospace', fontSize: 11 }}>{s.sku}</td>
                  <td style={{ padding: '6px 10px', maxWidth: 280, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{s.title || '—'}</td>
                  <td style={{ padding: '6px 10px', color: 'var(--text-muted)' }}>{s.vendor || '—'}</td>
                  <td style={{ padding: '6px 10px', textAlign: 'right' }}>{s.units}</td>
                  <td style={{ padding: '6px 10px', textAlign: 'right' }}>
                    {s.vendor_sale_units > 0 ? (
                      <span style={{ padding: '2px 6px', borderRadius: 4, fontSize: 10, fontWeight: 600,
                        backgroundColor: '#f3e8ff', color: '#a855f7' }}>
                        {s.vendor_sale_units} ({s.vendor_sale_share_pct}%)
                      </span>
                    ) : '—'}
                  </td>
                  <td style={{ padding: '6px 10px', textAlign: 'right', fontWeight: 600 }}>{fmt(s.revenue)}</td>
                  <td style={{ padding: '6px 10px', textAlign: 'right' }}>{fmt(s.cogs)}</td>
                  <td style={{ padding: '6px 10px', textAlign: 'right', color: '#94a3b8' }}>{fmt(s.allocated_overhead)}</td>
                  <td style={{ padding: '6px 10px', textAlign: 'right',
                    color: s.gross_profit >= 0 ? '#22c55e' : '#ef4444' }}>{fmt(s.gross_profit)}</td>
                  <td style={{ padding: '6px 10px', textAlign: 'right', fontWeight: 600,
                    color: s.fully_loaded_profit >= 0 ? '#22c55e' : '#ef4444' }}>{fmt(s.fully_loaded_profit)}</td>
                  <td style={{ padding: '6px 10px', textAlign: 'right' }}>
                    <MarginCell value={s.fully_loaded_margin_pct} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
