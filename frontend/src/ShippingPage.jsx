import { useMemo, useState } from 'react';
import * as api from './api';

const stateStyle = {
  verified: { label: 'Verified', bg: '#dcfce7', fg: '#166534' },
  provisional: { label: 'Measure / verify', bg: '#fef3c7', fg: '#92400e' },
  not_found: { label: 'Missing registry', bg: '#fee2e2', fg: '#991b1b' },
  missing_sku: { label: 'Missing SKU', bg: '#fee2e2', fg: '#991b1b' },
  review: { label: 'Review', bg: '#e5e7eb', fg: '#374151' },
};

function Badge({ state }) {
  const s = stateStyle[state] || stateStyle.review;
  return (
    <span style={{
      display: 'inline-block', padding: '3px 8px', borderRadius: 999,
      fontSize: 11, fontWeight: 700, background: s.bg, color: s.fg,
    }}>{s.label}</span>
  );
}

function dimsText(dims) {
  if (!Array.isArray(dims) || dims.length !== 3) return '—';
  return dims.map(v => Number(v).toFixed(2).replace(/\.00$/, '')).join(' × ') + ' in';
}

function weightText(value) {
  if (value === null || value === undefined || value === '') return '—';
  const kg = Number(value);
  if (!Number.isFinite(kg)) return '—';
  return `${kg.toFixed(2)} kg / ${(kg * 2.2046226218).toFixed(2)} lb`;
}

export default function ShippingPage({ onToast, currentUser }) {
  const [orderNumber, setOrderNumber] = useState('');
  const [order, setOrder] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  const destination = order?.shipping_address || {};
  const readiness = order?.packing_readiness || {};
  const registry = order?.registry || {};

  const packageGroups = useMemo(() => {
    const grouped = {};
    for (const pkg of order?.physical_packages || []) {
      const key = `${pkg.sku}||${pkg.part}`;
      if (!grouped[key]) grouped[key] = { ...pkg, count: 0 };
      grouped[key].count += 1;
    }
    return Object.values(grouped);
  }, [order]);

  async function loadOrder(e) {
    e?.preventDefault();
    const value = orderNumber.trim();
    if (!value) return;
    setLoading(true);
    setError('');
    try {
      const result = await api.getShippingOrder(value);
      setOrder(result);
      onToast?.(`Loaded ${result.name || value} from Shopify`);
    } catch (err) {
      setOrder(null);
      setError(err?.message || 'Could not load Shopify order');
      onToast?.(err?.message || 'Could not load Shopify order', 'error');
    } finally {
      setLoading(false);
    }
  }

  return (
    <div style={{ padding: '28px 34px 60px', maxWidth: 1500, margin: '0 auto' }}>
      <div style={{ marginBottom: 24 }}>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 16 }}>
          <div>
            <h1 style={{ margin: 0, fontSize: 25, fontWeight: 800 }}>Shipping</h1>
            <p style={{ margin: '6px 0 0', color: 'var(--text-light)', fontSize: 13 }}>
              Phase 1: load a live Shopify order and resolve its SKUs against the TC package registry.
            </p>
          </div>
          <div style={{ fontSize: 12, color: 'var(--text-light)', textAlign: 'right' }}>
            Signed in as<br />
            <strong style={{ color: 'var(--green)', fontSize: 13 }}>{currentUser || 'Unknown'}</strong>
          </div>
        </div>
      </div>

      <div style={{
        background: '#fff', border: '1px solid var(--border)', borderRadius: 12,
        padding: 20, marginBottom: 18,
      }}>
        <form onSubmit={loadOrder} style={{ display: 'flex', gap: 10, alignItems: 'end', flexWrap: 'wrap' }}>
          <div style={{ flex: '1 1 320px' }}>
            <label style={{ display: 'block', fontSize: 12, fontWeight: 700, marginBottom: 6, color: 'var(--text-light)' }}>
              Shopify order number
            </label>
            <input
              value={orderNumber}
              onChange={e => setOrderNumber(e.target.value)}
              placeholder="#51234 or 51234"
              autoFocus
              style={{
                width: '100%', boxSizing: 'border-box', padding: '11px 13px',
                borderRadius: 8, border: '1px solid var(--border)', fontSize: 15,
              }}
            />
          </div>
          <button
            type="submit"
            disabled={loading || !orderNumber.trim()}
            style={{
              border: 0, borderRadius: 8, padding: '11px 18px', minWidth: 130,
              background: 'var(--green)', color: '#fff', fontWeight: 700,
              cursor: loading ? 'wait' : 'pointer', opacity: loading ? 0.7 : 1,
            }}
          >{loading ? 'Loading…' : 'Load order'}</button>
        </form>
        {error && <div style={{ marginTop: 12, color: '#b91c1c', fontSize: 13, fontWeight: 600 }}>{error}</div>}
      </div>

      {!order && (
        <div style={{
          border: '1px dashed var(--border)', borderRadius: 12, padding: 48,
          textAlign: 'center', color: 'var(--text-light)', background: '#fff',
        }}>
          <div style={{ fontSize: 42, marginBottom: 12 }}>📦</div>
          Enter a Shopify order number to begin a shipment.
        </div>
      )}

      {order && (
        <>
          <div style={{ display: 'grid', gridTemplateColumns: 'minmax(300px, 1.2fr) minmax(280px, 1fr) minmax(260px, .9fr)', gap: 14, marginBottom: 18 }}>
            <div style={{ background: '#fff', border: '1px solid var(--border)', borderRadius: 12, padding: 18 }}>
              <div style={{ fontSize: 11, color: 'var(--text-light)', fontWeight: 700, textTransform: 'uppercase', letterSpacing: '.05em' }}>Order</div>
              <div style={{ fontSize: 22, fontWeight: 800, marginTop: 3 }}>{order.name}</div>
              <div style={{ fontSize: 13, marginTop: 8, color: 'var(--text-light)' }}>
                {order.created_at ? new Date(order.created_at).toLocaleString('en-CA') : ''}
              </div>
              <div style={{ fontSize: 13, marginTop: 5 }}>{order.shipping_method || 'No Shopify shipping method recorded'}</div>
            </div>

            <div style={{ background: '#fff', border: '1px solid var(--border)', borderRadius: 12, padding: 18 }}>
              <div style={{ fontSize: 11, color: 'var(--text-light)', fontWeight: 700, textTransform: 'uppercase', letterSpacing: '.05em' }}>Ship to</div>
              <div style={{ fontSize: 14, fontWeight: 700, marginTop: 5 }}>{destination.name || 'No shipping address'}</div>
              {destination.company && <div style={{ fontSize: 13 }}>{destination.company}</div>}
              {destination.address1 && <div style={{ fontSize: 13 }}>{destination.address1}</div>}
              {destination.address2 && <div style={{ fontSize: 13 }}>{destination.address2}</div>}
              {(destination.city || destination.province_code || destination.zip) && (
                <div style={{ fontSize: 13 }}>{[destination.city, destination.province_code, destination.zip].filter(Boolean).join(', ')}</div>
              )}
              {destination.country && <div style={{ fontSize: 13 }}>{destination.country}</div>}
            </div>

            <div style={{ background: '#fff', border: '1px solid var(--border)', borderRadius: 12, padding: 18 }}>
              <div style={{ fontSize: 11, color: 'var(--text-light)', fontWeight: 700, textTransform: 'uppercase', letterSpacing: '.05em' }}>Registry readiness</div>
              <div style={{ fontSize: 26, fontWeight: 800, color: readiness.ready_for_verified_packing ? 'var(--green)' : '#b45309', marginTop: 3 }}>
                {readiness.physical_package_count || 0} packages
              </div>
              <div style={{ fontSize: 12, color: 'var(--text-light)', marginTop: 4 }}>
                {registry.records || 0} registry records · {registry.verified_records || 0} warehouse verified
              </div>
              <div style={{ marginTop: 10, fontSize: 12, fontWeight: 700 }}>
                {readiness.ready_for_verified_packing
                  ? 'Ready for verified packing'
                  : `${readiness.unresolved_count || 0} unresolved · ${(readiness.provisional_skus || []).length} provisional SKU(s)`}
              </div>
            </div>
          </div>

          <div style={{ background: '#fff', border: '1px solid var(--border)', borderRadius: 12, overflow: 'hidden', marginBottom: 18 }}>
            <div style={{ padding: '15px 18px', borderBottom: '1px solid var(--border)', fontWeight: 800 }}>Shopify line items</div>
            <div style={{ overflowX: 'auto' }}>
              <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
                <thead>
                  <tr style={{ background: '#f8faf9', textAlign: 'left' }}>
                    {['SKU', 'Product', 'Qty to pack', 'Registry', 'Package records'].map(h => (
                      <th key={h} style={{ padding: '10px 12px', borderBottom: '1px solid var(--border)', color: 'var(--text-light)', fontSize: 11, textTransform: 'uppercase' }}>{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {(order.line_items || []).map((item, idx) => (
                    <tr key={item.id || idx}>
                      <td style={{ padding: '11px 12px', borderBottom: '1px solid var(--border)', fontWeight: 700 }}>{item.sku || '—'}</td>
                      <td style={{ padding: '11px 12px', borderBottom: '1px solid var(--border)' }}>
                        {item.title}{item.variant_title && item.variant_title !== 'Default Title' ? ` — ${item.variant_title}` : ''}
                      </td>
                      <td style={{ padding: '11px 12px', borderBottom: '1px solid var(--border)' }}>{item.pack_quantity}</td>
                      <td style={{ padding: '11px 12px', borderBottom: '1px solid var(--border)' }}><Badge state={item.registry_state} /></td>
                      <td style={{ padding: '11px 12px', borderBottom: '1px solid var(--border)' }}>{(item.registry_records || []).length}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>

          <div style={{ background: '#fff', border: '1px solid var(--border)', borderRadius: 12, overflow: 'hidden', marginBottom: 18 }}>
            <div style={{ padding: '15px 18px', borderBottom: '1px solid var(--border)', fontWeight: 800 }}>Resolved physical packages</div>
            <div style={{ overflowX: 'auto' }}>
              <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
                <thead>
                  <tr style={{ background: '#f8faf9', textAlign: 'left' }}>
                    {['SKU', 'Part', 'Count', 'Dimensions', 'Weight each', 'Verification'].map(h => (
                      <th key={h} style={{ padding: '10px 12px', borderBottom: '1px solid var(--border)', color: 'var(--text-light)', fontSize: 11, textTransform: 'uppercase' }}>{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {packageGroups.map((pkg, idx) => (
                    <tr key={`${pkg.registry_id || idx}:${pkg.sku}:${pkg.part}`}>
                      <td style={{ padding: '11px 12px', borderBottom: '1px solid var(--border)', fontWeight: 700 }}>{pkg.sku}</td>
                      <td style={{ padding: '11px 12px', borderBottom: '1px solid var(--border)' }}>{pkg.part}</td>
                      <td style={{ padding: '11px 12px', borderBottom: '1px solid var(--border)' }}>{pkg.count}</td>
                      <td style={{ padding: '11px 12px', borderBottom: '1px solid var(--border)' }}>{dimsText(pkg.dimensions_in)}</td>
                      <td style={{ padding: '11px 12px', borderBottom: '1px solid var(--border)' }}>{weightText(pkg.weight_kg)}</td>
                      <td style={{ padding: '11px 12px', borderBottom: '1px solid var(--border)' }}>
                        <Badge state={pkg.verification_status === 'Shopify — Unverified' ? 'provisional' : 'verified'} />
                      </td>
                    </tr>
                  ))}
                  {!packageGroups.length && (
                    <tr><td colSpan="6" style={{ padding: 24, textAlign: 'center', color: 'var(--text-light)' }}>No physical packages resolved yet.</td></tr>
                  )}
                </tbody>
              </table>
            </div>
          </div>

          {(readiness.unresolved || []).length > 0 && (
            <div style={{ background: '#fff7ed', border: '1px solid #fdba74', borderRadius: 12, padding: 18, marginBottom: 18 }}>
              <div style={{ fontWeight: 800, color: '#9a3412', marginBottom: 8 }}>Needs package data</div>
              {(readiness.unresolved || []).map((u, idx) => (
                <div key={idx} style={{ fontSize: 13, color: '#7c2d12', marginTop: 4 }}>
                  <strong>{u.sku || u.product}</strong>{u.part ? ` / ${u.part}` : ''}: {u.reason}
                </div>
              ))}
            </div>
          )}

          <div style={{
            background: '#f0fdf4', border: '1px solid #86efac', borderRadius: 12,
            padding: 18, color: '#166534', fontSize: 13,
          }}>
            <strong>Phase 1 boundary is working.</strong> This page now turns a live Shopify order into the physical-package input expected by the TC packing engine. The next integration step is to move the V5.7 packing-plan logic, accessory-carrier learning, carton inventory, and final shipment summary behind this page.
          </div>
        </>
      )}
    </div>
  );
}
