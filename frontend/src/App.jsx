import { useState, useEffect, useMemo, useCallback, useRef } from 'react';
import * as api from './api';
import StockOrdersPage from './StockOrdersPage';
import ToolsPage from './ToolsPage';
import BackordersPage from './BackordersPage';
import OverviewPage from './OverviewPage';
import AnalyticsPage from './AnalyticsPage';
import MarketIntelPage from './MarketIntelPage';
import VendorManagementPage from './VendorManagementPage';
import CogsPage from './CogsPage';
import SalePerformancePage from './SalePerformancePage';
import SalesManagerPage from './SalesManagerPage';
import DonationsPage from './DonationsPage';
import WaitersPage from './WaitersPage';
import PoComparisonPage from './PoComparisonPage';
import SettingsPage from './SettingsPage';

// ─── FORMATTING ─────────────────────────────────────────────────

const fmt = (n) => new Intl.NumberFormat('en-CA', {
  style: 'currency', currency: 'CAD', minimumFractionDigits: 0, maximumFractionDigits: 0,
}).format(n);
const fmtFull = (n) => new Intl.NumberFormat('en-CA', {
  style: 'currency', currency: 'CAD', minimumFractionDigits: 2,
}).format(n);
const fmtNum = (n) => new Intl.NumberFormat('en-CA').format(n);

// ─── LOGIN SCREEN ───────────────────────────────────────────────

function LoginScreen({ onLogin }) {
  const [token, setToken] = useState('');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);

  const handleSubmit = async (e) => {
    e.preventDefault();
    setLoading(true);
    setError('');
    try {
      const valid = await api.verifyToken(token);
      if (valid) {
        onLogin();
      } else {
        setError('Invalid token');
      }
    } catch {
      setError('Connection failed. Is the backend running?');
    }
    setLoading(false);
  };

  return (
    <div style={{
      minHeight: '100vh', display: 'flex', alignItems: 'center', justifyContent: 'center',
      backgroundColor: '#1a1f2e',
    }}>
      <div style={{
        backgroundColor: '#fff', borderRadius: 12, padding: 40, width: 400,
        boxShadow: '0 8px 32px rgba(0,0,0,0.3)',
      }}>
        <div style={{
          width: 56, height: 56, borderRadius: 12, backgroundColor: '#1a7e5a',
          display: 'flex', alignItems: 'center', justifyContent: 'center', margin: '0 auto 20px',
        }}>
          <span style={{ fontSize: 22, fontWeight: 800, color: '#fff' }}>TC</span>
        </div>
        <h1 style={{ textAlign: 'center', fontSize: 20, fontWeight: 700, marginBottom: 4 }}>
          Inventory Planner
        </h1>
        <p style={{ textAlign: 'center', color: '#7f8c8d', fontSize: 13, marginBottom: 28 }}>
          Telescopes Canada
        </p>
        <form onSubmit={handleSubmit}>
          <label style={{ display: 'block', fontSize: 12, fontWeight: 600, color: '#7f8c8d', marginBottom: 6 }}>
            API Token
          </label>
          <input
            type="password"
            value={token}
            onChange={e => setToken(e.target.value)}
            placeholder="Enter your access token"
            style={{
              width: '100%', padding: '10px 14px', borderRadius: 8,
              border: '1px solid #e2e5ea', fontSize: 14, marginBottom: 16,
            }}
            autoFocus
          />
          {error && (
            <div style={{ color: '#e74c3c', fontSize: 12, marginBottom: 12, fontWeight: 500 }}>
              {error}
            </div>
          )}
          <button
            type="submit"
            disabled={loading || !token}
            style={{
              width: '100%', padding: '10px 20px', borderRadius: 8,
              backgroundColor: '#1a7e5a', color: '#fff', fontWeight: 600,
              fontSize: 14, border: 'none', cursor: 'pointer',
            }}
          >
            {loading ? 'Connecting...' : 'Sign in'}
          </button>
        </form>
      </div>
    </div>
  );
}

// ─── SIDEBAR ────────────────────────────────────────────────────

function Sidebar({ currentPage, onNavigate, currentUser }) {
  const navItems = [
    { id: 'overview', icon: '📊', label: 'Overview' },
    { id: 'replenishment', icon: '🔄', label: 'Replenishment' },
    { id: 'stockorders', icon: '📋', label: 'Stock Orders' },
    { id: 'backorders', icon: '⚠️', label: 'Backorders' },
    'divider',
    { id: 'intelligence', icon: '🎯', label: 'Intelligence' },
    { id: 'brands', icon: '🏷️', label: 'Brand Analysis' },
    { id: 'purchasing', icon: '💰', label: 'Purchasing' },
    { id: 'actions', icon: '⚡', label: 'Actions' },
    { id: 'market', icon: '🌐', label: 'Market Intel' },
    { id: 'vendors', icon: '🏭', label: 'Vendors' },
    { id: 'cogs', icon: '📒', label: 'COGS Tracker' },
    { id: 'sales_manager', icon: '🏷️', label: 'Sales Manager' },
    { id: 'sale_performance', icon: '📊', label: 'Sale Performance' },
    { id: 'waiters', icon: '⏳', label: 'Waiters' },
    { id: 'donations', icon: '🎁', label: 'Donations' },
    { id: 'po_compare', icon: '⚖️', label: 'PO Comparison' },
    'divider',
    { id: 'tools', icon: '🔧', label: 'Tools' },
    { id: 'settings', icon: '⚙️', label: 'Settings' },
  ];

  return (
    <div style={{
      width: 52, minHeight: '100vh', backgroundColor: 'var(--sidebar-bg)',
      display: 'flex', flexDirection: 'column', alignItems: 'center',
      padding: '12px 0', gap: 4, position: 'fixed', left: 0, top: 0, zIndex: 100,
    }}>
      <div
        onClick={() => onNavigate('replenishment')}
        style={{
          width: 34, height: 34, borderRadius: 8, backgroundColor: 'var(--green)',
          display: 'flex', alignItems: 'center', justifyContent: 'center',
          marginBottom: 16, cursor: 'pointer',
        }}
      >
        <span style={{ fontSize: 15, fontWeight: 800, color: '#fff' }}>TC</span>
      </div>
      {navItems.map((item, idx) => {
        if (item === 'divider') {
          return <div key={`div-${idx}`} style={{ width: 28, height: 1, backgroundColor: 'var(--border)', margin: '4px 0' }} />;
        }
        return (
        <div
          key={item.id}
          onClick={() => onNavigate(item.id)}
          title={item.label}
          style={{
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            width: 42, height: 42, borderRadius: 8, cursor: 'pointer',
            backgroundColor: currentPage === item.id ? '#fff' : '#000',
            borderLeft: currentPage === item.id ? '3px solid var(--green)' : '3px solid transparent',
          }}
        >
          <span style={{ fontSize: 18 }}>
            {item.icon}
          </span>
        </div>
        );
      })}
      <div style={{ flex: 1 }} />
      {currentUser && (
        <div
          title={currentUser === 'Unknown'
            ? 'Signed in with the shared token — stock receipts will be logged as "Unknown". Use your personal token to record receipts under your name.'
            : `Signed in as ${currentUser} — stock receipts are logged under your name`}
          style={{
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            width: 34, height: 34, borderRadius: 17, marginBottom: 6,
            backgroundColor: currentUser === 'Unknown' ? '#9ca3af' : 'var(--green)',
            color: '#fff', fontSize: 12, fontWeight: 700, cursor: 'help',
          }}
        >
          {currentUser === 'Unknown' ? '?' : currentUser.slice(0, 2).toUpperCase()}
        </div>
      )}
      <div
        onClick={() => { api.setToken(''); window.location.reload(); }}
        title="Sign out"
        style={{
          display: 'flex', alignItems: 'center', justifyContent: 'center',
          width: 42, height: 42, borderRadius: 8, cursor: 'pointer',
        }}
      >
        <span style={{ fontSize: 16, opacity: 0.6 }}>🚪</span>
      </div>
    </div>
  );
}

// ─── TOAST NOTIFICATIONS ────────────────────────────────────────

function Toast({ message, type = 'success', onClose }) {
  useEffect(() => {
    const timer = setTimeout(onClose, 4000);
    return () => clearTimeout(timer);
  }, [onClose]);

  return <div className={`toast ${type}`}>{message}</div>;
}

// ─── REPLENISHMENT PAGE ─────────────────────────────────────────

function ReplenishmentPage({ onToast, onNavigate }) {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [refreshStep, setRefreshStep] = useState('');
  const [vendors, setVendors] = useState([]);
  const [vendorFilter, setVendorFilter] = useState('');
  const [searchTerm, setSearchTerm] = useState('');
  const [sortField, setSortField] = useState('replenish_qty');
  const [sortDir, setSortDir] = useState('desc');
  const [page, setPage] = useState(1);
  const [perPage] = useState(50);
  const [selected, setSelected] = useState(new Set());
  const [needsReplenish, setNeedsReplenish] = useState(true);
  const [replenishableOnly, setReplenishableOnly] = useState(true);
  const [belowMinOnly, setBelowMinOnly] = useState(false);
  const [editingMinSku, setEditingMinSku] = useState(null);
  const [editingMinValue, setEditingMinValue] = useState('');
  const [savingMin, setSavingMin] = useState(false);
  const [openPos, setOpenPos] = useState([]);
  const [addToPoId, setAddToPoId] = useState('');
  const [addingToPo, setAddingToPo] = useState(false);
  const [creatingOrder, setCreatingOrder] = useState(false);
  // Per-vendor PO reminder popup. `poReminder` holds the pending prompt while
  // we wait for the operator to choose; resolve() continues or aborts the
  // PO creation that triggered it.
  const [poReminder, setPoReminder] = useState(null);

  // Show the vendor's reminder (if any) and wait for a decision.
  // Returns true to proceed with the PO, false to cancel.
  const confirmVendorReminder = (vendorName) => {
    const v = vendors.find(x => x.name === vendorName);
    const text = (v?.po_reminder || '').trim();
    if (!text || v?.po_reminder_active === false) return Promise.resolve(true);
    return new Promise(resolve => {
      setPoReminder({ vendor: vendorName, text, resolve });
    });
  };

  const closePoReminder = async (action) => {
    const r = poReminder;
    setPoReminder(null);
    if (!r) return;
    if (action === 'dismiss') {
      try {
        await api.dismissPoReminder(r.vendor, false);
        // Reflect immediately so it doesn't reappear before the next refresh.
        setVendors(vs => vs.map(v => v.name === r.vendor ? { ...v, po_reminder_active: false } : v));
        onToast(`Reminder for ${r.vendor} won't show again`);
      } catch (e) {
        onToast('Could not save that preference: ' + e.message, 'error');
      }
    }
    r.resolve(action !== 'cancel');
  };
  const searchTimeout = useRef(null);

  // Fetch vendors on mount
  useEffect(() => {
    api.getVendors()
      .then(res => setVendors(res.vendors || []))
      .catch(() => {});
  }, []);

  // Load open purchase orders so the user can add selected items to one
  // without creating a new PO. Sorted descending by reference number so
  // the most recent appears first.
  const loadOpenPos = useCallback(async () => {
    try {
      const res = await api.listStockOrders({ status: 'open' });
      const sorted = [...(res.orders || [])].sort(
        (a, b) => (b.reference_number || 0) - (a.reference_number || 0)
      );
      setOpenPos(sorted);
    } catch {
      // Non-fatal — the dropdown just stays empty.
    }
  }, []);
  useEffect(() => { loadOpenPos(); }, [loadOpenPos]);

  // Fetch replenishment data
  const fetchData = useCallback(async () => {
    setLoading(true);
    try {
      const result = await api.getReplenishment({
        vendor: vendorFilter || null,
        search: searchTerm || null,
        sort: sortField,
        dir: sortDir,
        page,
        perPage,
        needsReplenish,
        replenishableOnly,
        belowMinOnly,
      });
      setData(result);
    } catch (err) {
      onToast(`Failed to load data: ${err.message}`, 'error');
    }
    setLoading(false);
  }, [vendorFilter, searchTerm, sortField, sortDir, page, perPage, needsReplenish, replenishableOnly, belowMinOnly, onToast]);

  useEffect(() => { fetchData(); }, [fetchData]);

  // Debounced search
  const handleSearch = (val) => {
    setSearchTerm(val);
    setPage(1);
    setSelected(new Set());
  };

  const handleSearchInput = (val) => {
    clearTimeout(searchTimeout.current);
    searchTimeout.current = setTimeout(() => handleSearch(val), 300);
  };

  // Sorting
  const handleSort = (field) => {
    if (sortField === field) {
      setSortDir(d => d === 'asc' ? 'desc' : 'asc');
    } else {
      setSortField(field);
      setSortDir('desc');
    }
    setPage(1);
  };

  // Selection
  const toggleSelect = (sku) => {
    setSelected(prev => {
      const next = new Set(prev);
      next.has(sku) ? next.delete(sku) : next.add(sku);
      return next;
    });
  };

  // Header checkbox toggles selection of just the current page's items.
  // Selection persists across pages — pagination doesn't clear selected SKUs,
  // so the user can flip through pages and accumulate selections, or use the
  // "Select all N across pages" banner to grab everything matching the filter.
  const toggleAll = () => {
    if (!data?.items) return;
    const currentPageSkus = data.items.map(i => i.sku);
    const currentPageAllSelected = currentPageSkus.every(s => selected.has(s));
    setSelected(prev => {
      const next = new Set(prev);
      if (currentPageAllSelected) {
        currentPageSkus.forEach(s => next.delete(s));
      } else {
        currentPageSkus.forEach(s => next.add(s));
      }
      return next;
    });
  };

  // Select every SKU matching current filters (across all pages) by hitting
  // the all-items endpoint. Used by the banner that appears when the user
  // has just selected all rows on the current page and there are more pages.
  const [selectingAll, setSelectingAll] = useState(false);
  const selectAllAcrossPages = async () => {
    setSelectingAll(true);
    try {
      const result = await api.getReplenishmentAllItems({
        vendor: vendorFilter || null,
        search: searchTerm || null,
        needsReplenish,
        replenishableOnly,
      });
      setSelected(new Set(result.items.map(i => i.sku)));
      onToast(`Selected all ${result.items.length} items`, 'success');
    } catch (err) {
      onToast(`Failed to select all: ${err.message}`, 'error');
    }
    setSelectingAll(false);
  };

  const clearSelection = () => setSelected(new Set());

  // Refresh from Shopify
  const handleRefresh = async () => {
    setRefreshing(true);
    setRefreshStep('Starting...');

    // Poll status every 2 seconds while refreshing
    const pollInterval = setInterval(async () => {
      try {
        const status = await api.getRefreshStatus();
        if (status.detail) setRefreshStep(status.detail);
      } catch (e) { /* ignore polling errors */ }
    }, 2000);

    try {
      const result = await api.refreshData();
      setRefreshStep('');
      onToast(
        `Refreshed: ${fmtNum(result.products_fetched)} products, ${fmtNum(result.needs_replenishment)} need replenishment`,
        'success'
      );
      fetchData();
    } catch (err) {
      setRefreshStep('');
      onToast(`Refresh failed: ${err.message}`, 'error');
    }

    clearInterval(pollInterval);
    setRefreshing(false);
  };

  // Create stock order
  const handleCreateOrder = async () => {
    if (selected.size === 0) return;
    // Re-entry guard: a 60+ item PO takes several seconds to create; a second
    // click during that window used to create a duplicate PO whose quantities
    // had already collapsed to 0 (the first PO's recalc zeroes replenish_qty).
    if (creatingOrder) return;
    setCreatingOrder(true);
    try {
      await doCreateOrder();
    } finally {
      setCreatingOrder(false);
    }
  };

  const doCreateOrder = async () => {

    // The user may have selected items across multiple pages. The current
    // `data.items` only contains the visible page (50). For a complete PO,
    // fetch full item data for ALL selected SKUs.
    let selectedItems;
    try {
      const visibleSkus = new Set((data?.items || []).map(i => i.sku));
      const allVisible = [...selected].every(sku => visibleSkus.has(sku));
      if (allVisible) {
        // Fast path: every selected SKU is on the current page
        selectedItems = (data?.items || []).filter(i => selected.has(i.sku));
      } else {
        // Cross-page selection: fetch full data for all selected SKUs
        const result = await api.getReplenishmentAllItems({ skus: [...selected] });
        selectedItems = result.items;
        // Guard: never silently create a smaller PO than what was selected.
        if (selectedItems.length !== selected.size) {
          const missing = result.missing_skus || [];
          const missingNote = missing.length
            ? `\nMissing: ${missing.slice(0, 10).join(', ')}${missing.length > 10 ? ` … (+${missing.length - 10} more)` : ''}`
            : '';
          if (!confirm(`Only ${selectedItems.length} of ${selected.size} selected SKUs could be loaded.${missingNote}\n\nCreate the PO with ${selectedItems.length} items anyway?`)) return;
        }
      }
    } catch (err) {
      onToast(`Failed to assemble order: ${err.message}`, 'error');
      return;
    }

    if (selectedItems.length === 0) {
      onToast('No matching items found for selected SKUs', 'error');
      return;
    }

    // Determine vendor - if all same vendor, use that; otherwise use the filter
    const vendorSet = new Set(selectedItems.map(i => i.vendor));
    const orderVendor = vendorSet.size === 1 ? [...vendorSet][0] : (vendorFilter || 'Mixed');

    // Vendor-specific reminder ("don't forget the T-rings"). Shown before the
    // PO is created so there's still a chance to add the forgotten items.
    if (!await confirmVendorReminder(orderVendor)) return;

    try {
      const leadTime = vendors.find(v => v.name === orderVendor)?.lead_time_days || 14;
      const expectedDate = new Date(Date.now() + leadTime * 86400000).toISOString().split('T')[0];

      await api.createStockOrder({
        vendor: orderVendor,
        expectedDate,
        items: selectedItems.map(i => ({
          product_title: i.product_title,
          variant_title: i.variant_title || '',
          sku: i.sku,
          barcode: i.barcode || '',
          ordered_qty: i.replenish_qty,
          unit_cost: i.cost,
          unit_price: i.price,
        })),
      });

      onToast(`Stock order created for ${orderVendor} with ${selectedItems.length} items`, 'success');
      setSelected(new Set());
      onNavigate('stockorders');
    } catch (err) {
      onToast(`Failed to create order: ${err.message}`, 'error');
    }
  };

  // Add the currently-selected SKUs to an existing open purchase order.
  // Useful when a vendor already has an open PO and the recommendations
  // should land on it instead of forcing a separate shipment.
  const handleAddToExistingOrder = async () => {
    if (selected.size === 0 || !addToPoId) return;
    const targetPo = openPos.find(p => String(p.id) === String(addToPoId));
    if (!targetPo) {
      onToast('Selected PO not found — refresh and try again', 'error');
      return;
    }

    let selectedItems;
    try {
      const visibleSkus = new Set((data?.items || []).map(i => i.sku));
      const allVisible = [...selected].every(sku => visibleSkus.has(sku));
      if (allVisible) {
        selectedItems = (data?.items || []).filter(i => selected.has(i.sku));
      } else {
        const result = await api.getReplenishmentAllItems({ skus: [...selected] });
        selectedItems = result.items;
        // Guard: never silently add fewer items than were selected.
        if (selectedItems.length !== selected.size) {
          const missing = result.missing_skus || [];
          const missingNote = missing.length
            ? `\nMissing: ${missing.slice(0, 10).join(', ')}${missing.length > 10 ? ` … (+${missing.length - 10} more)` : ''}`
            : '';
          if (!confirm(`Only ${selectedItems.length} of ${selected.size} selected SKUs could be loaded.${missingNote}\n\nAdd ${selectedItems.length} items to the PO anyway?`)) return;
        }
      }
    } catch (err) {
      onToast(`Failed to assemble items: ${err.message}`, 'error');
      return;
    }
    if (selectedItems.length === 0) {
      onToast('No matching items found for selected SKUs', 'error');
      return;
    }

    // Cross-vendor warning — adding a Brand X SKU to a Brand Y PO is
    // almost always a mistake.
    const vendorMismatches = selectedItems.filter(i => i.vendor && targetPo.vendor && i.vendor !== targetPo.vendor);
    if (vendorMismatches.length > 0) {
      const ok = window.confirm(
        `${vendorMismatches.length} of ${selectedItems.length} selected items have a different vendor than this PO ` +
        `(${targetPo.vendor}). Add anyway?`
      );
      if (!ok) return;
    }

    // Same vendor reminder applies when topping up an existing PO.
    if (!await confirmVendorReminder(targetPo.vendor)) return;

    setAddingToPo(true);
    let added = 0;
    let failed = 0;
    for (const i of selectedItems) {
      try {
        await api.addStockOrderItem(targetPo.id, {
          sku: i.sku,
          ordered_qty: i.replenish_qty,
          unit_cost: i.cost,
          unit_price: i.price,
          product_title: i.product_title,
          variant_title: i.variant_title || '',
          vendor: i.vendor,
          barcode: i.barcode || '',
        });
        added++;
      } catch (err) {
        failed++;
      }
    }
    setAddingToPo(false);

    if (added > 0) {
      onToast(
        `Added ${added} item${added === 1 ? '' : 's'} to PO #${targetPo.reference_number}` +
        (failed > 0 ? ` (${failed} failed)` : ''),
        failed > 0 ? 'error' : 'success'
      );
      setSelected(new Set());
      setAddToPoId('');
      loadOpenPos();
      fetchData();
    } else {
      onToast(`Failed to add items to PO #${targetPo.reference_number}`, 'error');
    }
  };

  // Mark selected items as non-replenishable
  const handleMarkNonReplenishable = async () => {
    if (selected.size === 0) return;
    try {
      await api.toggleReplenishable([...selected], false);
      onToast(`Marked ${selected.size} items as non-replenishable`, 'success');
      setSelected(new Set());
      fetchData();
    } catch (err) {
      onToast(`Failed: ${err.message}`, 'error');
    }
  };

  const items = data?.items || [];
  const totalPages = data?.total_pages || 1;
  const sortArrow = (field) => sortField === field ? (sortDir === 'asc' ? ' ↑' : ' ↓') : '';

  // Column definitions
  const columns = [
    { key: 'product_title', label: 'Name', align: 'left', width: '24%' },
    { key: 'replenish_qty', label: 'Replenish', align: 'right' },
    { key: 'current_stock', label: 'Stock', align: 'right' },
    { key: 'min_stock_level', label: 'Min', align: 'right', sortable: false },
    { key: 'price', label: 'Sale price', align: 'right' },
    { key: 'cost', label: 'Cost price', align: 'right' },
    { key: 'total_cost', label: 'Total cost', align: 'right' },
    { key: 'margin_pct', label: 'Margin', align: 'right' },
    { key: 'on_order', label: 'On order', align: 'right' },
    { key: 'forecast_profit', label: 'Forecast profit (mo.)', align: 'right' },
    { key: 'total_forecast_profit', label: 'Total forecast profit', align: 'right' },
    { key: 'days_of_stock', label: 'Days of stock', align: 'right' },
    { key: 'total_sold_365d', label: '365d sales', align: 'right' },
    { key: 'avg_monthly_velocity', label: 'AVG/mo', align: 'right' },
    { key: 'seasonal_monthly_velocity', label: 'Velocity/mo', align: 'right' },
  ];

  const startEditMin = (sku, currentVal) => {
    setEditingMinSku(sku);
    setEditingMinValue(String(currentVal || 0));
  };
  const cancelEditMin = () => {
    setEditingMinSku(null);
    setEditingMinValue('');
  };
  const saveEditMin = async (sku) => {
    const val = parseInt(editingMinValue, 10);
    if (Number.isNaN(val) || val < 0) {
      onToast('Min must be a non-negative integer', 'error'); return;
    }
    setSavingMin(true);
    try {
      await api.setMinStock(sku, val);
      onToast(val > 0 ? `Min for ${sku} set to ${val}` : `Min for ${sku} cleared`, 'success');
      setEditingMinSku(null);
      setEditingMinValue('');
      fetchData();
    } catch (err) {
      onToast(`Save failed: ${err.message}`, 'error');
    }
    setSavingMin(false);
  };

  const handleBulkSetMin = async () => {
    if (selected.size === 0) return;
    const input = prompt(
      `Set minimum stock level for ${selected.size} selected SKU(s)?\n\n` +
      `Enter a number (0 = clear minimum):`,
      '1'
    );
    if (input == null) return;
    const val = parseInt(input.trim(), 10);
    if (Number.isNaN(val) || val < 0) {
      onToast('Min must be a non-negative integer', 'error'); return;
    }
    try {
      const items = [...selected].map(sku => ({ sku, min_stock: val }));
      const res = await api.bulkSetMinStock(items);
      onToast(
        `Min stock: ${res.updated || 0} set, ${res.cleared || 0} cleared`,
        'success'
      );
      setSelected(new Set());
      fetchData();
    } catch (err) {
      onToast(`Bulk set failed: ${err.message}`, 'error');
    }
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100vh' }}>
      {/* Header */}
      <div style={{
        padding: '14px 24px', borderBottom: '1px solid var(--border)',
        backgroundColor: 'var(--white)', display: 'flex', alignItems: 'center', gap: 12,
      }}>
        <h1 style={{ fontSize: 20, fontWeight: 700, margin: 0 }}>Replenishment</h1>
        <span style={{
          fontSize: 12, color: 'var(--text-light)', backgroundColor: '#f0f1f3',
          padding: '4px 10px', borderRadius: 4,
        }}>
          Telescopes Canada Warehouse
        </span>

        {/* Vendor filter */}
        <select
          value={vendorFilter}
          onChange={e => { setVendorFilter(e.target.value); setPage(1); setSelected(new Set()); }}
          style={{
            padding: '6px 12px', borderRadius: 6, border: '1px solid var(--border)',
            fontSize: 13, color: vendorFilter ? 'var(--green)' : 'var(--text)',
            fontWeight: vendorFilter ? 600 : 400, marginLeft: 4,
          }}
        >
          <option value="">All Vendors</option>
          {vendors.map(v => <option key={v.name} value={v.name}>{v.name}</option>)}
        </select>

        {/* Search */}
        <div style={{
          flex: 1, display: 'flex', alignItems: 'center', gap: 8,
          padding: '6px 14px', borderRadius: 6, border: '1px solid var(--border)',
          backgroundColor: '#fafafa', maxWidth: 360,
        }}>
          <span style={{ color: 'var(--text-muted)', fontSize: 13 }}>🔍</span>
          <input
            type="text"
            placeholder="Search products or SKUs..."
            defaultValue={searchTerm}
            onChange={e => handleSearchInput(e.target.value)}
            style={{ border: 'none', outline: 'none', flex: 1, fontSize: 13, backgroundColor: 'transparent' }}
          />
        </div>

        {/* Refresh button */}
        <button
          onClick={handleRefresh}
          disabled={refreshing}
          title="Refresh data from Shopify"
          style={{
            padding: '6px 14px', borderRadius: 6, border: '1px solid var(--border)',
            backgroundColor: 'transparent', fontSize: 13, display: 'flex', alignItems: 'center', gap: 6,
          }}
        >
          <span style={{ display: 'inline-block', animation: refreshing ? 'spin 1s linear infinite' : 'none' }}>🔄</span>
          {refreshing ? (refreshStep || 'Refreshing...') : 'Refresh'}
        </button>

        {/* Date range */}
        <span style={{
          padding: '6px 14px', borderRadius: 6, border: '1px solid var(--green)',
          color: 'var(--green)', fontSize: 12, fontWeight: 500, whiteSpace: 'nowrap',
        }}>
          Trailing 12 months
        </span>
      </div>

      {/* Filter bar */}
      <div style={{
        padding: '8px 24px', borderBottom: '1px solid var(--border)',
        backgroundColor: '#fafbfc', display: 'flex', alignItems: 'center', gap: 12,
        fontSize: 13,
      }}>
        <span style={{ color: 'var(--text-light)', fontWeight: 500 }}>Filters:</span>
        <button
          onClick={() => { setNeedsReplenish(v => !v); setPage(1); setSelected(new Set()); }}
          style={{
            padding: '5px 12px', borderRadius: 6, fontSize: 12, fontWeight: 600,
            border: needsReplenish ? '1px solid var(--green)' : '1px solid var(--border)',
            backgroundColor: needsReplenish ? 'var(--green-bg)' : 'transparent',
            color: needsReplenish ? 'var(--green)' : 'var(--text-light)',
          }}
        >
          {needsReplenish ? '✓' : '○'} Needs replenishment
        </button>
        <button
          onClick={() => { setReplenishableOnly(v => !v); setPage(1); setSelected(new Set()); }}
          style={{
            padding: '5px 12px', borderRadius: 6, fontSize: 12, fontWeight: 600,
            border: replenishableOnly ? '1px solid var(--green)' : '1px solid var(--border)',
            backgroundColor: replenishableOnly ? 'var(--green-bg)' : 'transparent',
            color: replenishableOnly ? 'var(--green)' : 'var(--text-light)',
          }}
        >
          {replenishableOnly ? '✓' : '○'} Replenishable only
        </button>
        <button
          onClick={() => { setBelowMinOnly(v => !v); setPage(1); setSelected(new Set()); }}
          title="Show only SKUs whose stock + on-order is below their min stock floor."
          style={{
            padding: '5px 12px', borderRadius: 6, fontSize: 12, fontWeight: 600,
            border: belowMinOnly ? '1px solid #ef4444' : '1px solid var(--border)',
            backgroundColor: belowMinOnly ? '#fee2e2' : 'transparent',
            color: belowMinOnly ? '#dc2626' : 'var(--text-light)',
          }}
        >
          {belowMinOnly ? '✓' : '○'} Below min only
        </button>
        {selected.size > 0 && (
          <div style={{ marginLeft: 'auto', display: 'flex', gap: 8 }}>
            <button
              onClick={handleBulkSetMin}
              style={{
                padding: '5px 12px', borderRadius: 6, fontSize: 12, fontWeight: 600,
                border: '1px solid #6366f1', backgroundColor: '#eef2ff',
                color: '#4338ca',
              }}
            >
              Set min stock for {selected.size}
            </button>
            <button
              onClick={handleMarkNonReplenishable}
              style={{
                padding: '5px 12px', borderRadius: 6, fontSize: 12, fontWeight: 600,
                border: '1px solid var(--red)', backgroundColor: '#fde8e5',
                color: 'var(--red)',
              }}
            >
              Mark {selected.size} as non-replenishable
            </button>
          </div>
        )}
      </div>

      {/* Table */}
      <div style={{ flex: 1, overflow: 'auto', backgroundColor: 'var(--bg)' }}>
        {loading ? (
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: 300, gap: 12 }}>
            <div className="spinner" />
            <span style={{ color: 'var(--text-light)', fontSize: 14 }}>Loading replenishment data...</span>
          </div>
        ) : items.length === 0 ? (
          <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', height: 300, gap: 12 }}>
            <span style={{ fontSize: 40 }}>📦</span>
            <span style={{ color: 'var(--text-light)', fontSize: 15 }}>No data available</span>
            <span style={{ color: 'var(--text-muted)', fontSize: 13 }}>
              Click "Refresh" to pull data from Shopify
            </span>
          </div>
        ) : (
          <>
          {/* Selection banner: appears when items selected (esp. across pages) */}
          {selected.size > 0 && (
            <div style={{
              position: 'sticky', top: 0, zIndex: 11,
              padding: '8px 16px', backgroundColor: '#eff6ff',
              borderBottom: '1px solid #bfdbfe', display: 'flex',
              alignItems: 'center', gap: 12, fontSize: 13,
            }}>
              <span style={{ color: '#1e40af', fontWeight: 600 }}>
                {selected.size} {selected.size === 1 ? 'item' : 'items'} selected
              </span>
              {/* Show the "select all across pages" link when:
                  - the entire current page is selected
                  - there are more results than the current selection */}
              {items.length > 0 && items.every(i => selected.has(i.sku)) && (data?.total || 0) > selected.size && (
                <button
                  onClick={selectAllAcrossPages}
                  disabled={selectingAll}
                  style={{
                    background: 'none', border: 'none', color: '#2563eb',
                    fontWeight: 600, fontSize: 13, cursor: 'pointer', padding: 0,
                    textDecoration: 'underline',
                  }}>
                  {selectingAll ? 'Selecting…' : `Select all ${data?.total || 0} across all pages`}
                </button>
              )}
              <button
                onClick={clearSelection}
                style={{
                  background: 'none', border: 'none', color: 'var(--text-light)',
                  fontSize: 13, cursor: 'pointer', padding: 0, marginLeft: 'auto',
                }}>
                Clear selection
              </button>
            </div>
          )}
          <table style={{ width: '100%', backgroundColor: 'var(--white)', fontSize: 13 }}>
            <thead style={{ position: 'sticky', top: 0, backgroundColor: 'var(--white)', zIndex: 10 }}>
              <tr style={{ borderBottom: '2px solid var(--border)' }}>
                <th style={{ width: 36, textAlign: 'center' }}>
                  <input
                    type="checkbox"
                    checked={items.length > 0 && items.every(i => selected.has(i.sku))}
                    ref={el => { if (el) el.indeterminate = items.some(i => selected.has(i.sku)) && !items.every(i => selected.has(i.sku)); }}
                    onChange={toggleAll}
                  />
                </th>
                {columns.map(col => (
                  <th
                    key={col.key}
                    onClick={col.sortable === false ? undefined : () => handleSort(col.key)}
                    style={{
                      textAlign: col.align,
                      width: col.width,
                      backgroundColor: sortField === col.key ? '#f8f9fa' : 'transparent',
                      cursor: col.sortable === false ? 'default' : 'pointer',
                    }}
                  >
                    {col.label}{col.sortable !== false && sortArrow(col.key)}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {items.map(item => (
                <tr
                  key={item.sku}
                  style={{
                    backgroundColor: selected.has(item.sku) ? 'var(--green-bg)' : 'transparent',
                    cursor: 'pointer',
                  }}
                  onClick={() => toggleSelect(item.sku)}
                >
                  <td style={{ textAlign: 'center' }} onClick={e => e.stopPropagation()}>
                    <input type="checkbox" checked={selected.has(item.sku)} onChange={() => toggleSelect(item.sku)} />
                  </td>
                  <td>
                    <div style={{ fontWeight: 500 }}>
                      {item.replenish_qty > 0 && <span style={{ color: 'var(--orange)', marginRight: 4 }}>→</span>}
                      {item.product_title}
                    </div>
                    <div style={{ fontSize: 11, color: 'var(--text-muted)', marginTop: 2, display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap' }}>
                      <span>SKU {item.sku}{item.vendor ? ` · ${item.vendor}` : ''}</span>
                      {item.waiter_count > 0 && (
                        <span
                          title={`${item.waiter_count} customer${item.waiter_count === 1 ? '' : 's'} waiting for restock`}
                          style={{
                            display: 'inline-flex',
                            alignItems: 'center',
                            gap: 3,
                            padding: '1px 6px',
                            borderRadius: 10,
                            background: 'rgba(168, 85, 247, 0.15)',
                            color: '#a855f7',
                            fontWeight: 600,
                            fontSize: 10,
                            lineHeight: 1.4,
                          }}
                        >
                          🔔 {item.waiter_count} waiting
                        </span>
                      )}
                    </div>
                  </td>
                  <td style={{ textAlign: 'right', fontWeight: 700, color: item.replenish_qty > 0 ? 'var(--green)' : 'var(--text-muted)' }}
                      title="Primary recommendation (flat at max(avg, recent) velocity). The number in brackets is the seasonal-adjusted alternative for reference.">
                    {item.replenish_qty}
                    {item.seasonal_replenish_qty != null && item.seasonal_replenish_qty !== item.replenish_qty && (
                      <span style={{ marginLeft: 4, fontSize: 10, color: 'var(--text-muted)', fontWeight: 400 }}>
                        ({item.seasonal_replenish_qty})
                      </span>
                    )}
                  </td>
                  <td style={{ textAlign: 'right', color: item.current_stock < 0 ? 'var(--red)' : 'var(--text)' }}>
                    {item.current_stock}
                    {item.below_min && (
                      <div style={{ fontSize: 10, color: '#dc2626', fontWeight: 600 }}>
                        ↓ below min
                      </div>
                    )}
                  </td>
                  <td style={{ textAlign: 'right' }}>
                    {editingMinSku === item.sku ? (
                      <span style={{ display: 'inline-flex', gap: 2 }}>
                        <input
                          autoFocus type="number" min="0"
                          value={editingMinValue}
                          onChange={e => setEditingMinValue(e.target.value)}
                          onKeyDown={e => {
                            if (e.key === 'Enter') saveEditMin(item.sku);
                            if (e.key === 'Escape') cancelEditMin();
                          }}
                          style={{
                            width: 50, padding: '2px 4px', borderRadius: 4,
                            border: '1px solid var(--green)', fontSize: 12,
                            textAlign: 'right',
                          }}
                        />
                        <button onClick={() => saveEditMin(item.sku)} disabled={savingMin}
                          style={{ border: 'none', background: 'none', cursor: 'pointer', fontSize: 11, color: '#16a34a' }}>✓</button>
                        <button onClick={cancelEditMin}
                          style={{ border: 'none', background: 'none', cursor: 'pointer', fontSize: 11, color: 'var(--text-muted)' }}>x</button>
                      </span>
                    ) : (
                      <span
                        onClick={(e) => { e.stopPropagation(); startEditMin(item.sku, item.min_stock_level); }}
                        style={{
                          cursor: 'pointer',
                          padding: '2px 6px', borderRadius: 4,
                          backgroundColor: item.min_stock_level > 0 ? '#eef2ff' : 'transparent',
                          color: item.min_stock_level > 0 ? '#4338ca' : 'var(--text-muted)',
                          fontWeight: item.min_stock_level > 0 ? 600 : 400,
                        }}
                        title="Click to edit minimum stock level"
                      >
                        {item.min_stock_level > 0 ? item.min_stock_level : '—'}
                      </span>
                    )}
                  </td>
                  <td style={{ textAlign: 'right' }}>{fmtFull(item.price)}</td>
                  <td style={{ textAlign: 'right' }}>{fmtFull(item.cost)}</td>
                  <td style={{ textAlign: 'right' }} title="Cost price × replenish quantity">{fmtFull((item.cost || 0) * (item.replenish_qty || 0))}</td>
                  <td style={{ textAlign: 'right',
                               color: item.margin_pct == null ? 'var(--text-muted)'
                                      : item.margin_pct < 5 ? 'var(--red)'
                                      : item.margin_pct < 15 ? '#f97316'
                                      : '#16a34a',
                               fontWeight: 600 }}>
                    {item.margin_pct == null ? '—' : `${item.margin_pct.toFixed(1)}%`}
                  </td>
                  <td style={{ textAlign: 'right', color: item.on_order > 0 ? 'var(--green)' : 'var(--text-muted)' }}>
                    {item.on_order}
                  </td>
                  <td style={{ textAlign: 'right' }} title="Profit pro-rated by the product's 30-day sell-through rate (slow movers are dampened)">{fmtFull(item.forecast_profit)}</td>
                  <td style={{ textAlign: 'right' }} title="Full gross profit if every replenished unit sells: qty × (sale price − cost)">{fmtFull(item.total_forecast_profit || 0)}</td>
                  <td style={{ textAlign: 'right' }}>
                    <div>{item.days_of_stock}</div>
                    <div style={{ fontSize: 11, color: 'var(--text-muted)' }}>{item.sells_out_date || ''}</div>
                  </td>
                  <td style={{ textAlign: 'right' }}>{item.total_sold_365d}</td>
                  <td style={{ textAlign: 'right' }}
                      title={item.listing_adjusted
                        ? `Velocity calculated over ${item.days_listed} days (newly listed product)`
                        : ''}>
                    {(item.avg_monthly_velocity || 0).toFixed(2)}
                    {item.listing_adjusted && (
                      <span style={{ marginLeft: 4, fontSize: 9, color: '#6366f1', fontWeight: 700 }} title="Newly listed: velocity over actual listed days">★</span>
                    )}
                  </td>
                  <td style={{ textAlign: 'right' }}>{(item.seasonal_monthly_velocity || 0).toFixed(2)}</td>
                </tr>
              ))}
            </tbody>
            {data?.totals && (
              <tfoot>
                <tr style={{ borderTop: '2px solid var(--border)', fontWeight: 700, fontSize: 12 }}>
                  <td />
                  <td style={{ color: 'var(--text-light)' }}>{fmtNum(data.total)} variants</td>
                  <td style={{ textAlign: 'right' }}>{fmtNum(data.totals.total_replenish_qty)}</td>
                  <td style={{ textAlign: 'right' }}>{fmtNum(data.totals.total_stock)}</td>
                  <td />
                  <td style={{ textAlign: 'right' }} title="Sum of replenish_qty × sale price across the items shown — projected revenue from this PO.">
                    {fmtFull(data.totals.total_replenish_revenue || 0)}
                  </td>
                  <td />
                  <td style={{ textAlign: 'right' }} title="Sum of replenish_qty × cost across the items shown — what this PO would cost.">
                    {fmtFull(data.totals.total_replenish_cost || 0)}
                  </td>
                  <td />
                  <td style={{ textAlign: 'right' }}>{fmtNum(data.totals.total_on_order)}</td>
                  <td style={{ textAlign: 'right' }} title="Sum of pro-rated forecast profit across the items shown.">
                    {fmtFull(data.totals.total_forecast_profit || 0)}
                  </td>
                  <td style={{ textAlign: 'right' }} title="Sum of full (non-pro-rated) order profit across the items shown.">
                    {fmtFull(data.totals.total_full_forecast_profit || 0)}
                  </td>
                  <td />
                  <td style={{ textAlign: 'right' }}>{fmtNum(data.totals.total_365d_sales)}</td>
                  <td />
                  <td />
                </tr>
              </tfoot>
            )}
          </table>
          </>
        )}
      </div>

      {/* Bottom Action Bar */}
      <div style={{
        padding: '12px 24px', borderTop: '1px solid var(--border)',
        backgroundColor: 'var(--white)', display: 'flex', alignItems: 'center', gap: 12,
        justifyContent: 'space-between',
      }}>
        <div style={{ display: 'flex', gap: 10, alignItems: 'center' }}>
          <button
            onClick={handleCreateOrder}
            disabled={selected.size === 0 || creatingOrder}
            style={{
              padding: '8px 20px', borderRadius: 6, fontWeight: 600, fontSize: 13,
              backgroundColor: (selected.size > 0 && !creatingOrder) ? 'var(--green)' : '#ccc', color: '#fff',
              display: 'flex', alignItems: 'center', gap: 6,
              cursor: creatingOrder ? 'wait' : undefined,
            }}
          >
            {creatingOrder ? 'Creating order…' : `＋ New purchase order${selected.size > 0 ? ` (${selected.size})` : ''}`}
          </button>

          {/* Add selected items to an existing open PO. Dropdown is sorted
              descending by reference number so the latest PO is at the top. */}
          <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
            <span style={{ fontSize: 12, color: 'var(--text-light)' }}>or add to</span>
            <select
              value={addToPoId}
              onChange={e => setAddToPoId(e.target.value)}
              disabled={addingToPo || openPos.length === 0}
              title={openPos.length === 0 ? 'No open POs' : 'Pick an existing open PO'}
              style={{
                padding: '7px 10px', borderRadius: 6, border: '1px solid var(--border)',
                backgroundColor: 'var(--white)', color: 'var(--text)', fontSize: 12,
                minWidth: 220,
              }}
            >
              <option value="">— existing PO —</option>
              {openPos.map(po => (
                <option key={po.id} value={po.id}>
                  #{po.reference_number} · {po.vendor}
                  {po.item_count ? ` (${po.item_count} items)` : ''}
                </option>
              ))}
            </select>
            <button
              onClick={handleAddToExistingOrder}
              disabled={selected.size === 0 || !addToPoId || addingToPo}
              title={!addToPoId ? 'Pick a PO from the dropdown first' : 'Add selected items to that PO'}
              style={{
                padding: '8px 14px', borderRadius: 6, fontWeight: 600, fontSize: 12,
                border: 'none', cursor: 'pointer',
                backgroundColor: (selected.size > 0 && addToPoId && !addingToPo) ? '#6366f1' : '#ccc',
                color: '#fff',
              }}
            >
              {addingToPo ? 'Adding…' : `Add to PO${selected.size > 0 ? ` (${selected.size})` : ''}`}
            </button>
          </div>

          <button style={{
            padding: '8px 16px', borderRadius: 6, border: '1px solid var(--border)',
            backgroundColor: 'transparent', fontSize: 13,
          }}>
            📤 Export CSV
          </button>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <span style={{ fontSize: 12, color: 'var(--text-light)' }}>Show</span>
          <span style={{ fontSize: 13, fontWeight: 600 }}>{perPage}</span>
          <span style={{ fontSize: 12, color: 'var(--text-light)' }}>items</span>
          <span style={{ margin: '0 8px', color: 'var(--border)' }}>|</span>
          <button
            disabled={page <= 1}
            onClick={() => { setPage(1); }}
            style={{
              padding: '4px 10px', borderRadius: 4, border: '1px solid var(--border)',
              backgroundColor: 'transparent', fontSize: 12,
            }}
          >
            First
          </button>
          <button
            disabled={page <= 1}
            onClick={() => { setPage(p => p - 1); }}
            style={{
              padding: '4px 10px', borderRadius: 4, border: '1px solid var(--border)',
              backgroundColor: 'transparent', fontSize: 12,
            }}
          >
            ‹
          </button>
          <span style={{ fontSize: 12, color: 'var(--text-light)' }}>Page</span>
          <span style={{ fontSize: 13, fontWeight: 600, padding: '0 4px' }}>{page}</span>
          <span style={{ fontSize: 12, color: 'var(--text-light)' }}>of {totalPages}</span>
          <button
            disabled={page >= totalPages}
            onClick={() => { setPage(p => p + 1); }}
            style={{
              padding: '4px 10px', borderRadius: 4, border: '1px solid var(--border)',
              backgroundColor: 'transparent', fontSize: 12,
            }}
          >
            ›
          </button>
          <button
            disabled={page >= totalPages}
            onClick={() => { setPage(totalPages); }}
            style={{
              padding: '4px 10px', borderRadius: 4, border: '1px solid var(--border)',
              backgroundColor: 'transparent', fontSize: 12,
            }}
          >
            Last
          </button>
        </div>
      </div>

      {/* Vendor PO reminder — shown before a PO is created/topped up so the
          forgotten items can still be added. */}
      {poReminder && (
        <div style={{
          position: 'fixed', inset: 0, backgroundColor: 'rgba(0,0,0,0.45)',
          display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 1000,
        }}>
          <div style={{
            backgroundColor: 'var(--white)', borderRadius: 10, width: 460, maxWidth: '90vw',
            boxShadow: '0 12px 40px rgba(0,0,0,0.3)', overflow: 'hidden',
          }}>
            <div style={{
              padding: '14px 20px', backgroundColor: '#fef3c7', borderBottom: '1px solid #fde68a',
              display: 'flex', alignItems: 'center', gap: 10,
            }}>
              <span style={{ fontSize: 20 }}>📌</span>
              <div>
                <div style={{ fontWeight: 700, fontSize: 15 }}>Reminder for {poReminder.vendor}</div>
                <div style={{ fontSize: 12, color: '#92400e' }}>Before you create this purchase order</div>
              </div>
            </div>

            <div style={{
              padding: '20px', fontSize: 14, lineHeight: 1.6, whiteSpace: 'pre-wrap',
            }}>
              {poReminder.text}
            </div>

            <div style={{
              padding: '12px 20px', borderTop: '1px solid var(--border)',
              display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 8,
            }}>
              <button
                onClick={() => closePoReminder('cancel')}
                style={{
                  padding: '8px 14px', borderRadius: 6, border: '1px solid var(--border)',
                  backgroundColor: 'transparent', fontSize: 13, cursor: 'pointer',
                }}>
                Cancel
              </button>
              <div style={{ display: 'flex', gap: 8 }}>
                <button
                  onClick={() => closePoReminder('dismiss')}
                  title="Stop showing this reminder. The text is kept and can be re-enabled on the Vendors page."
                  style={{
                    padding: '8px 14px', borderRadius: 6, border: '1px solid var(--border)',
                    backgroundColor: 'transparent', fontSize: 13, cursor: 'pointer',
                  }}>
                  Don't show again
                </button>
                <button
                  onClick={() => closePoReminder('keep')}
                  style={{
                    padding: '8px 18px', borderRadius: 6, border: 'none', fontWeight: 600,
                    backgroundColor: 'var(--green)', color: '#fff', fontSize: 13, cursor: 'pointer',
                  }}>
                  Got it — remind me next time
                </button>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

// ─── PLACEHOLDER PAGES ──────────────────────────────────────────

function PlaceholderPage({ title, icon, description }) {
  return (
    <div style={{
      display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center',
      height: '100vh', gap: 16,
    }}>
      <span style={{ fontSize: 56 }}>{icon}</span>
      <h2 style={{ fontSize: 22, fontWeight: 700 }}>{title}</h2>
      <p style={{ color: 'var(--text-light)', fontSize: 14 }}>{description}</p>
    </div>
  );
}

// ─── MAIN APP ───────────────────────────────────────────────────

export default function App() {
  const [authenticated, setAuthenticated] = useState(api.isAuthenticated());
  const [currentPage, setCurrentPage] = useState('replenishment');
  const [toast, setToast] = useState(null);
  // Counter that increments every time the Stock Orders sidebar item is
  // clicked. When you're already viewing a PO detail and click the icon,
  // ``setCurrentPage('stockorders')`` is a no-op (page is already that),
  // so we use this counter as a side-channel signal to tell the
  // StockOrdersPage to drop back to the list view.
  const [stockOrdersResetSignal, setStockOrdersResetSignal] = useState(0);
  // Display name for the signed-in token (shown in the sidebar; receipts are
  // attributed to this user server-side).
  const [currentUser, setCurrentUser] = useState(null);

  useEffect(() => {
    if (!authenticated) { setCurrentUser(null); return; }
    api.whoami().then(r => setCurrentUser(r?.user || null)).catch(() => setCurrentUser(null));
  }, [authenticated]);

  // Wrapped navigation handler: if the user clicks Stock Orders while
  // already on Stock Orders, bump the reset counter to send the page
  // back to its list view.
  const navigate = useCallback((page) => {
    if (page === 'stockorders') {
      setStockOrdersResetSignal(n => n + 1);
    }
    setCurrentPage(page);
  }, []);

  const showToast = useCallback((message, type = 'success') => {
    setToast({ message, type });
  }, []);

  if (!authenticated) {
    return <LoginScreen onLogin={() => setAuthenticated(true)} />;
  }

  const renderPage = () => {
    switch (currentPage) {
      case 'overview':
        return <OverviewPage onNavigate={navigate} onToast={showToast} />;
      case 'replenishment':
        return <ReplenishmentPage onToast={showToast} onNavigate={navigate} />;
      case 'stockorders':
        return <StockOrdersPage onToast={showToast} resetSignal={stockOrdersResetSignal} />;
      case 'backorders':
        return <BackordersPage onToast={showToast} onNavigate={navigate} />;
      case 'intelligence':
        return <AnalyticsPage view="intelligence" onToast={showToast} />;
      case 'brands':
        return <AnalyticsPage view="brands" onToast={showToast} />;
      case 'purchasing':
        return <AnalyticsPage view="purchasing" onToast={showToast} />;
      case 'actions':
        return <AnalyticsPage view="actions" onToast={showToast} />;
      case 'analytics':
        return <AnalyticsPage view="intelligence" onToast={showToast} />;
      case 'market':
        return <MarketIntelPage onToast={showToast} />;
      case 'vendors':
        return <VendorManagementPage onToast={showToast} />;
      case 'cogs':
        return <CogsPage onToast={showToast} />;
      case 'sales_manager':
        return <SalesManagerPage onToast={showToast} />;
      case 'sale_performance':
        return <SalePerformancePage onToast={showToast} />;
      case 'waiters':
        return <WaitersPage onToast={showToast} />;
      case 'donations':
        return <DonationsPage onToast={showToast} />;
      case 'po_compare':
        return <PoComparisonPage onToast={showToast} />;
      case 'settings':
        return <SettingsPage onToast={showToast} />;
      case 'tools':
        return <ToolsPage onToast={showToast} />;
      default:
        return <ReplenishmentPage onToast={showToast} onNavigate={navigate} />;
    }
  };

  return (
    <div style={{ display: 'flex', minHeight: '100vh' }}>
      <Sidebar currentPage={currentPage} onNavigate={navigate} currentUser={currentUser} />
      <div style={{ marginLeft: 52, flex: 1, minHeight: '100vh' }}>
        {renderPage()}
      </div>
      {toast && <Toast message={toast.message} type={toast.type} onClose={() => setToast(null)} />}
    </div>
  );
}
