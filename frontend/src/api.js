/**
 * TC Inventory Planner - API Client
 * Handles all communication with the FastAPI backend.
 */

const API_BASE = '/api';

// Token is stored in localStorage after login
function getToken() {
  return localStorage.getItem('tc_planner_token') || '';
}

export function setToken(token) {
  localStorage.setItem('tc_planner_token', token);
}

export function isAuthenticated() {
  return !!getToken();
}

async function apiFetch(path, options = {}) {
  const token = getToken();
  const headers = {
    'Content-Type': 'application/json',
    ...(token && { Authorization: `Bearer ${token}` }),
    ...options.headers,
  };

  const response = await fetch(`${API_BASE}${path}`, {
    ...options,
    headers,
  });

  if (response.status === 401) {
    localStorage.removeItem('tc_planner_token');
    window.location.reload();
    throw new Error('Unauthorized');
  }

  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: 'Request failed' }));
    throw new Error(error.detail || `HTTP ${response.status}`);
  }

  return response.json();
}

// ─── Health ─────────────────────────────────────────────────────

export async function healthCheck() {
  return apiFetch('/health');
}

// ─── Auth ───────────────────────────────────────────────────────

export async function verifyToken(token) {
  setToken(token);
  try {
    await apiFetch('/config/vendors');
    return true;
  } catch {
    localStorage.removeItem('tc_planner_token');
    return false;
  }
}

// ─── Data Refresh ───────────────────────────────────────────────

export async function refreshData() {
  return apiFetch('/refresh', { method: 'POST' });
}

// ─── Replenishment ──────────────────────────────────────────────

export async function getReplenishment({
  vendor = null,
  search = null,
  sort = 'replenish_qty',
  dir = 'desc',
  page = 1,
  perPage = 50,
  needsReplenish = true,
  replenishableOnly = true,
} = {}) {
  const params = new URLSearchParams();
  if (vendor) params.set('vendor', vendor);
  if (search) params.set('search', search);
  params.set('sort', sort);
  params.set('dir', dir);
  params.set('page', page.toString());
  params.set('per_page', perPage.toString());
  params.set('needs_replenish', needsReplenish.toString());
  params.set('replenishable_only', replenishableOnly.toString());

  return apiFetch(`/replenishment?${params}`);
}

// Fetch all matching items in one shot — used for select-all-across-pages
// and for assembling PO line items spanning multiple paginated pages.
export async function getReplenishmentAllItems({
  vendor = null,
  search = null,
  needsReplenish = true,
  replenishableOnly = true,
  skus = null,  // optional: comma-separated SKUs to fetch (overrides filters)
} = {}) {
  const params = new URLSearchParams();
  if (vendor) params.set('vendor', vendor);
  if (search) params.set('search', search);
  params.set('needs_replenish', needsReplenish.toString());
  params.set('replenishable_only', replenishableOnly.toString());
  if (skus) params.set('skus', Array.isArray(skus) ? skus.join(',') : skus);
  return apiFetch(`/replenishment/all-items?${params}`);
}

export async function toggleReplenishable(skus, replenishable) {
  return apiFetch('/replenishment/toggle-replenishable', {
    method: 'POST',
    body: JSON.stringify({ skus, replenishable }),
  });
}

export async function getReplenishmentSummary() {
  return apiFetch('/replenishment/summary');
}

// ─── Vendors ────────────────────────────────────────────────────

export async function getVendors() {
  return apiFetch('/config/vendors');
}

// Turn a vendor's PO reminder popup off (active:false) or back on (active:true).
// The reminder text itself is preserved either way.
export async function dismissPoReminder(vendor, active = false) {
  return apiFetch('/vendors/' + encodeURIComponent(vendor) + '/po-reminder/dismiss', {
    method: 'POST',
    body: JSON.stringify({ active }),
  });
}

// Who am I? Resolves the current token to a user name ("Unknown" = shared token).
export async function whoami() {
  return apiFetch('/auth/whoami');
}

export async function getSeasonalMultipliers() {
  return apiFetch('/config/seasonal-multipliers');
}

// ─── Stock Orders ───────────────────────────────────────────────

export async function listStockOrders({ status = 'open', vendor = null, search = null } = {}) {
  const params = new URLSearchParams({ status });
  if (vendor) params.set('vendor', vendor);
  if (search && search.trim()) params.set('search', search.trim());
  return apiFetch(`/stock-orders?${params}`);
}

export async function getStockOrder(orderId) {
  return apiFetch(`/stock-orders/${orderId}`);
}

export async function createStockOrder({ vendor, items, expectedDate = null, notes = null }) {
  return apiFetch('/stock-orders', {
    method: 'POST',
    body: JSON.stringify({
      vendor,
      expected_date: expectedDate,
      notes,
      items,
    }),
  });
}

export async function updateStockOrderStatus(orderId, status) {
  return apiFetch(`/stock-orders/${orderId}/status`, {
    method: 'PATCH',
    body: JSON.stringify({ status }),
  });
}

export async function deleteStockOrder(orderId) {
  return apiFetch(`/stock-orders/${orderId}?confirm=true`, {
    method: 'DELETE',
  });
}

export async function receiveStockOrderItems(orderId, items) {
  return apiFetch(`/stock-orders/${orderId}/receive`, {
    method: 'POST',
    body: JSON.stringify({ items }),
  });
}

// Send just-received items to the RFID Stickers app's print queue.
// Same {item_id, received_qty} shape as receive; the backend resolves
// SKUs and talks to the RFID app server-to-server.
export async function sendRfidLabels(orderId, items) {
  return apiFetch(`/stock-orders/${orderId}/rfid-labels`, {
    method: 'POST',
    body: JSON.stringify({ items }),
  });
}

export async function prepareStockUpdate(orderId, items = null) {
  return apiFetch(`/stock-orders/${orderId}/prepare-stock-update`, {
    method: 'POST',
    body: JSON.stringify(items ? { items } : {}),
  });
}

export async function applyStockUpdate(orderId, locationId, items) {
  return apiFetch(`/stock-orders/${orderId}/apply-stock-update`, {
    method: 'POST',
    body: JSON.stringify({ location_id: locationId, items }),
  });
}

export async function editStockOrderItem(orderId, itemId, updates) {
  return apiFetch(`/stock-orders/${orderId}/items/${itemId}`, {
    method: 'PATCH',
    body: JSON.stringify(updates),
  });
}

export async function deleteStockOrderItem(orderId, itemId) {
  return apiFetch(`/stock-orders/${orderId}/items/${itemId}`, { method: 'DELETE' });
}

export async function updateStockOrderHeader(orderId, updates) {
  return apiFetch(`/stock-orders/${orderId}`, {
    method: 'PATCH',
    body: JSON.stringify(updates),
  });
}

export async function addStockOrderItem(orderId, item) {
  return apiFetch(`/stock-orders/${orderId}/items`, {
    method: 'POST',
    body: JSON.stringify(item),
  });
}

export async function emailStockOrder(orderId, toEmail = null) {
  return apiFetch(`/stock-orders/${orderId}/email`, {
    method: 'POST',
    body: JSON.stringify(toEmail ? { to_email: toEmail } : {}),
  });
}

export async function uploadMetafields(file, dryRun = false, titleCol = null) {
  const token = localStorage.getItem('tc_planner_token') || '';
  const formData = new FormData();
  formData.append('file', file);

  const params = new URLSearchParams();
  if (dryRun) params.append('dry_run', 'true');
  if (titleCol) params.append('title_col', titleCol);
  const qs = params.toString() ? '?' + params.toString() : '';

  const response = await fetch(`${API_BASE}/metafields/upload${qs}`, {
    method: 'POST',
    headers: { Authorization: `Bearer ${token}` },
    body: formData,
  });

  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: 'Upload failed' }));
    throw new Error(error.detail || `HTTP ${response.status}`);
  }
  return response.json();
}

export async function detectFileHeaders(file) {
  const token = localStorage.getItem('tc_planner_token') || '';
  const formData = new FormData();
  formData.append('file', file);

  const response = await fetch(`${API_BASE}/sales/detect-headers`, {
    method: 'POST',
    headers: { Authorization: `Bearer ${token}` },
    body: formData,
  });

  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: 'Detect failed' }));
    throw new Error(error.detail || `HTTP ${response.status}`);
  }
  return response.json();
}

// ─── Inventory Planner sync ────────────────────────────────────

export async function importStockOrdersFromIP(opts = {}) {
  const { clearExisting = false, includeClosed = false, confirm = false } = opts;
  return apiFetch('/stock-orders/import-from-ip', {
    method: 'POST',
    body: JSON.stringify({
      clear_existing: clearExisting,
      include_closed: includeClosed,
      confirm,
    }),
  });
}

export async function clearAllStockOrders() {
  return apiFetch('/stock-orders/all?confirm=true', { method: 'DELETE' });
}

// ─── Back-in-Stock Waiters ────────────────────────────────────

async function _uploadFile(path, file, extraParams = {}) {
  const token = localStorage.getItem('tc_planner_token') || '';
  const formData = new FormData();
  formData.append('file', file);
  const params = new URLSearchParams();
  for (const [k, v] of Object.entries(extraParams)) {
    if (v !== null && v !== undefined && v !== '') params.append(k, v);
  }
  const qs = params.toString() ? '?' + params.toString() : '';
  const response = await fetch(API_BASE + path + qs, {
    method: 'POST',
    headers: { Authorization: 'Bearer ' + token },
    body: formData,
  });
  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: 'Upload failed' }));
    throw new Error(error.detail || 'HTTP ' + response.status);
  }
  return response.json();
}

export async function previewWaiters(file, snapshotDate = null) {
  return _uploadFile('/waiters/preview', file, { snapshot_date: snapshotDate });
}

export async function importWaiters(file, snapshotDate = null, notes = null) {
  return _uploadFile('/waiters/import', file, {
    snapshot_date: snapshotDate, notes,
  });
}

export async function listWaiterSnapshots() {
  return apiFetch('/waiters/snapshots');
}

export async function deleteWaiterSnapshot(id) {
  return apiFetch('/waiters/snapshots/' + id, { method: 'DELETE' });
}

export async function getCurrentWaiters(vendor = null) {
  const qs = vendor ? '?vendor=' + encodeURIComponent(vendor) : '';
  return apiFetch('/waiters/current' + qs);
}

export async function getWaitersForSku(sku, includeHistory = false) {
  const qs = includeHistory ? '?include_history=true' : '';
  return apiFetch('/waiters/sku/' + encodeURIComponent(sku) + qs);
}

// ─── Settings / Thresholds ────────────────────────────────────

export async function getThresholds() {
  return apiFetch('/settings/thresholds');
}

export async function updateThresholds(updates) {
  return apiFetch('/settings/thresholds', {
    method: 'PUT',
    body: JSON.stringify(updates),
  });
}

// ─── PO Comparison (IP vs TC) ─────────────────────────────────

export async function comparePO(file, options = {}) {
  const { vendor = null, tcOrderId = null } = options;
  const token = localStorage.getItem('tc_planner_token') || '';
  const formData = new FormData();
  formData.append('file', file);

  const params = new URLSearchParams();
  if (vendor) params.append('vendor', vendor);
  if (tcOrderId) params.append('tc_order_id', tcOrderId);
  const qs = params.toString() ? '?' + params.toString() : '';

  const response = await fetch(API_BASE + '/po-comparison/compare' + qs, {
    method: 'POST',
    headers: { Authorization: 'Bearer ' + token },
    body: formData,
  });
  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: 'Comparison failed' }));
    throw new Error(error.detail || 'HTTP ' + response.status);
  }
  return response.json();
}
// ─── Backorders ─────────────────────────────────────────────────

export async function getBackorders() {
  return apiFetch('/backorders');
}

export async function refreshInventoryOnly() {
  return apiFetch('/refresh-inventory', { method: 'POST' });
}

// ─── Overview ───────────────────────────────────────────────────

export async function getOverview() {
  return apiFetch('/overview');
}

// ─── SKU Correction ─────────────────────────────────────────────

export async function correctSkus(file, dryRun = true, titleCol = null) {
  const token = localStorage.getItem('tc_planner_token') || '';
  const formData = new FormData();
  formData.append('file', file);

  const params = new URLSearchParams();
  params.append('dry_run', dryRun ? 'true' : 'false');
  if (titleCol) params.append('title_col', titleCol);
  const qs = '?' + params.toString();

  const response = await fetch(`${API_BASE}/skus/correct${qs}`, {
    method: 'POST',
    headers: { Authorization: `Bearer ${token}` },
    body: formData,
  });

  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: 'Upload failed' }));
    throw new Error(error.detail || `HTTP ${response.status}`);
  }
  return response.json();
}

// ─── ANALYTICS ──────────────────────────────────────────────────

export async function fetchAnalyticsSummary() {
  return apiFetch('/analytics/summary');
}

export async function fetchBrandScorecards() {
  return apiFetch('/analytics/brands');
}

export async function fetchSkuDetails(vendor = null, aging = null) {
  const params = new URLSearchParams();
  if (vendor) params.append('vendor', vendor);
  if (aging) params.append('aging', aging);
  const qs = params.toString();
  return apiFetch(`/analytics/skus${qs ? '?' + qs : ''}`);
}

export async function fetchActionLists() {
  return apiFetch('/analytics/actions');
}

export async function fetchPurchaseHistory(vendor = null, months = 12) {
  const params = new URLSearchParams();
  if (vendor) params.append('vendor', vendor);
  params.append('months', months);
  return apiFetch(`/analytics/purchases?${params.toString()}`);
}

export async function captureSnapshot() {
  return apiFetch('/analytics/snapshot', { method: 'POST' });
}

export async function fetchInventoryTrend(days = 90) {
  return apiFetch(`/analytics/trend?days=${days}`);
}

export async function fetchVendorPurchasing(months = 12) {
  return apiFetch(`/analytics/vendor-purchasing?months=${months}`);
}

// ─── MARKET INTELLIGENCE ────────────────────────────────────────

export async function fetchMarketProducts(vendor = null, limit = 100) {
  const params = new URLSearchParams();
  if (vendor) params.append('vendor', vendor);
  params.append('limit', limit);
  return apiFetch(`/market/products?${params.toString()}`);
}

export async function fetchCompetitors() {
  return apiFetch('/market/competitors');
}

export async function fetchCompetitorPrices(sku = null, competitor = null) {
  const params = new URLSearchParams();
  if (sku) params.append('sku', sku);
  if (competitor) params.append('competitor', competitor);
  const qs = params.toString();
  return apiFetch(`/market/prices${qs ? '?' + qs : ''}`);
}

export async function upsertCompetitorPrice(entry) {
  return apiFetch('/market/prices', { method: 'POST', body: JSON.stringify(entry) });
}

export async function deleteCompetitorPrice(id) {
  return apiFetch(`/market/prices/${id}`, { method: 'DELETE' });
}

export async function fetchMarketSummary() {
  return apiFetch('/market/summary');
}

// ─── VENDOR MANAGEMENT ─────────────────────────────────────────

export async function fetchVendors() {
  return apiFetch('/vendors');
}

export async function upsertVendor(data) {
  return apiFetch('/vendors', { method: 'POST', body: JSON.stringify(data) });
}

export async function fetchFxRate(pair = 'USDCAD') {
  return apiFetch(`/fx-rate?pair=${pair}`);
}

export async function refreshFxRate(pair = 'USDCAD', offsetPct = 0) {
  return apiFetch('/fx-rate/refresh', { method: 'POST', body: JSON.stringify({ pair, offset_pct: offsetPct }) });
}

export async function setFxOffset(pair = 'USDCAD', offsetPct = 0) {
  return apiFetch('/fx-rate/offset', { method: 'POST', body: JSON.stringify({ pair, offset_pct: offsetPct }) });
}

export async function uploadPricelist(vendor, file, opts) {
  const token = localStorage.getItem('tc_planner_token') || '';
  const formData = new FormData();
  formData.append('file', file);
  const params = new URLSearchParams({
    sku_column: opts.skuColumn,
    cost_column: opts.costColumn,
    cost_currency: opts.costCurrency || 'USD',
    msrp_currency: opts.msrpCurrency || 'CAD',
    fallback_msrp_currency: opts.fallbackMsrpCurrency || 'USD',
  });
  if (opts.descColumn) params.append('description_column', opts.descColumn);
  if (opts.msrpColumn) params.append('msrp_column', opts.msrpColumn);
  if (opts.fallbackMsrpColumn) params.append('fallback_msrp_column', opts.fallbackMsrpColumn);
  if (opts.skuSecondaryColumn) params.append('sku_secondary_column', opts.skuSecondaryColumn);
  if (opts.skuSeparator != null) params.append('sku_separator', opts.skuSeparator);
  if (opts.barcodeColumn) params.append('barcode_column', opts.barcodeColumn);
  if (opts.mapCadColumn) params.append('map_cad_column', opts.mapCadColumn);
  if (opts.cooColumn) params.append('coo_column', opts.cooColumn);
  const response = await fetch(API_BASE + '/vendors/' + encodeURIComponent(vendor) + '/pricelist?' + params, {
    method: 'POST',
    headers: { Authorization: 'Bearer ' + token },
    body: formData,
  });
  if (!response.ok) {
    const error = await response.json().catch(function() { return { detail: 'Upload failed' }; });
    throw new Error(error.detail || 'HTTP ' + response.status);
  }
  return response.json();
}

export async function downloadPricelist(vendor) {
  const token = localStorage.getItem('tc_planner_token') || '';
  const response = await fetch(API_BASE + '/vendors/' + encodeURIComponent(vendor) + '/pricelist/download', {
    headers: { Authorization: 'Bearer ' + token },
  });
  if (!response.ok) throw new Error('No pricelist stored');
  const blob = await response.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = 'pricelist_' + vendor.replace(/ /g, '_') + '.csv';
  a.click();
  URL.revokeObjectURL(url);
}

// --- PRODUCT TAGGING ---

export async function addTagToProducts(productIds, tag) {
  return apiFetch('/products/add-tag', {
    method: 'POST',
    body: JSON.stringify({ product_ids: productIds, tag: tag }),
  });
}

export async function removeTagFromProducts(productIds, tag) {
  return apiFetch('/products/remove-tag', {
    method: 'POST',
    body: JSON.stringify({ product_ids: productIds, tag: tag }),
  });
}

export async function detectPricelistColumns(file) {
  const token = localStorage.getItem('tc_planner_token') || '';
  const formData = new FormData();
  formData.append('file', file);
  const response = await fetch(API_BASE + '/vendors/pricelist/detect-columns', {
    method: 'POST',
    headers: { Authorization: 'Bearer ' + token },
    body: formData,
  });
  if (!response.ok) {
    const error = await response.json().catch(function() { return { detail: 'Detection failed' }; });
    throw new Error(error.detail || 'HTTP ' + response.status);
  }
  return response.json();
}

// ── Vendor Sales (Promotional Pricing) ─────────────────────────

// Upload a CSV/XLSX of vendor sale items for a given vendor. ``opts`` keys:
//   skuColumn (req), saleCostColumn (req), saleCurrency (default 'USD'),
//   startsAtColumn / endsAtColumn (CSV columns containing per-row dates),
//   defaultStartsAt / defaultEndsAt (ISO strings used when there are no
//     date columns; e.g. one sale window for the whole sheet).
// Either start/end columns or default values must be supplied.
export async function uploadVendorSale(vendor, file, opts) {
  const token = localStorage.getItem('tc_planner_token') || '';
  const formData = new FormData();
  formData.append('file', file);
  const params = new URLSearchParams({
    sku_column: opts.skuColumn,
    sale_cost_column: opts.saleCostColumn,
    sale_currency: opts.saleCurrency || 'USD',
  });
  if (opts.startsAtColumn) params.append('starts_at_column', opts.startsAtColumn);
  if (opts.endsAtColumn) params.append('ends_at_column', opts.endsAtColumn);
  if (opts.defaultStartsAt) params.append('default_starts_at', opts.defaultStartsAt);
  if (opts.defaultEndsAt) params.append('default_ends_at', opts.defaultEndsAt);
  const response = await fetch(API_BASE + '/vendors/' + encodeURIComponent(vendor) + '/sales?' + params, {
    method: 'POST',
    headers: { Authorization: 'Bearer ' + token },
    body: formData,
  });
  if (!response.ok) {
    const error = await response.json().catch(function() { return { detail: 'Sale upload failed' }; });
    throw new Error(error.detail || 'HTTP ' + response.status);
  }
  return response.json();
}

export async function listVendorSales(vendor) {
  return apiFetch('/vendors/' + encodeURIComponent(vendor) + '/sales');
}

export async function deleteVendorSale(vendor, saleId) {
  return apiFetch('/vendors/' + encodeURIComponent(vendor) + '/sales/' + saleId, {
    method: 'DELETE',
  });
}

export async function clearVendorSales(vendor, onlyExpired) {
  const params = new URLSearchParams({ only_expired: onlyExpired ? 'true' : 'false' });
  return apiFetch('/vendors/' + encodeURIComponent(vendor) + '/sales/clear?' + params, {
    method: 'POST',
  });
}

export async function confirmPricelistMapping(vendor, supplierSku, shopifySku) {
  return apiFetch('/vendors/' + encodeURIComponent(vendor) + '/pricelist/confirm-mapping', {
    method: 'POST',
    body: JSON.stringify({ supplier_sku: supplierSku, shopify_sku: shopifySku }),
  });
}

export async function recomparePricelist(vendor) {
  return apiFetch('/vendors/' + encodeURIComponent(vendor) + '/pricelist/recompare', { method: 'POST' });
}

export async function bulkUpdateCosts(vendor) {
  return apiFetch('/vendors/' + encodeURIComponent(vendor) + '/update-costs', { method: 'POST' });
}

export async function bulkUpdatePrices(vendor) {
  return apiFetch('/vendors/' + encodeURIComponent(vendor) + '/update-prices', { method: 'POST' });
}

export async function bulkUpdateCountryOfOrigin(vendor) {
  return apiFetch('/vendors/' + encodeURIComponent(vendor) + '/update-country-of-origin', { method: 'POST' });
}

export async function getRefreshStatus() {
  return apiFetch('/refresh/status');
}

export async function setProductStatus(productIds, status) {
  return apiFetch('/products/set-status', {
    method: 'POST',
    body: JSON.stringify({ product_ids: productIds, status: status }),
  });
}

export async function setInventoryPolicy(items, policy) {
  return apiFetch('/products/set-inventory-policy', {
    method: 'POST',
    body: JSON.stringify({ items: items, policy: policy }),
  });
}

export async function updateSkuCost(sku, cost) {
  return apiFetch('/products/update-cost', {
    method: 'POST',
    body: JSON.stringify({ sku, cost }),
  });
}

export async function updateVariantSku({ productId, variantId, newSku, oldSku = null }) {
  return apiFetch('/products/update-variant-sku', {
    method: 'POST',
    body: JSON.stringify({
      product_id: productId,
      variant_id: variantId,
      new_sku: newSku,
      old_sku: oldSku,
    }),
  });
}

// --- COGS TRACKING ---

export async function fetchCogsInvoices(vendor) {
  const params = vendor ? '?vendor=' + encodeURIComponent(vendor) : '';
  return apiFetch('/cogs/invoices' + params);
}

export async function fetchInvoiceLots(invoiceId) {
  return apiFetch('/cogs/invoices/' + invoiceId + '/lots');
}

export async function previewInvoice(vendor, file, opts) {
  const token = localStorage.getItem('tc_planner_token') || '';
  const formData = new FormData();
  formData.append('file', file);
  const params = new URLSearchParams({
    vendor: vendor,
    sku_column: opts.skuColumn,
    qty_column: opts.qtyColumn,
    cost_column: opts.costColumn,
    currency: opts.currency || 'USD',
  });
  if (opts.descColumn) params.append('description_column', opts.descColumn);
  const response = await fetch(API_BASE + '/cogs/invoices/preview?' + params, {
    method: 'POST',
    headers: { Authorization: 'Bearer ' + token },
    body: formData,
  });
  if (!response.ok) {
    const error = await response.json().catch(function() { return { detail: 'Preview failed' }; });
    throw new Error(error.detail || 'HTTP ' + response.status);
  }
  return response.json();
}

export async function confirmInvoice(data) {
  return apiFetch('/cogs/invoices/confirm', {
    method: 'POST',
    body: JSON.stringify(data),
  });
}

export async function deleteInvoice(invoiceId) {
  return apiFetch('/cogs/invoices/' + invoiceId, { method: 'DELETE' });
}

export async function fetchCogsSummary(startDate, endDate) {
  const params = new URLSearchParams();
  if (startDate) params.append('start_date', startDate);
  if (endDate) params.append('end_date', endDate);
  const qs = params.toString();
  return apiFetch('/cogs/summary' + (qs ? '?' + qs : ''));
}

export async function aiExtractPdf(file) {
  const token = localStorage.getItem('tc_planner_token') || '';
  const formData = new FormData();
  formData.append('file', file);
  const response = await fetch(API_BASE + '/ai-extract', {
    method: 'POST',
    headers: { Authorization: 'Bearer ' + token },
    body: formData,
  });
  if (!response.ok) {
    const error = await response.json().catch(function() { return { detail: 'AI extraction failed' }; });
    throw new Error(error.detail || 'HTTP ' + response.status);
  }
  return response.json();
}

export async function createDraftProduct(data) {
  return apiFetch('/products/create-draft', {
    method: 'POST',
    body: JSON.stringify(data),
  });
}

// ── Vendor Scrape Config ──────────────────────────────────────

export async function getVendorScrapeConfig(vendor) {
  return apiFetch(`/vendors/${encodeURIComponent(vendor)}/scrape-config`);
}

export async function saveVendorScrapeConfig(vendor, config) {
  return apiFetch(`/vendors/${encodeURIComponent(vendor)}/scrape-config`, {
    method: 'PUT',
    body: JSON.stringify({ config }),
  });
}

export async function resetVendorScrapeConfig(vendor) {
  return apiFetch(`/vendors/${encodeURIComponent(vendor)}/scrape-config`, {
    method: 'DELETE',
  });
}

export async function testVendorScrapeConfig(vendor, productUrl) {
  return apiFetch(`/vendors/${encodeURIComponent(vendor)}/scrape-config/test`, {
    method: 'POST',
    body: JSON.stringify({ product_url: productUrl }),
  });
}

export async function loadDefaultScrapeConfig(vendor) {
  return apiFetch(`/vendors/${encodeURIComponent(vendor)}/scrape-config/load-default`, {
    method: 'POST',
  });
}

// ── Sales Manager ─────────────────────────────────────────────

export async function listSales(vendor, status) {
  const params = new URLSearchParams();
  if (vendor) params.append('vendor', vendor);
  if (status) params.append('status', status);
  const qs = params.toString();
  return apiFetch('/sales' + (qs ? '?' + qs : ''));
}

export async function createSale(data) {
  return apiFetch('/sales', { method: 'POST', body: JSON.stringify(data) });
}

export async function getSale(saleId) {
  return apiFetch('/sales/' + saleId);
}

export async function updateSale(saleId, data) {
  return apiFetch('/sales/' + saleId, { method: 'PUT', body: JSON.stringify(data) });
}

export async function deleteSale(saleId) {
  return apiFetch('/sales/' + saleId, { method: 'DELETE' });
}

export async function uploadSalePricelist(saleId, file, skuCol, priceCol, currency, fxRate, dealerCostCol, dealerCostCurrency, dealerCostFxRate) {
  const token = localStorage.getItem('tc_planner_token') || '';
  const formData = new FormData();
  formData.append('file', file);
  const params = new URLSearchParams({
    sku_col: skuCol, price_col: priceCol,
    price_currency: currency, fx_rate: String(fxRate),
  });
  if (dealerCostCol) params.append('dealer_cost_col', dealerCostCol);
  if (dealerCostCurrency) params.append('dealer_cost_currency', dealerCostCurrency);
  if (dealerCostFxRate) params.append('dealer_cost_fx_rate', String(dealerCostFxRate));
  const response = await fetch(API_BASE + '/sales/' + saleId + '/upload?' + params.toString(), {
    method: 'POST',
    headers: { Authorization: 'Bearer ' + token },
    body: formData,
  });
  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: 'Upload failed' }));
    throw new Error(error.detail || 'HTTP ' + response.status);
  }
  return response.json();
}

export async function confirmSale(saleId, items) {
  return apiFetch('/sales/' + saleId + '/confirm', {
    method: 'POST', body: JSON.stringify({ items }),
  });
}

export async function activateSale(saleId) {
  return apiFetch('/sales/' + saleId + '/activate', { method: 'POST' });
}

export async function revertSale(saleId) {
  return apiFetch('/sales/' + saleId + '/revert', { method: 'POST' });
}

export async function resubmitSale(saleId) {
  return apiFetch('/sales/' + saleId + '/resubmit', { method: 'POST' });
}

export async function excludeSaleItem(saleId, sku) {
  return apiFetch('/sales/' + saleId + '/exclude', {
    method: 'POST', body: JSON.stringify({ sku }),
  });
}

export async function checkSaleSchedule() {
  return apiFetch('/sales/check-schedule', { method: 'POST' });
}

// ── Donations ─────────────────────────────────────────────────

export async function listDonations() {
  return apiFetch('/donations');
}

export async function createDonation(data) {
  return apiFetch('/donations', { method: 'POST', body: JSON.stringify(data) });
}

export async function updateDonation(id, data) {
  return apiFetch('/donations/' + id, { method: 'PUT', body: JSON.stringify(data) });
}

export async function deleteDonation(id) {
  return apiFetch('/donations/' + id, { method: 'DELETE' });
}

export async function lookupSku(sku) {
  return apiFetch('/products/lookup-sku?sku=' + encodeURIComponent(sku));
}

// ── SKU Mappings ──────────────────────────────────────────────

export async function listSkuMappings(vendor, search) {
  const params = search ? '?search=' + encodeURIComponent(search) : '';
  return apiFetch('/vendors/' + encodeURIComponent(vendor) + '/sku-mappings' + params);
}

export async function upsertSkuMapping(vendor, vendorSku, shopifySku) {
  return apiFetch('/vendors/' + encodeURIComponent(vendor) + '/sku-mappings', {
    method: 'POST',
    body: JSON.stringify({ vendor_sku: vendorSku, shopify_sku: shopifySku }),
  });
}

export async function deleteSkuMapping(id) {
  return apiFetch('/sku-mappings/' + id, { method: 'DELETE' });
}

export async function bulkImportSkuMappings(vendor, file, vendorSkuCol, tcSkuCol) {
  const token = localStorage.getItem('tc_planner_token') || '';
  const formData = new FormData();
  formData.append('file', file);
  const params = new URLSearchParams();
  if (vendorSkuCol) params.append('vendor_sku_col', vendorSkuCol);
  if (tcSkuCol) params.append('tc_sku_col', tcSkuCol);
  const url = API_BASE + '/vendors/' + encodeURIComponent(vendor) + '/sku-mappings/bulk-import'
    + (params.toString() ? '?' + params.toString() : '');
  const response = await fetch(url, {
    method: 'POST',
    headers: { Authorization: 'Bearer ' + token },
    body: formData,
  });
  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: 'Import failed' }));
    throw new Error(error.detail || 'HTTP ' + response.status);
  }
  return response.json();
}

// ─── FIFO ORDER MATCHING ────────────────────────────────────────

export async function runFifoMatch(sinceDate, untilDate, reprocess) {
  return apiFetch('/cogs/fifo/run', {
    method: 'POST',
    body: JSON.stringify({ since_date: sinceDate, until_date: untilDate || null, reprocess: !!reprocess }),
  });
}

export async function fetchFifoOrders(opts) {
  const params = new URLSearchParams();
  if (opts?.startDate) params.append('start_date', opts.startDate);
  if (opts?.endDate) params.append('end_date', opts.endDate);
  if (opts?.vendor) params.append('vendor', opts.vendor);
  if (opts?.limit) params.append('limit', String(opts.limit));
  if (opts?.offset) params.append('offset', String(opts.offset));
  const qs = params.toString();
  return apiFetch('/cogs/orders' + (qs ? '?' + qs : ''));
}

export async function fetchFifoOrderDetail(orderNumber) {
  return apiFetch('/cogs/orders/' + encodeURIComponent(orderNumber));
}

// ─── OPERATING EXPENSES ─────────────────────────────────────────

export async function fetchExpenses(opts) {
  const params = new URLSearchParams();
  if (opts?.startDate) params.append('start_date', opts.startDate);
  if (opts?.endDate) params.append('end_date', opts.endDate);
  if (opts?.category) params.append('category', opts.category);
  if (opts?.limit) params.append('limit', String(opts.limit));
  if (opts?.offset) params.append('offset', String(opts.offset));
  const qs = params.toString();
  return apiFetch('/expenses' + (qs ? '?' + qs : ''));
}

export async function fetchExpensesSummary(startDate, endDate) {
  const params = new URLSearchParams();
  if (startDate) params.append('start_date', startDate);
  if (endDate) params.append('end_date', endDate);
  const qs = params.toString();
  return apiFetch('/expenses/summary' + (qs ? '?' + qs : ''));
}

export async function fetchExpenseCategories() {
  return apiFetch('/expenses/categories');
}

export async function createExpense(data) {
  return apiFetch('/expenses', { method: 'POST', body: JSON.stringify(data) });
}

export async function updateExpense(id, data) {
  return apiFetch('/expenses/' + id, { method: 'PUT', body: JSON.stringify(data) });
}

export async function deleteExpense(id) {
  return apiFetch('/expenses/' + id, { method: 'DELETE' });
}

export async function uploadExpensesCsv(file, opts) {
  const token = localStorage.getItem('tc_planner_token') || '';
  const formData = new FormData();
  formData.append('file', file);
  const params = new URLSearchParams({
    date_column: opts.dateColumn,
    category_column: opts.categoryColumn,
    amount_column: opts.amountColumn,
  });
  if (opts.descriptionColumn) params.append('description_column', opts.descriptionColumn);
  if (opts.vendorColumn) params.append('vendor_column', opts.vendorColumn);
  if (opts.periodStartColumn) params.append('period_start_column', opts.periodStartColumn);
  if (opts.periodEndColumn) params.append('period_end_column', opts.periodEndColumn);
  if (opts.currencyColumn) params.append('currency_column', opts.currencyColumn);
  if (opts.defaultCurrency) params.append('default_currency', opts.defaultCurrency);
  const response = await fetch(API_BASE + '/expenses/upload?' + params, {
    method: 'POST',
    headers: { Authorization: 'Bearer ' + token },
    body: formData,
  });
  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: 'Upload failed' }));
    throw new Error(error.detail || 'HTTP ' + response.status);
  }
  return response.json();
}

// ─── RECURRING EXPENSE TEMPLATES ────────────────────────────────

export async function fetchRecurringTemplates(includeInactive) {
  const qs = includeInactive ? '?include_inactive=true' : '';
  return apiFetch('/expenses/templates' + qs);
}

export async function createRecurringTemplate(data) {
  return apiFetch('/expenses/templates', { method: 'POST', body: JSON.stringify(data) });
}

export async function updateRecurringTemplate(id, data) {
  return apiFetch('/expenses/templates/' + id, { method: 'PUT', body: JSON.stringify(data) });
}

export async function deleteRecurringTemplate(id, cascade) {
  return apiFetch('/expenses/templates/' + id + (cascade ? '?cascade=true' : ''), { method: 'DELETE' });
}

// ─── SALE PERFORMANCE ───────────────────────────────────────────

export async function fetchSkuLeaderboard(opts) {
  const params = new URLSearchParams();
  if (opts?.startDate) params.append('start_date', opts.startDate);
  if (opts?.endDate) params.append('end_date', opts.endDate);
  if (opts?.vendor) params.append('vendor', opts.vendor);
  if (opts?.method) params.append('method', opts.method);
  if (opts?.sortBy) params.append('sort_by', opts.sortBy);
  if (opts?.limit) params.append('limit', String(opts.limit));
  const qs = params.toString();
  return apiFetch('/sale-performance/skus' + (qs ? '?' + qs : ''));
}

export async function fetchSalePerformanceOrders(opts) {
  const params = new URLSearchParams();
  if (opts?.startDate) params.append('start_date', opts.startDate);
  if (opts?.endDate) params.append('end_date', opts.endDate);
  if (opts?.vendor) params.append('vendor', opts.vendor);
  if (opts?.method) params.append('method', opts.method);
  if (opts?.limit) params.append('limit', String(opts.limit));
  if (opts?.offset) params.append('offset', String(opts.offset));
  const qs = params.toString();
  return apiFetch('/sale-performance/orders' + (qs ? '?' + qs : ''));
}

// ─── MIN STOCK ──────────────────────────────────────────────────

export async function listMinStock(onlySet = true) {
  const qs = onlySet ? '' : '?only_set=false';
  return apiFetch('/min-stock' + qs);
}

export async function setMinStock(sku, minStock, notes) {
  return apiFetch('/min-stock', {
    method: 'POST',
    body: JSON.stringify({ sku, min_stock: minStock, notes: notes || null }),
  });
}

export async function bulkSetMinStock(items) {
  return apiFetch('/min-stock/bulk', { method: 'POST', body: JSON.stringify({ items }) });
}

// ─── BARCODE LABEL PRINT QUEUE ───────────────────────────────────
// Posts label jobs to the cloud-side queue; the local Python agent
// (running on the receiving PC) polls /api/labels/pending and does
// the actual ZPL generation + Browser Print dispatch.
export async function queueLabelPrintJobs(jobs) {
  return apiFetch('/labels', {
    method: 'POST',
    body: JSON.stringify({ jobs }),
  });
}

// ─── VARIANT BIN METAFIELD ───────────────────────────────────────
// Updates the stock.bin variant metafield in Shopify for a SKU.
// An empty value clears the metafield.
export async function updateVariantBin(sku, value) {
  return apiFetch('/variants/bin', {
    method: 'POST',
    body: JSON.stringify({ sku, value: value || '' }),
  });
}

// ─── VARIANT BARCODE ─────────────────────────────────────────────
// Updates the native barcode field in Shopify for a SKU's variant.
// An empty value clears the barcode.
export async function updateVariantBarcode(sku, value) {
  return apiFetch('/variants/barcode', {
    method: 'POST',
    body: JSON.stringify({ sku, value: value || '' }),
  });
}

// Bump each line's ordered_qty up to the live waitlist coverage gap.
// Used by the PO detail header "Top up waiters" button when newer
// snapshots have caused this PO to fall behind the current waitlist.
export async function topUpWaiterCoverage(orderId) {
  return apiFetch(`/stock-orders/${orderId}/top-up-waiter-coverage`, {
    method: 'POST',
  });
}

// ─── PENDING INVOICES (email ingest) ────────────────────────────

export async function fetchPendingInvoices(status) {
  const qs = status ? '?status=' + encodeURIComponent(status) : '';
  return apiFetch('/cogs/invoices/pending' + qs);
}

export async function fetchPendingInvoice(id) {
  return apiFetch('/cogs/invoices/pending/' + id);
}

export async function previewPendingMatch(id, vendor) {
  const qs = vendor ? '?vendor=' + encodeURIComponent(vendor) : '';
  return apiFetch('/cogs/invoices/pending/' + id + '/preview-match' + qs);
}

export async function approvePendingInvoice(id, data) {
  return apiFetch('/cogs/invoices/pending/' + id + '/approve', {
    method: 'POST', body: JSON.stringify(data),
  });
}

export async function rejectPendingInvoice(id, reason) {
  return apiFetch('/cogs/invoices/pending/' + id + '/reject', {
    method: 'POST', body: JSON.stringify({ reason: reason || null }),
  });
}

export async function deletePendingInvoice(id) {
  return apiFetch('/cogs/invoices/pending/' + id, { method: 'DELETE' });
}

// ─── VENDOR ALIASES ─────────────────────────────────────────────

export async function fetchVendorAliases() {
  return apiFetch('/vendor-aliases');
}

export async function createVendorAlias(alias, canonicalVendor) {
  return apiFetch('/vendor-aliases', {
    method: 'POST',
    body: JSON.stringify({ alias, canonical_vendor: canonicalVendor }),
  });
}

export async function deleteVendorAlias(id) {
  return apiFetch('/vendor-aliases/' + id, { method: 'DELETE' });
}

// ─── LLM INVOICE PARSING ────────────────────────────────────────

export async function fetchLlmInvoiceModels() {
  return apiFetch('/cogs/invoices/llm-models');
}

export async function llmParseInvoice(file, model) {
  const token = localStorage.getItem('tc_planner_token') || '';
  const formData = new FormData();
  formData.append('file', file);
  const params = new URLSearchParams();
  if (model) params.append('model', model);
  const qs = params.toString();
  const response = await fetch(API_BASE + '/cogs/invoices/llm-parse' + (qs ? '?' + qs : ''), {
    method: 'POST',
    headers: { Authorization: 'Bearer ' + token },
    body: formData,
  });
  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: 'AI parse failed' }));
    throw new Error(error.detail || 'HTTP ' + response.status);
  }
  return response.json();
}
