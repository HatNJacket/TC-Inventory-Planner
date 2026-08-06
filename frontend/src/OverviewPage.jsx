import { useState, useEffect, useCallback } from 'react';
import * as api from './api';

const fmt = (n) => new Intl.NumberFormat('en-CA', { style: 'currency', currency: 'CAD', minimumFractionDigits: 0, maximumFractionDigits: 0 }).format(n);
const fmtFull = (n) => new Intl.NumberFormat('en-CA', { style: 'currency', currency: 'CAD', minimumFractionDigits: 2 }).format(n);
const fmtNum = (n) => new Intl.NumberFormat('en-CA').format(n);

const DONUT_COLORS = ['#1a7e5a', '#3498db', '#e74c3c', '#9b59b6', '#e67e22', '#1abc9c', '#f39c12', '#2ecc71', '#e91e63', '#00bcd4'];

function DonutChart({ data, size = 160, thickness = 24 }) {
  const [hovered, setHovered] = useState(null);
  const total = data.reduce((s, d) => s + d.value, 0);
  if (total === 0) return null;
  const r = (size - thickness) / 2;
  const cx = size / 2, cy = size / 2;
  let cum = 0;
  const arcs = data.slice(0, 10).map((d, i) => {
    const pct = d.value / total;
    const s = cum * 2 * Math.PI - Math.PI / 2;
    cum += pct;
    const e = cum * 2 * Math.PI - Math.PI / 2;
    const mid = (s + e) / 2;
    return { path: `M ${cx + r * Math.cos(s)} ${cy + r * Math.sin(s)} A ${r} ${r} 0 ${pct > 0.5 ? 1 : 0} 1 ${cx + r * Math.cos(e)} ${cy + r * Math.sin(e)}`, color: DONUT_COLORS[i], midAngle: mid, ...d, index: i };
  });
  const display = hovered !== null ? arcs[hovered] : arcs[0];
  return (
    <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} style={{ cursor: 'pointer' }}>
      {arcs.map((a, i) => (
        <path key={i} d={a.path} fill="none" stroke={a.color}
          strokeWidth={hovered === i ? thickness + 6 : thickness}
          style={{ transition: 'stroke-width 0.15s ease' }}
          onMouseEnter={() => setHovered(i)}
          onMouseLeave={() => setHovered(null)} />
      ))}
      {display && <>
        <text x={cx} y={cy - 5} textAnchor="middle" style={{ fontSize: 10, fill: '#555', fontWeight: 500, pointerEvents: 'none' }}>{display.vendor}</text>
        <text x={cx} y={cy + 11} textAnchor="middle" style={{ fontSize: 12, fill: '#333', fontWeight: 700, pointerEvents: 'none' }}>{fmt(display.value)}</text>
      </>}
    </svg>
  );
}

function StatRow({ label, value }) {
  return (
    <div style={{ display: 'flex', justifyContent: 'space-between', padding: '8px 0' }}>
      <span style={{ fontSize: 13, color: 'var(--text-light)' }}>{label}</span>
      <span style={{ fontSize: 14, fontWeight: 600 }}>{value}</span>
    </div>
  );
}

function Card({ title, children, style: s }) {
  return (
    <div style={{ backgroundColor: 'var(--white)', borderRadius: 10, border: '1px solid var(--border)', padding: '20px 24px', ...s }}>
      <h3 style={{ fontSize: 16, fontWeight: 700, margin: '0 0 16px 0' }}>{title}</h3>
      {children}
    </div>
  );
}

export default function OverviewPage({ onNavigate, onToast }) {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    (async () => {
      try {
        const d = await api.getOverview();
        setData(d);
      } catch (err) { onToast('Failed to load overview: ' + err.message, 'error'); }
      setLoading(false);
    })();
  }, [onToast]);

  if (loading) return <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: '100vh', gap: 12 }}><div className="spinner" /><span style={{ color: 'var(--text-light)' }}>Loading overview...</span></div>;
  if (!data) return <div style={{ padding: 40, textAlign: 'center', color: 'var(--text-light)' }}>No data. Run a Refresh on the Replenishment page first.</div>;

  const { stock, purchases, top_replenish, replenishment, overstock, top_overstock, best_sellers, sales, forecast } = data;
  const now = new Date();
  const dateRange = `${now.toLocaleDateString('en-CA', { month: 'short', day: '2-digit', year: 'numeric' })} - ${new Date(now.getTime() + 365*86400000).toLocaleDateString('en-CA', { month: 'short', day: '2-digit', year: 'numeric' })}`;

  return (
    <div style={{ padding: '24px 28px', overflow: 'auto', height: '100vh' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 16, marginBottom: 24 }}>
        <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>Overview</h1>
        <span style={{ fontSize: 13, color: 'var(--text-light)', backgroundColor: '#f0f1f3', padding: '4px 12px', borderRadius: 6 }}>Telescopes Canada Warehouse</span>
      </div>

      {/* Row 1: Stock + Purchases */}
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 20, marginBottom: 20 }}>
        <Card title="Stock">
          <div style={{ display: 'flex', justifyContent: 'space-between' }}>
            <div style={{ flex: 1 }}>
              <StatRow label="Variants" value={fmtNum(stock.variants)} />
              <StatRow label="Variants in stock" value={fmtNum(stock.in_stock)} />
              <StatRow label="Stock units" value={fmtNum(stock.units)} />
              <StatRow label="Stock cost" value={fmt(stock.cost)} />
              <StatRow label="Stock retail" value={fmt(stock.retail)} />
            </div>
            <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', marginLeft: 16 }}>
              <span style={{ fontSize: 11, color: 'var(--text-muted)', marginBottom: 4 }}>Stock retail by vendor</span>
              <DonutChart data={stock.vendors || []} />
            </div>
          </div>
        </Card>

        <Card title="Purchases">
          <div style={{ display: 'flex', justifyContent: 'space-between' }}>
            <div style={{ flex: 1 }}>
              <StatRow label="Active purchase orders" value={purchases.active} />
              <StatRow label="Overdue purchase orders" value={purchases.overdue} />
              <StatRow label="On order units" value={fmtNum(purchases.on_order_units)} />
              <StatRow label="On order cost" value={fmt(purchases.on_order_cost)} />
            </div>
            <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', marginLeft: 16 }}>
              <span style={{ fontSize: 11, color: 'var(--text-muted)', marginBottom: 4 }}>On order cost by vendor</span>
              <DonutChart data={purchases.vendors || []} />
            </div>
          </div>
          <div style={{ display: 'flex', justifyContent: 'flex-end', marginTop: 12 }}>
            <button onClick={() => onNavigate('stockorders')} style={{ padding: '7px 18px', borderRadius: 6, border: '2px solid var(--green)', backgroundColor: 'transparent', color: 'var(--green)', fontWeight: 600, fontSize: 13, cursor: 'pointer' }}>Purchase orders</button>
          </div>
        </Card>
      </div>

      {/* Row 2: Top replenish + Replenishment & Forecast */}
      <div style={{ display: 'grid', gridTemplateColumns: '1.5fr 1fr', gap: 20, marginBottom: 20 }}>
        <Card title="Top variants to replenish">
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
            <thead><tr style={{ borderBottom: '1px solid var(--border)' }}>
              <th style={{ padding: '6px 4px', textAlign: 'left', color: 'var(--text-light)', fontWeight: 500 }}>#</th>
              <th style={{ padding: '6px 4px', textAlign: 'left', color: 'var(--text-light)', fontWeight: 500 }}>Title</th>
              <th style={{ padding: '6px 4px', textAlign: 'right', color: 'var(--text-light)', fontWeight: 500 }}>Stock</th>
              <th style={{ padding: '6px 4px', textAlign: 'right', color: 'var(--text-light)', fontWeight: 500 }}>Replenish</th>
              <th style={{ padding: '6px 4px', textAlign: 'right', color: 'var(--text-light)', fontWeight: 500 }}>Cost</th>
              <th style={{ padding: '6px 4px', textAlign: 'right', color: 'var(--text-light)', fontWeight: 500 }}>Retail</th>
            </tr></thead>
            <tbody>
              {(top_replenish || []).map((p, i) => (
                <tr key={i} style={{ borderBottom: '1px solid var(--border)' }}>
                  <td style={{ padding: '8px 4px', color: 'var(--text-light)' }}>{i + 1}</td>
                  <td style={{ padding: '8px 4px' }}>
                    <a href="#" onClick={e => { e.preventDefault(); onNavigate('replenishment'); }} style={{ color: 'var(--green)', textDecoration: 'none', fontWeight: 500 }}>{p.title}</a>
                    <div style={{ fontSize: 11, color: 'var(--text-muted)' }}>Planning Period: {p.planning_start}-{p.planning_end}</div>
                  </td>
                  <td style={{ padding: '8px 4px', textAlign: 'right' }}>{p.stock}</td>
                  <td style={{ padding: '8px 4px', textAlign: 'right', fontWeight: 600 }}>{p.replenish}</td>
                  <td style={{ padding: '8px 4px', textAlign: 'right' }}>{fmt(p.cost)}</td>
                  <td style={{ padding: '8px 4px', textAlign: 'right' }}>{fmt(p.retail)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <div style={{ display: 'flex', justifyContent: 'center', marginTop: 14 }}>
            <button onClick={() => onNavigate('replenishment')} style={{ padding: '7px 18px', borderRadius: 6, border: '2px solid var(--green)', backgroundColor: 'transparent', color: 'var(--green)', fontWeight: 600, fontSize: 13, cursor: 'pointer' }}>Replenishment report</button>
          </div>
        </Card>

        <div style={{ display: 'flex', flexDirection: 'column', gap: 20 }}>
          <Card title="Replenishment">
            <StatRow label={'\uD83D\uDD50 Replenishment units'} value={fmtNum(replenishment.units)} />
            <StatRow label={'\uD83D\uDCB0 Replenishment cost'} value={fmt(replenishment.cost)} />
            <StatRow label={'\uD83C\uDFF7\uFE0F Replenishment retail'} value={fmt(replenishment.retail)} />
          </Card>
          <Card title="Forecast">
            <div style={{ display: 'flex', justifyContent: 'flex-end', marginTop: -36, marginBottom: 12 }}>
              <span style={{ padding: '4px 12px', borderRadius: 6, border: '1px solid var(--green)', color: 'var(--green)', fontSize: 11, fontWeight: 500 }}>{'\uD83D\uDCC5'} {dateRange}</span>
            </div>
            <StatRow label={'\uD83D\uDCC8 Forecast sales'} value={fmtNum(forecast.sales)} />
            <StatRow label={'\uD83D\uDCCA Forecast revenue'} value={fmt(forecast.revenue)} />
          </Card>
        </div>
      </div>

      {/* Row 3: Overstock + Top overstock */}
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1.5fr', gap: 20, marginBottom: 20 }}>
        <Card title="Overstock">
          <StatRow label="Overstocked variants" value={fmtNum(overstock.variants)} />
          <StatRow label="Overstocked units" value={fmtNum(overstock.units)} />
          <StatRow label="Overstocked cost" value={fmt(overstock.cost)} />
          <StatRow label="Overstocked retail" value={fmt(overstock.retail)} />
        </Card>

        <Card title="Top overstocked variants">
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
            <thead><tr style={{ borderBottom: '1px solid var(--border)' }}>
              <th style={{ padding: '6px 4px', textAlign: 'left', color: 'var(--text-light)', fontWeight: 500 }}>#</th>
              <th style={{ padding: '6px 4px', textAlign: 'left', color: 'var(--text-light)', fontWeight: 500 }}>Title</th>
              <th style={{ padding: '6px 4px', textAlign: 'right', color: 'var(--text-light)', fontWeight: 500 }}>Stock</th>
              <th style={{ padding: '6px 4px', textAlign: 'right', color: 'var(--text-light)', fontWeight: 500 }}>Cost</th>
              <th style={{ padding: '6px 4px', textAlign: 'right', color: 'var(--text-light)', fontWeight: 500 }}>Retail</th>
            </tr></thead>
            <tbody>
              {(top_overstock || []).map((p, i) => (
                <tr key={i} style={{ borderBottom: '1px solid var(--border)' }}>
                  <td style={{ padding: '8px 4px', color: 'var(--text-light)' }}>{i + 1}</td>
                  <td style={{ padding: '8px 4px', fontWeight: 500 }}>{p.title}</td>
                  <td style={{ padding: '8px 4px', textAlign: 'right' }}>{p.stock}</td>
                  <td style={{ padding: '8px 4px', textAlign: 'right' }}>{fmt(p.cost)}</td>
                  <td style={{ padding: '8px 4px', textAlign: 'right' }}>{fmt(p.retail)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      </div>

      {/* Row 4: Best sellers + Sales KPIs */}
      <div style={{ display: 'grid', gridTemplateColumns: '1.5fr 1fr', gap: 20, marginBottom: 20 }}>
        <Card title="Best sellers">
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
            <thead><tr style={{ borderBottom: '1px solid var(--border)' }}>
              <th style={{ padding: '6px 4px', textAlign: 'left', color: 'var(--text-light)', fontWeight: 500 }}>#</th>
              <th style={{ padding: '6px 4px', textAlign: 'left', color: 'var(--text-light)', fontWeight: 500 }}>Title</th>
              <th style={{ padding: '6px 4px', textAlign: 'right', color: 'var(--text-light)', fontWeight: 500 }}>Sales</th>
              <th style={{ padding: '6px 4px', textAlign: 'right', color: 'var(--text-light)', fontWeight: 500 }}>COGS</th>
              <th style={{ padding: '6px 4px', textAlign: 'right', color: 'var(--text-light)', fontWeight: 500 }}>Revenue</th>
            </tr></thead>
            <tbody>
              {(best_sellers || []).map((p, i) => (
                <tr key={i} style={{ borderBottom: '1px solid var(--border)' }}>
                  <td style={{ padding: '8px 4px', color: 'var(--text-light)' }}>{i + 1}</td>
                  <td style={{ padding: '8px 4px', fontWeight: 500 }}>{p.title}</td>
                  <td style={{ padding: '8px 4px', textAlign: 'right' }}>{fmtNum(p.sales)}</td>
                  <td style={{ padding: '8px 4px', textAlign: 'right' }}>{fmt(p.cogs)}</td>
                  <td style={{ padding: '8px 4px', textAlign: 'right' }}>{fmt(p.revenue)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>

        <Card title="Sales" style={{ position: 'relative' }}>
          <div style={{ display: 'flex', justifyContent: 'flex-end', marginTop: -36, marginBottom: 12 }}>
            <span style={{ padding: '4px 12px', borderRadius: 6, border: '1px solid var(--green)', color: 'var(--green)', fontSize: 11, fontWeight: 500 }}>{'\uD83D\uDCC5'} Trailing 12 months</span>
          </div>
          <StatRow label={'\uD83D\uDED2 Units sold'} value={fmtNum(sales.units_sold)} />
          <StatRow label={'\uD83D\uDCB0 Cost of goods sold'} value={fmt(sales.cogs)} />
          <StatRow label={'\uD83D\uDCCA Revenue'} value={fmt(sales.revenue)} />
        </Card>
      </div>
    </div>
  );
}
