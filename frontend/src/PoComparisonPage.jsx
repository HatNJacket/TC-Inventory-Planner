import { useState, useMemo } from 'react';
import * as api from './api';

const fmt = (n) => new Intl.NumberFormat('en-CA', { minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(n);
const fmtMoney = (n) => '$' + fmt(n || 0);
const fmtInt = (n) => new Intl.NumberFormat('en-CA').format(n || 0);

const VERDICT_STYLES = {
  order:        { bg: '#d1fae5', color: '#065f46', label: 'Order',      description: 'Meets all thresholds — good reorder candidate' },
  overstocked:  { bg: '#fef3c7', color: '#92400e', label: 'Overstock',  description: 'Has plenty of stock already — skip reorder' },
  margin_low:   { bg: '#fef3c7', color: '#92400e', label: 'Low margin', description: 'Below margin threshold — review manually' },
  skip:         { bg: '#fee2e2', color: '#991b1b', label: 'Skip',       description: 'Zero sales, Special Order, or dead stock' },
  no_data:      { bg: '#e5e7eb', color: '#374151', label: 'Unknown',    description: 'SKU not in velocity cache — can\'t evaluate' },
};

const AGREEMENT_STYLES = {
  both_agree:   { bg: '#e0f2fe', color: '#075985', label: 'Both' },
  qty_differs:  { bg: '#ede9fe', color: '#5b21b6', label: 'Qty diff' },
  ip_only:      { bg: '#fef3c7', color: '#92400e', label: 'IP only' },
  tc_only:      { bg: '#d1fae5', color: '#065f46', label: 'TC only' },
};

const s = {
  card: { backgroundColor: 'var(--card-bg, #fff)', border: '1px solid var(--border)', borderRadius: 8, padding: '16px 20px', marginBottom: 16 },
  btn: (variant) => ({
    padding: '7px 16px', borderRadius: 6, border: 'none', cursor: 'pointer', fontWeight: 600, fontSize: 12,
    backgroundColor: variant === 'primary' ? '#6366f1' : variant === 'danger' ? '#ef4444' : '#e2e8f0',
    color: variant === 'primary' || variant === 'danger' ? '#fff' : 'var(--text)',
  }),
  th: { padding: '8px 10px', fontSize: 11, fontWeight: 600, color: 'var(--text-muted)', textAlign: 'left', borderBottom: '2px solid var(--border)', position: 'sticky', top: 0, backgroundColor: 'var(--card-bg, #fff)', zIndex: 1 },
  td: { padding: '6px 10px', fontSize: 12, borderBottom: '1px solid var(--border)', verticalAlign: 'top' },
};

export default function PoComparisonPage({ onToast }) {
  const [file, setFile] = useState(null);
  const [vendorFilter, setVendorFilter] = useState('');
  const [result, setResult] = useState(null);
  const [loading, setLoading] = useState(false);
  const [verdictFilter, setVerdictFilter] = useState('');
  const [agreementFilter, setAgreementFilter] = useState('');

  const runCompare = async () => {
    if (!file) return;
    setLoading(true);
    setResult(null);
    try {
      const data = await api.comparePO(file, { vendor: vendorFilter || null });
      setResult(data);
      onToast?.(`Compared ${data.summary.ip_total_skus} IP items against TC`, 'success');
    } catch (e) {
      onToast?.('Comparison failed: ' + e.message, 'error');
    }
    setLoading(false);
  };

  const filtered = useMemo(() => {
    if (!result) return [];
    let rows = result.rows;
    if (verdictFilter) rows = rows.filter(r => r.verdict === verdictFilter);
    if (agreementFilter) rows = rows.filter(r => r.agreement === agreementFilter);
    return rows;
  }, [result, verdictFilter, agreementFilter]);

  return (
    <div style={{ padding: '24px 28px', overflow: 'auto', height: '100vh' }}>
      <div style={{ marginBottom: 20 }}>
        <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>IP vs TC PO Comparison</h1>
        <div style={{ fontSize: 12, color: 'var(--text-muted)', marginTop: 4 }}>
          Upload an Inventory Planner purchase order export. We'll compare it to TC's recommendations and show, per SKU, what each system suggests and why.
        </div>
      </div>

      {/* Upload panel */}
      <div style={s.card}>
        <div style={{ display: 'flex', gap: 12, alignItems: 'flex-end', flexWrap: 'wrap' }}>
          <div style={{ flex: 1, minWidth: 280 }}>
            <label style={{ fontSize: 11, fontWeight: 600, color: 'var(--text-muted)', display: 'block', marginBottom: 3 }}>IP PO Export (CSV or XLSX)</label>
            <input type="file" accept=".csv,.xlsx,.xls,.txt" onChange={e => { setFile(e.target.files?.[0] || null); setResult(null); }}
              style={{ fontSize: 13 }} />
          </div>
          <div style={{ width: 200 }}>
            <label style={{ fontSize: 11, fontWeight: 600, color: 'var(--text-muted)', display: 'block', marginBottom: 3 }}>Vendor filter (optional)</label>
            <input value={vendorFilter} onChange={e => setVendorFilter(e.target.value)} placeholder="e.g. Celestron"
              style={{ padding: '6px 10px', borderRadius: 6, border: '1px solid var(--border)', fontSize: 13, width: '100%', boxSizing: 'border-box' }} />
          </div>
          <button onClick={runCompare} disabled={!file || loading} style={{ ...s.btn('primary') }}>
            {loading ? 'Comparing...' : 'Compare'}
          </button>
        </div>
      </div>

      {result && (
        <>
          {/* Summary */}
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 12, marginBottom: 16 }}>
            <SummaryCard label="IP Recommends" value={`${fmtInt(result.summary.ip_total_units)} units`}
              sub={`${result.summary.ip_total_skus} SKUs · ${fmtMoney(result.summary.ip_total_cost)}`} />
            <SummaryCard label="TC Recommends" value={`${fmtInt(result.summary.tc_total_units)} units`}
              sub={`${result.summary.tc_total_skus} SKUs in increase-buying list`} />
            <SummaryCard label="IP Only (not in TC)" value={fmtInt(result.summary.skus_ip_only)}
              sub={result.summary.ip_skip_dollars > 0 ? `${fmtMoney(result.summary.ip_skip_dollars)} IP wants that we'd skip` : 'All IP-only items reviewed'}
              color={result.summary.ip_skip_dollars > 10000 ? '#ef4444' : null} />
            <SummaryCard label="Both Agree" value={fmtInt(result.summary.skus_in_both)}
              sub={`${result.summary.skus_tc_only} TC only`} />
          </div>

          {/* Verdict counts */}
          <div style={s.card}>
            <div style={{ fontSize: 13, fontWeight: 700, marginBottom: 10 }}>Verdicts</div>
            <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
              {['order', 'overstocked', 'margin_low', 'skip', 'no_data'].map(v => {
                const st = VERDICT_STYLES[v];
                const count = result.summary.verdict_counts[v] || 0;
                const active = verdictFilter === v;
                return (
                  <button key={v} onClick={() => setVerdictFilter(active ? '' : v)}
                    title={st.description}
                    style={{ padding: '8px 14px', borderRadius: 6, border: active ? '2px solid ' + st.color : '1px solid var(--border)',
                      backgroundColor: active ? st.bg : '#fff', color: active ? st.color : 'var(--text)',
                      fontSize: 12, fontWeight: 600, cursor: 'pointer' }}>
                    {st.label}: {count}
                  </button>
                );
              })}
              {(verdictFilter || agreementFilter) && (
                <button onClick={() => { setVerdictFilter(''); setAgreementFilter(''); }}
                  style={{ padding: '8px 14px', borderRadius: 6, border: '1px solid var(--border)', backgroundColor: 'transparent',
                    fontSize: 11, color: 'var(--text-muted)', cursor: 'pointer' }}>Clear filters</button>
              )}
            </div>
            <div style={{ marginTop: 10, display: 'flex', gap: 8, flexWrap: 'wrap' }}>
              {['ip_only', 'qty_differs', 'both_agree', 'tc_only'].map(a => {
                const st = AGREEMENT_STYLES[a];
                const count = result.rows.filter(r => r.agreement === a).length;
                const active = agreementFilter === a;
                return (
                  <button key={a} onClick={() => setAgreementFilter(active ? '' : a)}
                    style={{ padding: '6px 12px', borderRadius: 4, border: active ? '2px solid ' + st.color : '1px solid var(--border)',
                      backgroundColor: active ? st.bg : '#fff', color: active ? st.color : 'var(--text)',
                      fontSize: 11, fontWeight: 500, cursor: 'pointer' }}>
                    {st.label}: {count}
                  </button>
                );
              })}
            </div>
            <div style={{ marginTop: 8, fontSize: 10, color: 'var(--text-muted)' }}>
              Using thresholds: max DOS {result.thresholds.buy_more_max_dos} · min velocity {result.thresholds.buy_more_min_velocity}/day · min margin {result.thresholds.buy_more_min_margin_pct}%
            </div>
          </div>

          {/* Table */}
          <div style={s.card}>
            <div style={{ fontSize: 13, fontWeight: 700, marginBottom: 10 }}>
              Per-SKU Comparison ({filtered.length} {filtered.length === 1 ? 'row' : 'rows'})
            </div>
            <div style={{ maxHeight: 700, overflow: 'auto', border: '1px solid var(--border)', borderRadius: 6 }}>
              <table style={{ width: '100%', borderCollapse: 'collapse' }}>
                <thead>
                  <tr>
                    <th style={s.th}>SKU</th>
                    <th style={s.th}>Product</th>
                    <th style={{ ...s.th, textAlign: 'center', width: 80 }}>IP</th>
                    <th style={{ ...s.th, textAlign: 'center', width: 80 }}>TC</th>
                    <th style={{ ...s.th, textAlign: 'right', width: 70 }}>Stock</th>
                    <th style={{ ...s.th, textAlign: 'right', width: 65 }}>On order</th>
                    <th style={{ ...s.th, textAlign: 'right', width: 80 }} title="Sales in last 30/90/365 days">30/90/365d</th>
                    <th style={{ ...s.th, textAlign: 'right', width: 65 }}>DOS</th>
                    <th style={{ ...s.th, textAlign: 'right', width: 70 }}>Margin</th>
                    <th style={{ ...s.th, textAlign: 'right', width: 90 }}>IP $</th>
                    <th style={{ ...s.th, width: 100 }}>Verdict</th>
                    <th style={s.th}>Why</th>
                  </tr>
                </thead>
                <tbody>
                  {filtered.length === 0 ? (
                    <tr><td colSpan={12} style={{ padding: 30, textAlign: 'center', color: 'var(--text-muted)' }}>
                      No rows match current filters.
                    </td></tr>
                  ) : filtered.map((r, i) => {
                    const v = r.velocity;
                    const verdictStyle = VERDICT_STYLES[r.verdict] || VERDICT_STYLES.no_data;
                    const agreeStyle = AGREEMENT_STYLES[r.agreement] || {};
                    const ipDollars = r.ip_qty * (v?.cost || 0);
                    return (
                      <tr key={i} style={{ backgroundColor: i % 2 === 0 ? 'transparent' : 'var(--row-alt, #fafbfc)' }}>
                        <td style={{ ...s.td, fontFamily: 'monospace', fontSize: 11, fontWeight: 500 }}>
                          {r.sku}
                          {r.agreement && (
                            <div style={{ marginTop: 3, display: 'inline-block', padding: '1px 6px', borderRadius: 3, fontSize: 9,
                              backgroundColor: agreeStyle.bg, color: agreeStyle.color, fontWeight: 600 }}>
                              {agreeStyle.label}
                            </div>
                          )}
                        </td>
                        <td style={{ ...s.td, maxWidth: 240, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} title={r.title || ''}>
                          {r.title || '—'}
                          {v?.tags && /special order|istock-preorder/i.test(v.tags) && (
                            <div style={{ fontSize: 9, color: '#92400e', fontWeight: 600, marginTop: 2 }}>
                              ⚑ {v.tags.match(/special order|istock-preorder/gi)?.join(' · ')}
                            </div>
                          )}
                        </td>
                        <td style={{ ...s.td, textAlign: 'center', fontWeight: 600, color: r.ip_qty > 0 ? '#92400e' : 'var(--text-muted)' }}>{r.ip_qty || '—'}</td>
                        <td style={{ ...s.td, textAlign: 'center', fontWeight: 600, color: r.tc_qty > 0 ? '#065f46' : 'var(--text-muted)' }}>{r.tc_qty || '—'}</td>
                        <td style={{ ...s.td, textAlign: 'right' }}>{v?.current_stock ?? '—'}</td>
                        <td style={{ ...s.td, textAlign: 'right', color: r.on_order > 0 ? '#6366f1' : 'var(--text-muted)' }}>{r.on_order || '—'}</td>
                        <td style={{ ...s.td, textAlign: 'right', fontSize: 11, color: 'var(--text-muted)' }}>
                          {v ? `${v.total_sold_30d}/${v.total_sold_90d}/${v.total_sold_365d}` : '—'}
                        </td>
                        <td style={{ ...s.td, textAlign: 'right' }}>
                          {v ? (v.days_of_stock >= 365 ? '∞' : Math.round(v.days_of_stock)) : '—'}
                        </td>
                        <td style={{ ...s.td, textAlign: 'right' }}>
                          {v ? `${v.margin_pct.toFixed(1)}%` : '—'}
                        </td>
                        <td style={{ ...s.td, textAlign: 'right', color: ipDollars > 1000 ? '#92400e' : 'var(--text-muted)', fontWeight: ipDollars > 1000 ? 600 : 400 }}>
                          {ipDollars > 0 ? fmtMoney(ipDollars) : '—'}
                        </td>
                        <td style={s.td}>
                          <span style={{ padding: '2px 8px', borderRadius: 10, fontSize: 10, fontWeight: 700,
                            backgroundColor: verdictStyle.bg, color: verdictStyle.color }}>
                            {verdictStyle.label}
                          </span>
                        </td>
                        <td style={{ ...s.td, fontSize: 11, color: 'var(--text-muted)', maxWidth: 300 }}>
                          {r.reasons.join(' · ')}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </div>
        </>
      )}
    </div>
  );
}

function SummaryCard({ label, value, sub, color }) {
  return (
    <div style={s.card}>
      <div style={{ fontSize: 11, color: 'var(--text-muted)', fontWeight: 600 }}>{label}</div>
      <div style={{ fontSize: 22, fontWeight: 700, marginTop: 4, color: color || 'var(--text)' }}>{value}</div>
      <div style={{ fontSize: 11, color: 'var(--text-muted)', marginTop: 2 }}>{sub}</div>
    </div>
  );
}
