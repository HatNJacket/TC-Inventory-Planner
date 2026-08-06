import { useState, useEffect, useRef, Fragment } from 'react';
import * as api from './api';
import ExpensesTab from './ExpensesTab';
import PendingInvoicesTab from './PendingInvoicesTab';

const fmt = (n) => new Intl.NumberFormat('en-CA', { style: 'currency', currency: 'CAD', minimumFractionDigits: 0, maximumFractionDigits: 0 }).format(n || 0);
const fmtFull = (n) => new Intl.NumberFormat('en-CA', { style: 'currency', currency: 'CAD', minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(n || 0);

// --- MATCH TYPE BADGE ---
function MatchBadge({ type }) {
  const styles = {
    exact: { bg: '#dcfce7', color: '#16a34a', label: 'Exact' },
    mapped: { bg: '#dbeafe', color: '#2563eb', label: 'Saved' },
    suggested: { bg: '#fef3c7', color: '#d97706', label: 'Suggested' },
    unmatched: { bg: '#fee2e2', color: '#dc2626', label: 'Unmatched' },
  };
  const s = styles[type] || styles.unmatched;
  return (
    <span style={{ padding: '2px 8px', borderRadius: 4, fontSize: 10, fontWeight: 600,
      backgroundColor: s.bg, color: s.color }}>{s.label}</span>
  );
}

// --- INVOICE UPLOAD MODAL ---
function InvoiceUploadModal({ onDone, onClose, onToast }) {
  const [mode, setMode] = useState('ai'); // 'ai' | 'manual'
  const [aiModel, setAiModel] = useState('claude');
  const [aiParsing, setAiParsing] = useState(false);
  const [aiResult, setAiResult] = useState(null); // raw LLM output for display
  const [vendor, setVendor] = useState('');
  const [invoiceNum, setInvoiceNum] = useState('');
  const [invoiceDate, setInvoiceDate] = useState(new Date().toISOString().split('T')[0]);
  const [currency, setCurrency] = useState('USD');
  const [isVendorSale, setIsVendorSale] = useState(false);
  const [file, setFile] = useState(null);
  const [columns, setColumns] = useState([]);
  const [skuCol, setSkuCol] = useState('');
  const [qtyCol, setQtyCol] = useState('');
  const [costCol, setCostCol] = useState('');
  const [descCol, setDescCol] = useState('');
  const [detecting, setDetecting] = useState(false);
  const [previewing, setPreviewing] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [preview, setPreview] = useState(null);
  const [matchItems, setMatchItems] = useState([]);
  const [result, setResult] = useState(null);
  const fileRef = useRef();

  const handleFileChange = async (e) => {
    const f = e.target.files[0];
    if (!f) return;
    setFile(f);
    if (mode === 'ai') {
      // AI path: send straight to the LLM, autofill header fields, then
      // synthesize a CSV file that the existing preview endpoint can match.
      setAiParsing(true);
      try {
        const ai = await api.llmParseInvoice(f, aiModel);
        setAiResult(ai);
        if (ai.vendor) setVendor(ai.vendor);
        if (ai.invoice_number) setInvoiceNum(ai.invoice_number);
        if (ai.invoice_date) setInvoiceDate(ai.invoice_date);
        if (ai.currency) setCurrency(ai.currency);
        const csv = ai.csv_content || '';
        const itemCount = (ai.items || []).filter(i => !i.is_skip && !i.is_shipping && i.vendor_sku).length;
        const skipped = (ai.items || []).length - itemCount;
        if (!itemCount) {
          onToast?.('AI parsed but found no usable line items. Try the Manual flow.', 'error');
          setAiParsing(false);
          return;
        }
        const sug = ai.suggested_columns || {};
        const csvFile = new File([csv], (f.name || 'invoice') + '.csv', { type: 'text/csv' });
        const res = await api.previewInvoice(ai.vendor || '', csvFile, {
          skuColumn: sug.sku_column || 'vendor_sku',
          qtyColumn: sug.qty_column || 'quantity',
          costColumn: sug.cost_column || 'unit_cost',
          currency: ai.currency || 'USD',
          descColumn: sug.description_column || 'description',
        });
        if (res.status === 'ok') {
          setPreview(res);
          setMatchItems(res.items.map(i => ({ ...i })));
          onToast?.(
            'AI extracted ' + itemCount + ' lines' +
            (skipped > 0 ? ' (' + skipped + ' shipping/skip rows omitted)' : '') +
            ' · ' + (ai.tokens_used || 0) + ' tokens',
            'success'
          );
        } else {
          onToast?.(res.message || 'AI preview failed — switch to Manual flow', 'error');
        }
      } catch (err) {
        onToast?.('AI parse failed: ' + err.message, 'error');
      }
      setAiParsing(false);
      return;
    }
    // Manual path: detect column headers from the raw file.
    setDetecting(true);
    try {
      const res = await api.detectPricelistColumns(f);
      const cols = res.columns || [];
      setColumns(cols);
      cols.forEach(c => {
        const cl = c.toLowerCase();
        if (!skuCol && (cl.includes('sku') || cl.includes('item') || cl.includes('part') || cl.includes('model'))) setSkuCol(c);
        if (!qtyCol && (cl.includes('qty') || cl.includes('quantity') || cl.includes('ordered') || cl.includes('shipped'))) setQtyCol(c);
        if (!costCol && (cl.includes('cost') || cl.includes('price') || cl.includes('unit') || cl.includes('dealer'))) setCostCol(c);
        if (!descCol && (cl.includes('desc') || cl.includes('name') || cl.includes('title') || cl.includes('product'))) setDescCol(c);
      });
    } catch (err) { onToast?.('Could not read file: ' + err.message, 'error'); }
    setDetecting(false);
  };

  const handlePreview = async () => {
    if (!file || !vendor || !skuCol || !qtyCol || !costCol) return;
    setPreviewing(true);
    try {
      const res = await api.previewInvoice(vendor, file, {
        skuColumn: skuCol, qtyColumn: qtyCol, costColumn: costCol,
        currency, descColumn: descCol || undefined,
      });
      if (res.status === 'ok') {
        setPreview(res);
        setMatchItems(res.items.map(i => ({ ...i })));
      } else {
        onToast?.(res.message || 'Preview failed', 'error');
      }
    } catch (err) { onToast?.('Preview failed: ' + err.message, 'error'); }
    setPreviewing(false);
  };

  const handleConfirm = async () => {
    if (!invoiceNum) { onToast?.('Enter an invoice number', 'error'); return; }
    setConfirming(true);
    try {
      const res = await api.confirmInvoice({
        vendor: vendor,
        invoice_number: invoiceNum,
        invoice_date: invoiceDate,
        currency: preview.currency,
        fx_rate: preview.fx_rate,
        items: matchItems.filter(i => i.matched_sku),
        is_vendor_sale: isVendorSale,
      });
      if (res.status === 'ok') {
        setResult(res);
        onToast?.('Invoice imported: ' + res.line_items + ' items, ' + fmtFull(res.total_cost_cad), 'success');
      } else {
        onToast?.(res.message || 'Import failed', 'error');
      }
    } catch (err) { onToast?.('Import failed: ' + err.message, 'error'); }
    setConfirming(false);
  };

  const updateMatch = (idx, sku, title) => {
    setMatchItems(prev => {
      const next = [...prev];
      next[idx] = { ...next[idx], matched_sku: sku, matched_title: title, match_type: sku ? 'manual' : 'unmatched' };
      return next;
    });
  };

  const clearMatch = (idx) => updateMatch(idx, '', '');

  const ColSelect = ({ label, value, onChange, required }) => (
    <div>
      <div style={{ fontSize: 11, color: 'var(--text-muted)', marginBottom: 4 }}>{label}{required && ' *'}</div>
      <select value={value} onChange={e => onChange(e.target.value)} style={{
        padding: '6px 10px', borderRadius: 6, border: '1px solid var(--border)',
        backgroundColor: 'var(--input-bg)', color: 'var(--text)', fontSize: 12, width: '100%',
      }}>
        <option value="">-- Select --</option>
        {columns.map(c => <option key={c} value={c}>{c}</option>)}
      </select>
    </div>
  );

  const inputStyle = {
    padding: '6px 10px', borderRadius: 6, border: '1px solid var(--border)',
    backgroundColor: 'var(--input-bg)', color: 'var(--text)', fontSize: 12, width: '100%',
  };

  return (
    <div style={{
      position: 'fixed', top: 0, left: 0, right: 0, bottom: 0,
      backgroundColor: 'rgba(0,0,0,0.5)', zIndex: 1000,
      display: 'flex', alignItems: 'center', justifyContent: 'center',
    }} onClick={onClose}>
      <div style={{
        backgroundColor: 'var(--card-bg)', borderRadius: 12, padding: 24,
        width: 1000, maxHeight: '90vh', overflowY: 'auto',
        boxShadow: '0 8px 32px rgba(0,0,0,0.3)',
      }} onClick={e => e.stopPropagation()}>
        <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 16 }}>
          <div style={{ fontSize: 16, fontWeight: 600 }}>Upload Purchase Invoice</div>
          <button onClick={onClose} style={{ border: 'none', background: 'none', cursor: 'pointer', fontSize: 18, color: 'var(--text-muted)' }}>x</button>
        </div>

        {result?.status === 'ok' ? (
          <>
            <div style={{ padding: 16, borderRadius: 8, backgroundColor: 'var(--hover-bg)', marginBottom: 16 }}>
              <div style={{ fontSize: 14, fontWeight: 600, marginBottom: 8, color: '#22c55e' }}>Invoice imported successfully</div>
              <div style={{ fontSize: 12 }}>
                {result.vendor} -- #{result.invoice_number} -- {result.invoice_date}
              </div>
              <div style={{ fontSize: 12, marginTop: 4 }}>
                {result.line_items} items imported, {result.skipped_items} skipped, {fmtFull(result.total_cost_cad)} CAD
              </div>
            </div>
            <button onClick={() => { setResult(null); setPreview(null); onDone(); }} style={{
              padding: '8px 16px', borderRadius: 6, border: '1px solid var(--border)',
              cursor: 'pointer', backgroundColor: 'transparent', color: 'var(--text)', fontSize: 13,
            }}>Close</button>
          </>

        ) : preview ? (
          /* STEP 2: Matching preview */
          <>
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 8, marginBottom: 16 }}>
              {[
                { label: 'Exact/Saved Matches', value: preview.matched_count, color: '#22c55e' },
                { label: 'Suggested', value: preview.suggested_count, color: '#d97706' },
                { label: 'Unmatched', value: preview.unmatched_count, color: '#ef4444' },
                { label: 'Total Items', value: preview.total_items, color: '#6366f1' },
              ].map((c, i) => (
                <div key={i} style={{ padding: 10, borderRadius: 6, borderLeft: '4px solid ' + c.color,
                  border: '1px solid var(--border)', backgroundColor: 'var(--card-bg)' }}>
                  <div style={{ fontSize: 10, color: 'var(--text-muted)' }}>{c.label}</div>
                  <div style={{ fontSize: 18, fontWeight: 700 }}>{c.value}</div>
                </div>
              ))}
            </div>

            {preview.fx_rate !== 1 && (
              <div style={{ fontSize: 12, color: 'var(--text-muted)', marginBottom: 12, padding: '6px 10px', backgroundColor: 'var(--hover-bg)', borderRadius: 6 }}>
                FX: 1 {preview.currency} = {preview.fx_rate.toFixed(4)} CAD
              </div>
            )}

            <div style={{ maxHeight: 400, overflowY: 'auto', marginBottom: 16 }}>
              <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
                <thead>
                  <tr>
                    {['Status', 'Vendor SKU', 'Description', 'Qty', 'Cost (' + preview.currency + ')', 'Cost (CAD)', 'Matched Shopify SKU'].map(h => (
                      <th key={h} style={{ padding: '6px 8px', fontSize: 11, fontWeight: 600, color: 'var(--text-muted)',
                        textAlign: ['Qty', 'Cost (' + preview.currency + ')', 'Cost (CAD)'].includes(h) ? 'right' : 'left',
                        borderBottom: '2px solid var(--border)', position: 'sticky', top: 0, backgroundColor: 'var(--card-bg)', zIndex: 1 }}>{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {matchItems.map((item, idx) => (
                    <tr key={idx} style={{ backgroundColor: idx % 2 === 0 ? 'transparent' : 'var(--row-alt)' }}>
                      <td style={{ padding: '4px 8px' }}><MatchBadge type={item.match_type} /></td>
                      <td style={{ padding: '4px 8px', fontFamily: 'monospace', fontSize: 11 }}>{item.vendor_sku}</td>
                      <td style={{ padding: '4px 8px', maxWidth: 200, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{item.description}</td>
                      <td style={{ padding: '4px 8px', textAlign: 'right' }}>{item.quantity}</td>
                      <td style={{ padding: '4px 8px', textAlign: 'right' }}>{fmtFull(item.unit_cost_foreign)}</td>
                      <td style={{ padding: '4px 8px', textAlign: 'right', fontWeight: 600 }}>{fmtFull(item.unit_cost_cad)}</td>
                      <td style={{ padding: '4px 8px' }}>
                        {item.matched_sku ? (
                          <div style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
                            <span style={{ fontFamily: 'monospace', fontSize: 11 }}>{item.matched_sku}</span>
                            <span style={{ fontSize: 10, color: 'var(--text-muted)', maxWidth: 150, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                              {item.matched_title}
                            </span>
                            {item.match_type !== 'exact' && item.match_type !== 'mapped' && (
                              <button onClick={() => clearMatch(idx)} style={{ border: 'none', background: 'none', cursor: 'pointer', fontSize: 11, color: '#ef4444', padding: '0 2px' }}>x</button>
                            )}
                          </div>
                        ) : (
                          <div>
                            {item.suggestions?.length > 0 ? (
                              <select onChange={e => {
                                const s = item.suggestions.find(sg => sg.sku === e.target.value);
                                if (s) updateMatch(idx, s.sku, s.title);
                              }} value="" style={{ fontSize: 11, padding: '2px 4px', borderRadius: 4, border: '1px solid var(--border)', backgroundColor: 'var(--input-bg)', color: 'var(--text)' }}>
                                <option value="">-- Pick match --</option>
                                {item.suggestions.map(s => (
                                  <option key={s.sku} value={s.sku}>{s.sku} - {s.title}</option>
                                ))}
                              </select>
                            ) : (
                              <span style={{ fontSize: 11, color: 'var(--text-muted)' }}>No match found</span>
                            )}
                          </div>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
              <button onClick={() => setPreview(null)} style={{
                padding: '8px 16px', borderRadius: 6, border: '1px solid var(--border)',
                cursor: 'pointer', backgroundColor: 'transparent', color: 'var(--text)', fontSize: 13,
              }}>Back</button>
              <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                <div>
                  <div style={{ fontSize: 10, color: 'var(--text-muted)' }}>Invoice # *</div>
                  <input value={invoiceNum} onChange={e => setInvoiceNum(e.target.value)}
                    placeholder="INV-001" style={{ ...inputStyle, width: 140 }} />
                </div>
                <div>
                  <div style={{ fontSize: 10, color: 'var(--text-muted)' }}>Date</div>
                  <input type="date" value={invoiceDate} onChange={e => setInvoiceDate(e.target.value)}
                    style={{ ...inputStyle, width: 140 }} />
                </div>
                <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 12, paddingTop: 18, cursor: 'pointer' }}
                  title="Flag every lot from this invoice as 'bought on sale' so margin reports can attribute the lower cost basis.">
                  <input type="checkbox" checked={isVendorSale} onChange={e => setIsVendorSale(e.target.checked)} />
                  Vendor sale invoice
                </label>
              </div>
              <div style={{ flex: 1 }} />
              <span style={{ fontSize: 12, color: 'var(--text-muted)' }}>
                {matchItems.filter(i => i.matched_sku).length} of {matchItems.length} matched
              </span>
              <button onClick={handleConfirm} disabled={confirming || !invoiceNum}
                style={{
                  padding: '8px 20px', borderRadius: 6, border: 'none', cursor: 'pointer',
                  backgroundColor: '#22c55e', color: '#fff', fontWeight: 600, fontSize: 13,
                  opacity: (confirming || !invoiceNum) ? 0.5 : 1,
                }}>
                {confirming ? 'Importing...' : 'Confirm & Import'}
              </button>
            </div>
          </>

        ) : (
          /* STEP 1: File upload and column mapping */
          <>
            {/* Mode toggle: AI parse vs manual column mapping */}
            <div style={{ display: 'flex', gap: 2, backgroundColor: '#f0f1f3', borderRadius: 6, padding: 2, marginBottom: 16, width: 'fit-content' }}>
              {[
                ['ai', '✨ Parse with AI (PDF/XLSX/CSV)'],
                ['manual', '🔧 Manual column mapping'],
              ].map(([v, l]) => (
                <button key={v} onClick={() => { setMode(v); setColumns([]); setAiResult(null); }}
                  style={{ padding: '6px 14px', borderRadius: 4, border: 'none', fontSize: 12, fontWeight: 600, cursor: 'pointer',
                    backgroundColor: mode === v ? '#fff' : 'transparent',
                    color: mode === v ? '#6366f1' : 'var(--text-muted)',
                    boxShadow: mode === v ? '0 1px 3px rgba(0,0,0,0.1)' : 'none' }}>{l}</button>
              ))}
            </div>

            {mode === 'ai' && (
              <div style={{ marginBottom: 12, padding: '10px 12px', borderRadius: 6, backgroundColor: 'var(--hover-bg)', fontSize: 12, color: 'var(--text-muted)' }}>
                The AI reads the document and fills in vendor, invoice number, date, currency, and line items automatically. You'll review the matched preview before confirming.
                <div style={{ marginTop: 8, display: 'flex', gap: 8, alignItems: 'center' }}>
                  <span style={{ fontSize: 11 }}>Model:</span>
                  <select value={aiModel} onChange={e => setAiModel(e.target.value)} style={{
                    padding: '4px 8px', borderRadius: 4, border: '1px solid var(--border)',
                    backgroundColor: 'var(--input-bg)', color: 'var(--text)', fontSize: 12,
                  }}>
                    <option value="claude">Claude Sonnet 4.6</option>
                    <option value="claude-opus">Claude Opus 4.6 (most accurate, costlier)</option>
                    <option value="gpt">GPT-5</option>
                    <option value="gpt-mini">GPT-5 Mini (fastest)</option>
                  </select>
                  <span style={{ fontSize: 10 }}>Try a few on the same invoice and pick the one that's most accurate for your suppliers.</span>
                </div>
              </div>
            )}

            {mode === 'manual' && (
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 100px', gap: 8, marginBottom: 16 }}>
                <div>
                  <div style={{ fontSize: 11, color: 'var(--text-muted)', marginBottom: 4 }}>Vendor *</div>
                  <input value={vendor} onChange={e => setVendor(e.target.value)}
                    placeholder="e.g. Celestron" style={inputStyle} />
                </div>
                <div style={{ fontSize: 11, color: 'var(--text-muted)', paddingTop: 18 }}>
                  Invoice # and date entered on the next step after matching
                </div>
                <div>
                  <div style={{ fontSize: 11, color: 'var(--text-muted)', marginBottom: 4 }}>Currency</div>
                  <select value={currency} onChange={e => setCurrency(e.target.value)} style={inputStyle}>
                    <option value="USD">USD</option>
                    <option value="CAD">CAD</option>
                    <option value="EUR">EUR</option>
                  </select>
                </div>
              </div>
            )}

            <div style={{ marginBottom: 16 }}>
              <input ref={fileRef} type="file" accept=".csv,.tsv,.txt,.xlsx,.xls,.pdf" onChange={handleFileChange}
                style={{ fontSize: 13 }} />
              {detecting && <span style={{ fontSize: 12, color: 'var(--text-muted)', marginLeft: 8 }}>Detecting columns...</span>}
              {aiParsing && <span style={{ fontSize: 12, color: '#6366f1', marginLeft: 8 }}>Parsing with AI — can take 30–60s for big invoices...</span>}
            </div>

            {aiResult && !preview && !aiParsing && (
              <div style={{ marginBottom: 16, padding: '10px 12px', borderRadius: 6, backgroundColor: '#fef9c3', color: '#854d0e', fontSize: 12, border: '1px solid #fde68a' }}>
                AI extracted {aiResult.items?.length || 0} rows but the matching preview failed.
                Switch to Manual column mapping to recover.
              </div>
            )}

            {mode === 'manual' && columns.length > 0 && (
              <>
                <div style={{ fontSize: 12, color: 'var(--text-muted)', marginBottom: 8 }}>
                  Detected {columns.length} columns. Map them below:
                </div>
                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr 1fr', gap: 8, marginBottom: 16 }}>
                  <ColSelect label="SKU Column *" value={skuCol} onChange={setSkuCol} required />
                  <ColSelect label="Quantity *" value={qtyCol} onChange={setQtyCol} required />
                  <ColSelect label="Unit Cost *" value={costCol} onChange={setCostCol} required />
                  <ColSelect label="Description" value={descCol} onChange={setDescCol} />
                </div>
                <button onClick={handlePreview}
                  disabled={previewing || !vendor || !skuCol || !qtyCol || !costCol}
                  style={{
                    padding: '8px 20px', borderRadius: 6, border: 'none', cursor: 'pointer',
                    backgroundColor: '#6366f1', color: '#fff', fontWeight: 600, fontSize: 13,
                    opacity: (previewing || !vendor || !skuCol || !qtyCol || !costCol) ? 0.5 : 1,
                  }}>
                  {previewing ? 'Matching...' : 'Preview Matches'}
                </button>
              </>
            )}
          </>
        )}
      </div>
    </div>
  );
}


// --- FIFO RUN MODAL ---
function FifoRunModal({ onDone, onClose, onToast }) {
  const today = new Date().toISOString().split('T')[0];
  const ninetyDaysAgo = new Date(Date.now() - 90 * 86400 * 1000).toISOString().split('T')[0];
  const [sinceDate, setSinceDate] = useState(ninetyDaysAgo);
  const [untilDate, setUntilDate] = useState(today);
  const [reprocess, setReprocess] = useState(false);
  const [running, setRunning] = useState(false);
  const [result, setResult] = useState(null);

  const handleRun = async () => {
    setRunning(true);
    try {
      const res = await api.runFifoMatch(sinceDate, untilDate || null, reprocess);
      setResult(res);
      if (res.status === 'ok') {
        onToast?.(
          'FIFO match: ' + res.orders_processed + ' orders, ' +
          res.journal_rows + ' lot matches, ' +
          res.unmatched_rows + ' unmatched lines',
          'success'
        );
      } else {
        onToast?.(res.message || 'FIFO match failed', 'error');
      }
    } catch (err) {
      onToast?.('FIFO match failed: ' + err.message, 'error');
    }
    setRunning(false);
  };

  const inputStyle = {
    padding: '6px 10px', borderRadius: 6, border: '1px solid var(--border)',
    backgroundColor: 'var(--input-bg)', color: 'var(--text)', fontSize: 12, width: '100%',
  };

  return (
    <div style={{
      position: 'fixed', top: 0, left: 0, right: 0, bottom: 0,
      backgroundColor: 'rgba(0,0,0,0.5)', zIndex: 1000,
      display: 'flex', alignItems: 'center', justifyContent: 'center',
    }} onClick={onClose}>
      <div style={{
        backgroundColor: 'var(--card-bg)', borderRadius: 12, padding: 24,
        width: 540, boxShadow: '0 8px 32px rgba(0,0,0,0.3)',
      }} onClick={e => e.stopPropagation()}>
        <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 12 }}>
          <div style={{ fontSize: 16, fontWeight: 600 }}>Run FIFO Match</div>
          <button onClick={onClose} style={{ border: 'none', background: 'none', cursor: 'pointer', fontSize: 18, color: 'var(--text-muted)' }}>x</button>
        </div>
        <div style={{ fontSize: 12, color: 'var(--text-muted)', marginBottom: 16 }}>
          Pulls paid Shopify orders in the date range and consumes purchase lots oldest-first.
          Only lots whose invoice date is on or before each order's placement date are eligible.
          Already-processed orders are skipped unless "Reprocess" is checked.
        </div>

        {result?.status === 'ok' ? (
          <>
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 8, marginBottom: 16 }}>
              {[
                { label: 'Orders fetched', value: result.orders_total, color: '#6366f1' },
                { label: 'Orders processed', value: result.orders_processed, color: '#22c55e' },
                { label: 'Skipped (existing)', value: result.orders_skipped, color: '#94a3b8' },
                { label: 'Lot matches', value: result.journal_rows, color: '#22c55e' },
                { label: 'Unmatched lines', value: result.unmatched_rows, color: '#ef4444' },
                { label: 'Vendor-sale units', value: result.vendor_sale_units, color: '#a855f7' },
              ].map((c, i) => (
                <div key={i} style={{ padding: 10, borderRadius: 6, borderLeft: '4px solid ' + c.color,
                  border: '1px solid var(--border)', backgroundColor: 'var(--card-bg)' }}>
                  <div style={{ fontSize: 10, color: 'var(--text-muted)' }}>{c.label}</div>
                  <div style={{ fontSize: 18, fontWeight: 700 }}>{c.value}</div>
                </div>
              ))}
            </div>
            <div style={{ padding: 12, borderRadius: 6, backgroundColor: 'var(--hover-bg)', marginBottom: 16, fontSize: 12 }}>
              Matched revenue {fmtFull(result.matched_revenue)} -- COGS {fmtFull(result.matched_cogs)} -- Gross profit {fmtFull(result.gross_profit)}
              {result.unmatched_revenue > 0 && (
                <div style={{ marginTop: 4, color: '#ef4444' }}>
                  Unmatched revenue (no lot at order time): {fmtFull(result.unmatched_revenue)}
                </div>
              )}
            </div>
            <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
              <button onClick={() => { setResult(null); onDone(); }} style={{
                padding: '8px 16px', borderRadius: 6, border: 'none', cursor: 'pointer',
                backgroundColor: '#6366f1', color: '#fff', fontWeight: 600, fontSize: 13,
              }}>Done</button>
            </div>
          </>
        ) : (
          <>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8, marginBottom: 12 }}>
              <div>
                <div style={{ fontSize: 11, color: 'var(--text-muted)', marginBottom: 4 }}>From *</div>
                <input type="date" value={sinceDate} onChange={e => setSinceDate(e.target.value)} style={inputStyle} />
              </div>
              <div>
                <div style={{ fontSize: 11, color: 'var(--text-muted)', marginBottom: 4 }}>To</div>
                <input type="date" value={untilDate} onChange={e => setUntilDate(e.target.value)} style={inputStyle} />
              </div>
            </div>
            <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 12, marginBottom: 16, cursor: 'pointer' }}
              title="If checked, orders already in the journal are deleted and re-matched (lot quantities are restored first).">
              <input type="checkbox" checked={reprocess} onChange={e => setReprocess(e.target.checked)} />
              Reprocess orders that are already in the journal
            </label>
            <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
              <button onClick={onClose} style={{
                padding: '8px 16px', borderRadius: 6, border: '1px solid var(--border)',
                cursor: 'pointer', backgroundColor: 'transparent', color: 'var(--text)', fontSize: 13,
              }}>Cancel</button>
              <button onClick={handleRun} disabled={running || !sinceDate} style={{
                padding: '8px 20px', borderRadius: 6, border: 'none', cursor: 'pointer',
                backgroundColor: '#22c55e', color: '#fff', fontWeight: 600, fontSize: 13,
                opacity: (running || !sinceDate) ? 0.5 : 1,
              }}>
                {running ? 'Running...' : 'Run FIFO Match'}
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  );
}


// --- ORDER DETAIL ROW (line breakdown when an order is expanded) ---
function OrderDetailRow({ orderNumber, onToast }) {
  const [detail, setDetail] = useState(null);
  const [loading, setLoading] = useState(true);
  useEffect(() => {
    let active = true;
    api.fetchFifoOrderDetail(orderNumber).then(d => {
      if (active) { setDetail(d); setLoading(false); }
    }).catch(err => {
      if (active) { onToast?.('Failed to load order: ' + err.message, 'error'); setLoading(false); }
    });
    return () => { active = false; };
  }, [orderNumber]);

  if (loading) return <div style={{ padding: 12, fontSize: 12, color: 'var(--text-muted)' }}>Loading lines...</div>;
  if (!detail) return null;

  return (
    <div style={{ padding: '0 24px 16px', backgroundColor: 'var(--hover-bg)' }}>
      {detail.matched_lines.length > 0 && (
        <>
          <div style={{ fontSize: 11, fontWeight: 600, color: 'var(--text-muted)', padding: '8px 0 4px' }}>
            Matched lines ({detail.matched_lines.length})
          </div>
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 11 }}>
            <thead>
              <tr>
                {['SKU', 'Qty', 'Sale price', 'Unit cost', 'Revenue', 'COGS', 'Margin %', 'Lot invoice', 'Sale lot?'].map(h => (
                  <th key={h} style={{ padding: '4px 8px', borderBottom: '1px solid var(--border)',
                    textAlign: ['Qty', 'Sale price', 'Unit cost', 'Revenue', 'COGS', 'Margin %'].includes(h) ? 'right' : 'left',
                    fontSize: 10, color: 'var(--text-muted)' }}>{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {detail.matched_lines.map(line => {
                const margin = line.total_revenue > 0
                  ? ((line.total_revenue - line.total_cogs) / line.total_revenue * 100)
                  : 0;
                return (
                  <tr key={line.id}>
                    <td style={{ padding: '3px 8px', fontFamily: 'monospace' }}>{line.sku}</td>
                    <td style={{ padding: '3px 8px', textAlign: 'right' }}>{line.quantity_sold}</td>
                    <td style={{ padding: '3px 8px', textAlign: 'right' }}>{fmtFull(line.sale_price)}</td>
                    <td style={{ padding: '3px 8px', textAlign: 'right' }}>
                      {fmtFull(line.unit_cost_cad)}
                      {line.regular_unit_cost_cad && line.regular_unit_cost_cad !== line.unit_cost_cad && (
                        <span style={{ marginLeft: 4, fontSize: 9, color: '#94a3b8', textDecoration: 'line-through' }}>
                          {fmtFull(line.regular_unit_cost_cad)}
                        </span>
                      )}
                    </td>
                    <td style={{ padding: '3px 8px', textAlign: 'right', fontWeight: 600 }}>{fmtFull(line.total_revenue)}</td>
                    <td style={{ padding: '3px 8px', textAlign: 'right' }}>{fmtFull(line.total_cogs)}</td>
                    <td style={{ padding: '3px 8px', textAlign: 'right', fontWeight: 600,
                      color: margin >= 20 ? '#22c55e' : margin >= 10 ? '#f97316' : '#ef4444' }}>
                      {margin.toFixed(1)}%
                    </td>
                    <td style={{ padding: '3px 8px', fontFamily: 'monospace', fontSize: 10, color: 'var(--text-muted)' }}>
                      {line.vendor || ''} {line.invoice_number ? '#' + line.invoice_number : ''} {line.invoice_date || ''}
                    </td>
                    <td style={{ padding: '3px 8px' }}>
                      {line.is_vendor_sale_cost && (
                        <span style={{ padding: '2px 6px', borderRadius: 4, fontSize: 9, fontWeight: 600,
                          backgroundColor: '#f3e8ff', color: '#a855f7' }}>SALE</span>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </>
      )}
      {detail.unmatched_lines.length > 0 && (
        <>
          <div style={{ fontSize: 11, fontWeight: 600, color: '#ef4444', padding: '12px 0 4px' }}>
            Unmatched lines ({detail.unmatched_lines.length}) -- no purchase lot covered these at order time
          </div>
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 11 }}>
            <thead>
              <tr>
                {['SKU', 'Vendor', 'Qty', 'Sale price', 'Revenue', 'Reason'].map(h => (
                  <th key={h} style={{ padding: '4px 8px', borderBottom: '1px solid var(--border)',
                    textAlign: ['Qty', 'Sale price', 'Revenue'].includes(h) ? 'right' : 'left',
                    fontSize: 10, color: 'var(--text-muted)' }}>{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {detail.unmatched_lines.map(line => (
                <tr key={line.id}>
                  <td style={{ padding: '3px 8px', fontFamily: 'monospace' }}>{line.sku}</td>
                  <td style={{ padding: '3px 8px', fontSize: 10, color: 'var(--text-muted)' }}>{line.vendor || ''}</td>
                  <td style={{ padding: '3px 8px', textAlign: 'right' }}>{line.quantity}</td>
                  <td style={{ padding: '3px 8px', textAlign: 'right' }}>{fmtFull(line.sale_price)}</td>
                  <td style={{ padding: '3px 8px', textAlign: 'right', fontWeight: 600 }}>{fmtFull(line.total_revenue)}</td>
                  <td style={{ padding: '3px 8px', fontSize: 10, color: 'var(--text-muted)' }}>{line.reason}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}
    </div>
  );
}


// --- MAIN COGS PAGE ---

export default function CogsPage({ onToast }) {
  const [activeTab, setActiveTab] = useState('inventory');
  const [invoices, setInvoices] = useState([]);
  const [summary, setSummary] = useState(null);
  const [loading, setLoading] = useState(true);
  const [showUpload, setShowUpload] = useState(false);
  const [showFifoRun, setShowFifoRun] = useState(false);
  const [expandedInvoice, setExpandedInvoice] = useState(null);
  const [lots, setLots] = useState([]);

  // Orders P&L
  const [orders, setOrders] = useState([]);
  const [ordersTotal, setOrdersTotal] = useState(0);
  const [ordersLoading, setOrdersLoading] = useState(false);
  const [expandedOrder, setExpandedOrder] = useState(null);
  const todayIso = new Date().toISOString().split('T')[0];
  const ninetyDaysAgoIso = new Date(Date.now() - 90 * 86400 * 1000).toISOString().split('T')[0];
  const [ordersStart, setOrdersStart] = useState(ninetyDaysAgoIso);
  const [ordersEnd, setOrdersEnd] = useState(todayIso);

  const loadOrders = async () => {
    setOrdersLoading(true);
    try {
      const res = await api.fetchFifoOrders({
        startDate: ordersStart,
        endDate: ordersEnd,
        limit: 200,
      });
      setOrders(res.orders || []);
      setOrdersTotal(res.total || 0);
    } catch (err) {
      onToast?.('Failed to load orders: ' + err.message, 'error');
    }
    setOrdersLoading(false);
  };

  const loadData = async () => {
    setLoading(true);
    try {
      const [inv, summ] = await Promise.all([
        api.fetchCogsInvoices(),
        api.fetchCogsSummary(),
      ]);
      setInvoices(inv);
      setSummary(summ);
    } catch (err) {
      onToast?.('Failed to load COGS data: ' + err.message, 'error');
    }
    setLoading(false);
  };

  useEffect(() => { loadData(); loadOrders(); }, []);
  useEffect(() => { loadOrders(); }, [ordersStart, ordersEnd]);

  const handleExpandInvoice = async (invoiceId) => {
    if (expandedInvoice === invoiceId) { setExpandedInvoice(null); return; }
    try {
      const data = await api.fetchInvoiceLots(invoiceId);
      setLots(data);
      setExpandedInvoice(invoiceId);
    } catch (err) { onToast?.('Failed to load lots: ' + err.message, 'error'); }
  };

  const handleDelete = async (invoiceId) => {
    if (!confirm('Delete this invoice and all its purchase lots?')) return;
    try {
      const res = await api.deleteInvoice(invoiceId);
      if (res.status === 'ok') { onToast?.('Invoice deleted', 'success'); loadData(); }
      else { onToast?.(res.message, 'error'); }
    } catch (err) { onToast?.('Delete failed: ' + err.message, 'error'); }
  };

  if (loading) return <div style={{ padding: 40, textAlign: 'center', color: 'var(--text-muted)' }}>Loading COGS data...</div>;

  const s = summary?.summary || {};
  const ls = summary?.lot_stats || {};

  return (
    <div style={{ padding: 24, maxWidth: 1500 }}>
      <div style={{ marginBottom: 16 }}>
        <h1 style={{ fontSize: 20, fontWeight: 700, margin: 0 }}>COGS Tracker</h1>
        <div style={{ fontSize: 12, color: 'var(--text-muted)', marginTop: 4 }}>
          Inventory invoices and FIFO matching, plus operating expenses for fully-loaded margin.
        </div>
      </div>

      {/* Top-level tabs */}
      <div style={{ display: 'flex', gap: 2, backgroundColor: '#f0f1f3', borderRadius: 6, padding: 2, marginBottom: 16, width: 'fit-content' }}>
        {[
          ['inventory', '📦 Inventory Invoices'],
          ['pending', '📥 Pending Invoices'],
          ['expenses', '💼 Operating Expenses'],
        ].map(([v, l]) => (
          <button key={v} onClick={() => setActiveTab(v)}
            style={{ padding: '6px 16px', borderRadius: 4, border: 'none', fontSize: 12, fontWeight: 600, cursor: 'pointer',
              backgroundColor: activeTab === v ? '#fff' : 'transparent',
              color: activeTab === v ? '#6366f1' : 'var(--text-muted)',
              boxShadow: activeTab === v ? '0 1px 3px rgba(0,0,0,0.1)' : 'none' }}>{l}</button>
        ))}
      </div>

      {activeTab === 'expenses' ? (
        <ExpensesTab onToast={onToast} />
      ) : activeTab === 'pending' ? (
        <PendingInvoicesTab onToast={onToast} />
      ) : (
      <>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 20 }}>
        <div style={{ fontSize: 12, color: 'var(--text-muted)' }}>
          FIFO inventory costing with vendor SKU matching
          {summary?.mapping_count > 0 && ' | ' + summary.mapping_count + ' saved SKU mappings'}
        </div>
        <div style={{ display: 'flex', gap: 8 }}>
          <button onClick={() => setShowFifoRun(true)} style={{
            padding: '8px 16px', borderRadius: 6, border: '1px solid var(--border)', cursor: 'pointer',
            backgroundColor: 'transparent', color: 'var(--text)', fontWeight: 600, fontSize: 13,
          }}>Run FIFO Match</button>
          <button onClick={() => setShowUpload(true)} style={{
            padding: '8px 16px', borderRadius: 6, border: 'none', cursor: 'pointer',
            backgroundColor: '#6366f1', color: '#fff', fontWeight: 600, fontSize: 13,
          }}>Upload Invoice</button>
        </div>
      </div>

      {/* Summary Cards */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(5, 1fr)', gap: 12, marginBottom: 24 }}>
        {[
          { label: 'Invoices', value: summary?.invoice_count || 0, color: '#6366f1', isCurrency: false },
          { label: 'Open Lots Value', value: ls.open_inventory_value, color: '#f97316', isCurrency: true },
          { label: 'Total Revenue (FIFO)', value: s.total_revenue, color: '#22c55e', isCurrency: true },
          { label: 'Total COGS (FIFO)', value: s.total_cogs, color: '#ef4444', isCurrency: true },
          { label: 'Gross Margin', value: s.gross_margin_pct ? s.gross_margin_pct + '%' : '--', color: '#8b5cf6', isCurrency: false },
        ].map((c, i) => (
          <div key={i} style={{ padding: 16, borderRadius: 8, border: '1px solid var(--border)',
            backgroundColor: 'var(--card-bg)', borderLeft: '4px solid ' + c.color }}>
            <div style={{ fontSize: 11, color: 'var(--text-muted)', marginBottom: 4 }}>{c.label}</div>
            <div style={{ fontSize: 22, fontWeight: 700 }}>{c.isCurrency ? fmt(c.value) : c.value}</div>
          </div>
        ))}
      </div>

      {/* Monthly Breakdown */}
      {summary?.monthly?.length > 0 && (
        <div style={{ backgroundColor: 'var(--card-bg)', border: '1px solid var(--border)', borderRadius: 8, overflow: 'hidden', marginBottom: 24 }}>
          <div style={{ padding: '12px 16px', borderBottom: '1px solid var(--border)', fontSize: 14, fontWeight: 600 }}>Monthly COGS Breakdown</div>
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
            <thead>
              <tr>
                {['Month', 'Orders', 'Revenue', 'COGS', 'Gross Profit', 'Margin %', 'Units'].map(h => (
                  <th key={h} style={{ padding: '8px 12px', fontSize: 11, fontWeight: 600, color: 'var(--text-muted)',
                    textAlign: h === 'Month' ? 'left' : 'right', borderBottom: '2px solid var(--border)' }}>{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {summary.monthly.map((m, i) => (
                <tr key={m.month} style={{ backgroundColor: i % 2 === 0 ? 'transparent' : 'var(--row-alt)' }}>
                  <td style={{ padding: '6px 12px', fontWeight: 600 }}>{m.month}</td>
                  <td style={{ padding: '6px 12px', textAlign: 'right' }}>{m.orders}</td>
                  <td style={{ padding: '6px 12px', textAlign: 'right' }}>{fmt(m.revenue)}</td>
                  <td style={{ padding: '6px 12px', textAlign: 'right' }}>{fmt(m.cogs)}</td>
                  <td style={{ padding: '6px 12px', textAlign: 'right', color: m.profit >= 0 ? '#22c55e' : '#ef4444', fontWeight: 600 }}>{fmt(m.profit)}</td>
                  <td style={{ padding: '6px 12px', textAlign: 'right', fontWeight: 600,
                    color: m.margin_pct >= 20 ? '#22c55e' : m.margin_pct >= 10 ? '#f97316' : '#ef4444' }}>{m.margin_pct}%</td>
                  <td style={{ padding: '6px 12px', textAlign: 'right' }}>{m.units}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* Per-Order P&L */}
      <div style={{ backgroundColor: 'var(--card-bg)', border: '1px solid var(--border)', borderRadius: 8, overflow: 'hidden', marginBottom: 24 }}>
        <div style={{ padding: '12px 16px', borderBottom: '1px solid var(--border)', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <div style={{ fontSize: 14, fontWeight: 600 }}>Per-Order P&L ({ordersTotal})</div>
          <div style={{ display: 'flex', gap: 8, alignItems: 'center', fontSize: 12 }}>
            <span style={{ color: 'var(--text-muted)' }}>From</span>
            <input type="date" value={ordersStart} onChange={e => setOrdersStart(e.target.value)} style={{
              padding: '4px 8px', borderRadius: 4, border: '1px solid var(--border)',
              backgroundColor: 'var(--input-bg)', color: 'var(--text)', fontSize: 12,
            }} />
            <span style={{ color: 'var(--text-muted)' }}>to</span>
            <input type="date" value={ordersEnd} onChange={e => setOrdersEnd(e.target.value)} style={{
              padding: '4px 8px', borderRadius: 4, border: '1px solid var(--border)',
              backgroundColor: 'var(--input-bg)', color: 'var(--text)', fontSize: 12,
            }} />
          </div>
        </div>
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
          <thead>
            <tr>
              {['Order', 'Date', 'Units', 'Sale lots', 'Revenue', 'COGS', 'Gross profit', 'Margin %', 'Unmatched'].map(h => (
                <th key={h} style={{ padding: '8px 12px', fontSize: 11, fontWeight: 600, color: 'var(--text-muted)',
                  textAlign: ['Order', 'Date'].includes(h) ? 'left' : 'right',
                  borderBottom: '2px solid var(--border)' }}>{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {ordersLoading && (
              <tr><td colSpan={9} style={{ padding: 16, textAlign: 'center', color: 'var(--text-muted)' }}>Loading...</td></tr>
            )}
            {!ordersLoading && orders.length === 0 && (
              <tr><td colSpan={9} style={{ padding: 24, textAlign: 'center', color: 'var(--text-muted)' }}>
                No matched orders in this date range. Click "Run FIFO Match" to pull and match Shopify orders.
              </td></tr>
            )}
            {orders.map((o, i) => (
              <Fragment key={o.order_number}>
                <tr style={{ cursor: 'pointer',
                  backgroundColor: i % 2 === 0 ? 'transparent' : 'var(--row-alt)' }}
                  onClick={() => setExpandedOrder(expandedOrder === o.order_number ? null : o.order_number)}>
                  <td style={{ padding: '6px 12px', fontFamily: 'monospace', fontWeight: 600 }}>{o.order_number}</td>
                  <td style={{ padding: '6px 12px' }}>{o.order_date}</td>
                  <td style={{ padding: '6px 12px', textAlign: 'right' }}>{o.units}</td>
                  <td style={{ padding: '6px 12px', textAlign: 'right' }}>
                    {o.vendor_sale_units > 0 ? (
                      <span style={{ padding: '2px 6px', borderRadius: 4, fontSize: 10, fontWeight: 600,
                        backgroundColor: '#f3e8ff', color: '#a855f7' }}>
                        {o.vendor_sale_units} ({o.vendor_sale_share_pct}%)
                      </span>
                    ) : '—'}
                  </td>
                  <td style={{ padding: '6px 12px', textAlign: 'right', fontWeight: 600 }}>{fmtFull(o.revenue)}</td>
                  <td style={{ padding: '6px 12px', textAlign: 'right' }}>{fmtFull(o.cogs)}</td>
                  <td style={{ padding: '6px 12px', textAlign: 'right', fontWeight: 600,
                    color: o.gross_profit >= 0 ? '#22c55e' : '#ef4444' }}>{fmtFull(o.gross_profit)}</td>
                  <td style={{ padding: '6px 12px', textAlign: 'right', fontWeight: 600,
                    color: o.margin_pct >= 20 ? '#22c55e' : o.margin_pct >= 10 ? '#f97316' : '#ef4444' }}>
                    {o.margin_pct}%
                  </td>
                  <td style={{ padding: '6px 12px', textAlign: 'right',
                    color: o.unmatched_units > 0 ? '#ef4444' : 'var(--text-muted)' }}>
                    {o.unmatched_units > 0 ? o.unmatched_units + ' u / ' + fmtFull(o.unmatched_revenue) : '—'}
                  </td>
                </tr>
                {expandedOrder === o.order_number && (
                  <tr>
                    <td colSpan={9} style={{ padding: 0 }}>
                      <OrderDetailRow orderNumber={o.order_number} onToast={onToast} />
                    </td>
                  </tr>
                )}
              </Fragment>
            ))}
          </tbody>
        </table>
      </div>

      {/* Invoice List */}
      <div style={{ backgroundColor: 'var(--card-bg)', border: '1px solid var(--border)', borderRadius: 8, overflow: 'hidden' }}>
        <div style={{ padding: '12px 16px', borderBottom: '1px solid var(--border)', fontSize: 14, fontWeight: 600 }}>
          Purchase Invoices ({invoices.length})
        </div>
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
          <thead>
            <tr>
              {['Date', 'Vendor', 'Invoice #', 'Currency', 'FX Rate', 'Items', 'Total (CAD)', ''].map(h => (
                <th key={h} style={{ padding: '8px 12px', fontSize: 11, fontWeight: 600, color: 'var(--text-muted)',
                  textAlign: h === 'Date' || h === 'Vendor' || h === 'Invoice #' || h === 'Currency' ? 'left' : 'right',
                  borderBottom: '2px solid var(--border)' }}>{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {invoices.length === 0 && (
              <tr><td colSpan={8} style={{ padding: 24, textAlign: 'center', color: 'var(--text-muted)' }}>
                No invoices uploaded yet. Click "Upload Invoice" to get started.
              </td></tr>
            )}
            {invoices.map((inv, i) => (
              <tr key={inv.id} style={{ cursor: 'pointer', backgroundColor: i % 2 === 0 ? 'transparent' : 'var(--row-alt)' }}
                onClick={() => handleExpandInvoice(inv.id)}>
                <td style={{ padding: '6px 12px' }}>{inv.invoice_date}</td>
                <td style={{ padding: '6px 12px' }}>{inv.vendor}</td>
                <td style={{ padding: '6px 12px', fontFamily: 'monospace', fontSize: 11 }}>{inv.invoice_number}</td>
                <td style={{ padding: '6px 12px' }}>{inv.currency}</td>
                <td style={{ padding: '6px 12px', textAlign: 'right' }}>{inv.fx_rate.toFixed(4)}</td>
                <td style={{ padding: '6px 12px', textAlign: 'right' }}>{inv.total_items}</td>
                <td style={{ padding: '6px 12px', textAlign: 'right', fontWeight: 600 }}>{fmtFull(inv.total_cost_cad)}</td>
                <td style={{ padding: '6px 12px', textAlign: 'right' }}>
                  <button onClick={(e) => { e.stopPropagation(); handleDelete(inv.id); }}
                    style={{ border: 'none', background: 'none', cursor: 'pointer', fontSize: 12, color: '#ef4444' }}>x</button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {expandedInvoice && (
          <div style={{ padding: '0 24px 16px', backgroundColor: 'var(--hover-bg)' }}>
            <div style={{ fontSize: 11, fontWeight: 600, color: 'var(--text-muted)', padding: '8px 0 4px' }}>
              Line Items ({lots.length})
            </div>
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 11 }}>
              <thead>
                <tr>
                  {['SKU', 'Qty Purchased', 'Qty Remaining', 'Unit Cost (Foreign)', 'Unit Cost (CAD)', 'Sale lot?'].map(h => (
                    <th key={h} style={{ padding: '4px 8px', borderBottom: '1px solid var(--border)', textAlign: 'left', fontSize: 10 }}>{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {lots.map(lot => (
                  <tr key={lot.id}>
                    <td style={{ padding: '3px 8px', fontFamily: 'monospace' }}>{lot.sku}</td>
                    <td style={{ padding: '3px 8px' }}>{lot.quantity_purchased}</td>
                    <td style={{ padding: '3px 8px', fontWeight: lot.quantity_remaining > 0 ? 600 : 400, color: lot.quantity_remaining === 0 ? 'var(--text-muted)' : 'var(--text)' }}>{lot.quantity_remaining}</td>
                    <td style={{ padding: '3px 8px' }}>{fmtFull(lot.unit_cost_foreign)}</td>
                    <td style={{ padding: '3px 8px', fontWeight: 600 }}>
                      {fmtFull(lot.unit_cost_cad)}
                      {lot.regular_unit_cost_cad && lot.regular_unit_cost_cad !== lot.unit_cost_cad && (
                        <span style={{ marginLeft: 6, fontSize: 9, color: '#94a3b8', textDecoration: 'line-through' }}>
                          {fmtFull(lot.regular_unit_cost_cad)}
                        </span>
                      )}
                    </td>
                    <td style={{ padding: '3px 8px' }}>
                      {lot.is_vendor_sale && (
                        <span style={{ padding: '2px 6px', borderRadius: 4, fontSize: 9, fontWeight: 600,
                          backgroundColor: '#f3e8ff', color: '#a855f7' }}>SALE</span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {showUpload && (
        <InvoiceUploadModal
          onDone={() => { setShowUpload(false); loadData(); }}
          onClose={() => setShowUpload(false)}
          onToast={onToast}
        />
      )}
      {showFifoRun && (
        <FifoRunModal
          onDone={() => { setShowFifoRun(false); loadData(); loadOrders(); }}
          onClose={() => setShowFifoRun(false)}
          onToast={onToast}
        />
      )}
      </>
      )}
    </div>
  );
}
