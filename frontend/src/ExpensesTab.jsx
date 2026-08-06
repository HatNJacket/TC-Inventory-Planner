import { useState, useEffect } from 'react';
import * as api from './api';

const fmt = (n) => new Intl.NumberFormat('en-CA', { style: 'currency', currency: 'CAD', minimumFractionDigits: 0, maximumFractionDigits: 0 }).format(n || 0);
const fmtFull = (n) => new Intl.NumberFormat('en-CA', { style: 'currency', currency: 'CAD', minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(n || 0);
const todayIso = () => new Date().toISOString().split('T')[0];
const daysAgoIso = (n) => new Date(Date.now() - n * 86400 * 1000).toISOString().split('T')[0];

const SUPPORTED_CURRENCIES = ['CAD', 'USD', 'EUR'];

const inputStyle = {
  padding: '6px 10px', borderRadius: 6, border: '1px solid var(--border)',
  backgroundColor: 'var(--input-bg)', color: 'var(--text)', fontSize: 12, width: '100%',
};


// --- ONE-OFF EXPENSE FORM ---
function ExpenseFormModal({ initial, onDone, onClose, onToast, categories }) {
  const isEdit = !!initial?.id;
  const [expenseDate, setExpenseDate] = useState(initial?.expense_date || todayIso());
  const [category, setCategory] = useState(initial?.category || 'payroll');
  const [currency, setCurrency] = useState(initial?.currency || 'CAD');
  const [amount, setAmount] = useState(
    initial?.amount_foreign != null ? initial.amount_foreign : (initial?.amount_cad || '')
  );
  const [fxRate, setFxRate] = useState(initial?.fx_rate || 1.0);
  const [fxLoading, setFxLoading] = useState(false);
  const [fxError, setFxError] = useState(null);
  const [description, setDescription] = useState(initial?.description || '');
  const [vendor, setVendor] = useState(initial?.vendor || '');
  const [periodStart, setPeriodStart] = useState(initial?.period_start || '');
  const [periodEnd, setPeriodEnd] = useState(initial?.period_end || '');
  const [notes, setNotes] = useState(initial?.notes || '');
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    let active = true;
    setFxError(null);
    if (currency === 'CAD') { setFxRate(1.0); return; }
    setFxLoading(true);
    api.fetchFxRate(currency + 'CAD').then(res => {
      if (!active) return;
      const rate = res?.effective_rate || 0;
      if (rate > 0) setFxRate(rate);
      else setFxError('No ' + currency + 'CAD rate stored. Set it on the Vendors page.');
      setFxLoading(false);
    }).catch(err => {
      if (active) { setFxError('FX lookup failed: ' + err.message); setFxLoading(false); }
    });
    return () => { active = false; };
  }, [currency]);

  const cadEquivalent = Number(amount || 0) * Number(fxRate || 0);

  const handleSave = async () => {
    if (!expenseDate || !category || !amount) {
      onToast?.('Date, category, and amount are required', 'error'); return;
    }
    if (currency !== 'CAD' && (!fxRate || fxRate <= 0)) {
      onToast?.('Cannot save: no FX rate for ' + currency, 'error'); return;
    }
    setSaving(true);
    try {
      const body = {
        expense_date: expenseDate, category,
        amount_foreign: Number(amount), currency,
        description: description || null, vendor: vendor || null,
        period_start: periodStart || null, period_end: periodEnd || null,
        notes: notes || null,
      };
      const res = isEdit
        ? await api.updateExpense(initial.id, body)
        : await api.createExpense(body);
      if (res.status === 'ok') {
        onToast?.(isEdit ? 'Expense updated' : 'Expense added', 'success');
        onDone();
      } else {
        onToast?.(res.message || 'Save failed', 'error');
      }
    } catch (err) { onToast?.('Save failed: ' + err.message, 'error'); }
    setSaving(false);
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
        <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 16 }}>
          <div style={{ fontSize: 16, fontWeight: 600 }}>{isEdit ? 'Edit Expense' : 'Add Operating Expense'}</div>
          <button onClick={onClose} style={{ border: 'none', background: 'none', cursor: 'pointer', fontSize: 18, color: 'var(--text-muted)' }}>x</button>
        </div>

        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 80px 1fr', gap: 8, marginBottom: 8 }}>
          <div>
            <div style={{ fontSize: 11, color: 'var(--text-muted)', marginBottom: 4 }}>Date *</div>
            <input type="date" value={expenseDate} onChange={e => setExpenseDate(e.target.value)} style={inputStyle} />
          </div>
          <div>
            <div style={{ fontSize: 11, color: 'var(--text-muted)', marginBottom: 4 }}>Category *</div>
            <select value={category} onChange={e => setCategory(e.target.value)} style={inputStyle}>
              {categories.map(c => <option key={c} value={c}>{c}</option>)}
            </select>
          </div>
          <div>
            <div style={{ fontSize: 11, color: 'var(--text-muted)', marginBottom: 4 }}>Currency</div>
            <select value={currency} onChange={e => setCurrency(e.target.value)} style={inputStyle}>
              {SUPPORTED_CURRENCIES.map(c => <option key={c} value={c}>{c}</option>)}
            </select>
          </div>
          <div>
            <div style={{ fontSize: 11, color: 'var(--text-muted)', marginBottom: 4 }}>Amount ({currency}) *</div>
            <input type="number" step="0.01" value={amount} onChange={e => setAmount(e.target.value)} style={inputStyle} />
          </div>
        </div>
        {currency !== 'CAD' && (
          <div style={{ marginBottom: 12, fontSize: 11, padding: '6px 10px', borderRadius: 6,
            backgroundColor: fxError ? '#fef2f2' : 'var(--hover-bg)',
            color: fxError ? '#dc2626' : 'var(--text-muted)',
            border: fxError ? '1px solid #fecaca' : '1px solid var(--border)' }}>
            {fxLoading ? 'Looking up FX rate...' :
              fxError ? fxError :
              <>FX: 1 {currency} = {Number(fxRate).toFixed(4)} CAD · <strong style={{ color: 'var(--text)' }}>≈ {fmtFull(cadEquivalent)} CAD</strong></>
            }
          </div>
        )}

        <div style={{ display: 'grid', gridTemplateColumns: '2fr 1fr', gap: 8, marginBottom: 12 }}>
          <div>
            <div style={{ fontSize: 11, color: 'var(--text-muted)', marginBottom: 4 }}>Description</div>
            <input value={description} onChange={e => setDescription(e.target.value)} placeholder="e.g. April payroll" style={inputStyle} />
          </div>
          <div>
            <div style={{ fontSize: 11, color: 'var(--text-muted)', marginBottom: 4 }}>Vendor</div>
            <input value={vendor} onChange={e => setVendor(e.target.value)} placeholder="(optional)" style={inputStyle} />
          </div>
        </div>

        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8, marginBottom: 12 }}>
          <div>
            <div style={{ fontSize: 11, color: 'var(--text-muted)', marginBottom: 4 }}>Period start (for recurring)</div>
            <input type="date" value={periodStart} onChange={e => setPeriodStart(e.target.value)} style={inputStyle} />
          </div>
          <div>
            <div style={{ fontSize: 11, color: 'var(--text-muted)', marginBottom: 4 }}>Period end</div>
            <input type="date" value={periodEnd} onChange={e => setPeriodEnd(e.target.value)} style={inputStyle} />
          </div>
        </div>
        <div style={{ fontSize: 11, color: 'var(--text-muted)', marginBottom: 12 }}>
          Leave period blank for one-time costs. Set both for monthly lease/payroll so the allocator can split overlap.
        </div>

        <div style={{ marginBottom: 16 }}>
          <div style={{ fontSize: 11, color: 'var(--text-muted)', marginBottom: 4 }}>Notes</div>
          <textarea value={notes} onChange={e => setNotes(e.target.value)} rows={2} style={{ ...inputStyle, fontFamily: 'inherit' }} />
        </div>

        <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
          <button onClick={onClose} style={{
            padding: '8px 16px', borderRadius: 6, border: '1px solid var(--border)',
            cursor: 'pointer', backgroundColor: 'transparent', color: 'var(--text)', fontSize: 13,
          }}>Cancel</button>
          <button onClick={handleSave} disabled={saving} style={{
            padding: '8px 20px', borderRadius: 6, border: 'none', cursor: 'pointer',
            backgroundColor: '#22c55e', color: '#fff', fontWeight: 600, fontSize: 13,
            opacity: saving ? 0.5 : 1,
          }}>{saving ? 'Saving...' : isEdit ? 'Update' : 'Add'}</button>
        </div>
      </div>
    </div>
  );
}


// --- BULK UPLOAD MODAL ---
function ExpenseUploadModal({ onDone, onClose, onToast }) {
  const [file, setFile] = useState(null);
  const [columns, setColumns] = useState([]);
  const [dateCol, setDateCol] = useState('');
  const [categoryCol, setCategoryCol] = useState('');
  const [amountCol, setAmountCol] = useState('');
  const [descCol, setDescCol] = useState('');
  const [vendorCol, setVendorCol] = useState('');
  const [periodStartCol, setPeriodStartCol] = useState('');
  const [periodEndCol, setPeriodEndCol] = useState('');
  const [currencyCol, setCurrencyCol] = useState('');
  const [defaultCurrency, setDefaultCurrency] = useState('CAD');
  const [uploading, setUploading] = useState(false);
  const [result, setResult] = useState(null);

  const handleFile = async (e) => {
    const f = e.target.files[0];
    if (!f) return;
    setFile(f);
    try {
      const res = await api.detectPricelistColumns(f);
      const cols = res.columns || [];
      setColumns(cols);
      cols.forEach(c => {
        const cl = c.toLowerCase();
        if (!dateCol && (cl.includes('date') || cl === 'when')) setDateCol(c);
        if (!categoryCol && (cl.includes('category') || cl.includes('type'))) setCategoryCol(c);
        if (!amountCol && (cl.includes('amount') || cl.includes('total') || cl.includes('cost'))) setAmountCol(c);
        if (!descCol && (cl.includes('desc') || cl.includes('memo') || cl.includes('note'))) setDescCol(c);
        if (!vendorCol && (cl.includes('vendor') || cl.includes('payee'))) setVendorCol(c);
        if (!periodStartCol && (cl.includes('start') || cl === 'from')) setPeriodStartCol(c);
        if (!periodEndCol && (cl.includes('end') || cl === 'to')) setPeriodEndCol(c);
        if (!currencyCol && (cl === 'currency' || cl === 'ccy' || cl === 'curr')) setCurrencyCol(c);
      });
    } catch (err) { onToast?.('Could not read file: ' + err.message, 'error'); }
  };

  const handleUpload = async () => {
    if (!file || !dateCol || !categoryCol || !amountCol) {
      onToast?.('Date, category, and amount columns are required', 'error'); return;
    }
    setUploading(true);
    try {
      const res = await api.uploadExpensesCsv(file, {
        dateColumn: dateCol, categoryColumn: categoryCol, amountColumn: amountCol,
        descriptionColumn: descCol || undefined, vendorColumn: vendorCol || undefined,
        periodStartColumn: periodStartCol || undefined, periodEndColumn: periodEndCol || undefined,
        currencyColumn: currencyCol || undefined, defaultCurrency,
      });
      setResult(res);
      if (res.status === 'ok') {
        onToast?.('Imported ' + res.inserted + ' expenses (' + fmtFull(res.total_amount_cad) + ')', 'success');
      } else {
        onToast?.(res.message || 'Upload failed', 'error');
      }
    } catch (err) { onToast?.('Upload failed: ' + err.message, 'error'); }
    setUploading(false);
  };

  const ColSelect = ({ label, value, onChange, required }) => (
    <div>
      <div style={{ fontSize: 11, color: 'var(--text-muted)', marginBottom: 4 }}>{label}{required && ' *'}</div>
      <select value={value} onChange={e => onChange(e.target.value)} style={inputStyle}>
        <option value="">-- Select --</option>
        {columns.map(c => <option key={c} value={c}>{c}</option>)}
      </select>
    </div>
  );

  return (
    <div style={{
      position: 'fixed', top: 0, left: 0, right: 0, bottom: 0,
      backgroundColor: 'rgba(0,0,0,0.5)', zIndex: 1000,
      display: 'flex', alignItems: 'center', justifyContent: 'center',
    }} onClick={onClose}>
      <div style={{
        backgroundColor: 'var(--card-bg)', borderRadius: 12, padding: 24,
        width: 720, maxHeight: '90vh', overflowY: 'auto',
        boxShadow: '0 8px 32px rgba(0,0,0,0.3)',
      }} onClick={e => e.stopPropagation()}>
        <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 16 }}>
          <div style={{ fontSize: 16, fontWeight: 600 }}>Upload Expenses (CSV / XLSX)</div>
          <button onClick={onClose} style={{ border: 'none', background: 'none', cursor: 'pointer', fontSize: 18, color: 'var(--text-muted)' }}>x</button>
        </div>

        {result?.status === 'ok' ? (
          <>
            <div style={{ padding: 16, borderRadius: 8, backgroundColor: 'var(--hover-bg)', marginBottom: 16 }}>
              <div style={{ fontSize: 14, fontWeight: 600, color: '#22c55e' }}>Imported {result.inserted} expenses ({fmtFull(result.total_amount_cad)})</div>
              {result.skipped > 0 && (
                <div style={{ fontSize: 12, color: '#ef4444', marginTop: 4 }}>
                  Skipped {result.skipped} rows. First few errors: {(result.errors || []).slice(0, 3).map(e => 'row ' + e.row + ': ' + e.reason).join('; ')}
                </div>
              )}
            </div>
            <button onClick={() => { setResult(null); onDone(); }} style={{
              padding: '8px 16px', borderRadius: 6, border: 'none', cursor: 'pointer',
              backgroundColor: '#6366f1', color: '#fff', fontWeight: 600, fontSize: 13,
            }}>Done</button>
          </>
        ) : (
          <>
            <div style={{ marginBottom: 16 }}>
              <input type="file" accept=".csv,.tsv,.txt,.xlsx,.xls" onChange={handleFile} style={{ fontSize: 13 }} />
            </div>
            {columns.length > 0 && (
              <>
                <div style={{ fontSize: 12, color: 'var(--text-muted)', marginBottom: 8 }}>
                  Detected {columns.length} columns. Map them below:
                </div>
                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr 1fr', gap: 8, marginBottom: 12 }}>
                  <ColSelect label="Date" value={dateCol} onChange={setDateCol} required />
                  <ColSelect label="Category" value={categoryCol} onChange={setCategoryCol} required />
                  <ColSelect label="Amount" value={amountCol} onChange={setAmountCol} required />
                  <ColSelect label="Description" value={descCol} onChange={setDescCol} />
                </div>
                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr 1fr', gap: 8, marginBottom: 12 }}>
                  <ColSelect label="Vendor" value={vendorCol} onChange={setVendorCol} />
                  <ColSelect label="Period start" value={periodStartCol} onChange={setPeriodStartCol} />
                  <ColSelect label="Period end" value={periodEndCol} onChange={setPeriodEndCol} />
                  <ColSelect label="Currency (optional)" value={currencyCol} onChange={setCurrencyCol} />
                </div>
                <div style={{ marginBottom: 12, padding: '8px 12px', borderRadius: 6, backgroundColor: 'var(--hover-bg)', fontSize: 11, color: 'var(--text-muted)' }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                    <span>Default currency (used for rows where the currency column is blank):</span>
                    <select value={defaultCurrency} onChange={e => setDefaultCurrency(e.target.value)} style={{
                      padding: '4px 8px', borderRadius: 4, border: '1px solid var(--border)',
                      backgroundColor: 'var(--input-bg)', color: 'var(--text)', fontSize: 12,
                    }}>
                      {SUPPORTED_CURRENCIES.map(c => <option key={c} value={c}>{c}</option>)}
                    </select>
                  </div>
                </div>
                <button onClick={handleUpload} disabled={uploading || !dateCol || !categoryCol || !amountCol} style={{
                  padding: '8px 20px', borderRadius: 6, border: 'none', cursor: 'pointer',
                  backgroundColor: '#22c55e', color: '#fff', fontWeight: 600, fontSize: 13,
                  opacity: (uploading || !dateCol || !categoryCol || !amountCol) ? 0.5 : 1,
                }}>{uploading ? 'Uploading...' : 'Upload'}</button>
              </>
            )}
          </>
        )}
      </div>
    </div>
  );
}


// --- RECURRING TEMPLATE FORM ---
function TemplateFormModal({ initial, onDone, onClose, onToast, categories }) {
  const isEdit = !!initial?.id;
  const [name, setName] = useState(initial?.name || '');
  const [category, setCategory] = useState(initial?.category || 'lease');
  const [currency, setCurrency] = useState(initial?.currency || 'CAD');
  const [amount, setAmount] = useState(initial?.amount_foreign || '');
  const [dayOfMonth, setDayOfMonth] = useState(initial?.day_of_month || 1);
  const [vendor, setVendor] = useState(initial?.vendor || '');
  const [notes, setNotes] = useState(initial?.notes || '');
  const [startDate, setStartDate] = useState(initial?.start_date || todayIso());
  const [endDate, setEndDate] = useState(initial?.end_date || '');
  const [active, setActive] = useState(initial?.active !== false);
  const [fxRate, setFxRate] = useState(initial?.fx_rate || 1.0);
  const [fxError, setFxError] = useState(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    let alive = true;
    setFxError(null);
    if (currency === 'CAD') { setFxRate(1.0); return; }
    api.fetchFxRate(currency + 'CAD').then(res => {
      if (!alive) return;
      const rate = res?.effective_rate || 0;
      if (rate > 0) setFxRate(rate);
      else setFxError('No ' + currency + 'CAD rate stored. Set it on the Vendors page.');
    }).catch(err => { if (alive) setFxError('FX lookup failed: ' + err.message); });
    return () => { alive = false; };
  }, [currency]);

  const handleSave = async () => {
    if (!name || !category || !amount || !startDate) {
      onToast?.('Name, category, amount, and start date are required', 'error'); return;
    }
    if (currency !== 'CAD' && fxError) {
      onToast?.(fxError, 'error'); return;
    }
    setSaving(true);
    try {
      const body = {
        name, category, amount_foreign: Number(amount), currency,
        day_of_month: Number(dayOfMonth), vendor: vendor || null,
        notes: notes || null, start_date: startDate,
        end_date: endDate || null, active,
      };
      const res = isEdit
        ? await api.updateRecurringTemplate(initial.id, body)
        : await api.createRecurringTemplate(body);
      if (res.status === 'ok') {
        onToast?.(isEdit ? 'Template updated — applies to future months' : 'Template added', 'success');
        onDone();
      } else {
        onToast?.(res.message || 'Save failed', 'error');
      }
    } catch (err) { onToast?.('Save failed: ' + err.message, 'error'); }
    setSaving(false);
  };

  return (
    <div style={{
      position: 'fixed', top: 0, left: 0, right: 0, bottom: 0,
      backgroundColor: 'rgba(0,0,0,0.5)', zIndex: 1000,
      display: 'flex', alignItems: 'center', justifyContent: 'center',
    }} onClick={onClose}>
      <div style={{
        backgroundColor: 'var(--card-bg)', borderRadius: 12, padding: 24,
        width: 600, maxHeight: '90vh', overflowY: 'auto',
        boxShadow: '0 8px 32px rgba(0,0,0,0.3)',
      }} onClick={e => e.stopPropagation()}>
        <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 16 }}>
          <div style={{ fontSize: 16, fontWeight: 600 }}>{isEdit ? 'Edit Recurring Template' : 'Add Recurring Template'}</div>
          <button onClick={onClose} style={{ border: 'none', background: 'none', cursor: 'pointer', fontSize: 18, color: 'var(--text-muted)' }}>x</button>
        </div>

        <div style={{ marginBottom: 12 }}>
          <div style={{ fontSize: 11, color: 'var(--text-muted)', marginBottom: 4 }}>Name *</div>
          <input value={name} onChange={e => setName(e.target.value)} placeholder="e.g. Warehouse lease" style={inputStyle} />
        </div>

        <div style={{ display: 'grid', gridTemplateColumns: '1fr 80px 1fr 80px', gap: 8, marginBottom: 8 }}>
          <div>
            <div style={{ fontSize: 11, color: 'var(--text-muted)', marginBottom: 4 }}>Category *</div>
            <select value={category} onChange={e => setCategory(e.target.value)} style={inputStyle}>
              {categories.map(c => <option key={c} value={c}>{c}</option>)}
            </select>
          </div>
          <div>
            <div style={{ fontSize: 11, color: 'var(--text-muted)', marginBottom: 4 }}>Currency</div>
            <select value={currency} onChange={e => setCurrency(e.target.value)} style={inputStyle}>
              {SUPPORTED_CURRENCIES.map(c => <option key={c} value={c}>{c}</option>)}
            </select>
          </div>
          <div>
            <div style={{ fontSize: 11, color: 'var(--text-muted)', marginBottom: 4 }}>Amount per month ({currency}) *</div>
            <input type="number" step="0.01" value={amount} onChange={e => setAmount(e.target.value)} style={inputStyle} />
          </div>
          <div>
            <div style={{ fontSize: 11, color: 'var(--text-muted)', marginBottom: 4 }}>Day of month *</div>
            <input type="number" min="1" max="31" value={dayOfMonth} onChange={e => setDayOfMonth(e.target.value)} style={inputStyle} />
          </div>
        </div>
        {currency !== 'CAD' && (
          <div style={{ marginBottom: 12, fontSize: 11, padding: '6px 10px', borderRadius: 6,
            backgroundColor: fxError ? '#fef2f2' : 'var(--hover-bg)',
            color: fxError ? '#dc2626' : 'var(--text-muted)',
            border: fxError ? '1px solid #fecaca' : '1px solid var(--border)' }}>
            {fxError ? fxError : <>FX: 1 {currency} = {Number(fxRate).toFixed(4)} CAD · ≈ {fmtFull((Number(amount) || 0) * Number(fxRate))} CAD/month</>}
          </div>
        )}
        <div style={{ fontSize: 11, color: 'var(--text-muted)', marginBottom: 12 }}>
          If day-of-month exceeds the month length (e.g. 31 in February) it lands on the last day of that month.
        </div>

        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8, marginBottom: 12 }}>
          <div>
            <div style={{ fontSize: 11, color: 'var(--text-muted)', marginBottom: 4 }}>Start date *</div>
            <input type="date" value={startDate} onChange={e => setStartDate(e.target.value)} style={inputStyle} />
          </div>
          <div>
            <div style={{ fontSize: 11, color: 'var(--text-muted)', marginBottom: 4 }}>End date (blank = open-ended)</div>
            <input type="date" value={endDate} onChange={e => setEndDate(e.target.value)} style={inputStyle} />
          </div>
        </div>

        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8, marginBottom: 12 }}>
          <div>
            <div style={{ fontSize: 11, color: 'var(--text-muted)', marginBottom: 4 }}>Vendor</div>
            <input value={vendor} onChange={e => setVendor(e.target.value)} placeholder="(optional)" style={inputStyle} />
          </div>
          <div style={{ display: 'flex', alignItems: 'flex-end' }}>
            <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 12, cursor: 'pointer' }}>
              <input type="checkbox" checked={active} onChange={e => setActive(e.target.checked)} />
              Active (generate monthly rows)
            </label>
          </div>
        </div>

        <div style={{ marginBottom: 16 }}>
          <div style={{ fontSize: 11, color: 'var(--text-muted)', marginBottom: 4 }}>Notes / comment</div>
          <textarea value={notes} onChange={e => setNotes(e.target.value)} rows={3} style={{ ...inputStyle, fontFamily: 'inherit' }}
            placeholder="Anything you want recorded with each generated row." />
        </div>

        {isEdit && (
          <div style={{ marginBottom: 16, padding: '8px 12px', borderRadius: 6, fontSize: 11,
            backgroundColor: '#fef9c3', color: '#854d0e', border: '1px solid #fde68a' }}>
            Edits apply to <strong>future months only</strong>. Already-generated rows stay as they are — edit them individually if you need to.
          </div>
        )}

        <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
          <button onClick={onClose} style={{
            padding: '8px 16px', borderRadius: 6, border: '1px solid var(--border)',
            cursor: 'pointer', backgroundColor: 'transparent', color: 'var(--text)', fontSize: 13,
          }}>Cancel</button>
          <button onClick={handleSave} disabled={saving} style={{
            padding: '8px 20px', borderRadius: 6, border: 'none', cursor: 'pointer',
            backgroundColor: '#22c55e', color: '#fff', fontWeight: 600, fontSize: 13,
            opacity: saving ? 0.5 : 1,
          }}>{saving ? 'Saving...' : isEdit ? 'Update' : 'Add'}</button>
        </div>
      </div>
    </div>
  );
}


// --- RECURRING TEMPLATES PANEL ---
function RecurringTemplatesPanel({ categories, onChanged, onToast }) {
  const [templates, setTemplates] = useState([]);
  const [loading, setLoading] = useState(true);
  const [showForm, setShowForm] = useState(false);
  const [editing, setEditing] = useState(null);
  const [showInactive, setShowInactive] = useState(false);

  const load = async () => {
    setLoading(true);
    try {
      const res = await api.fetchRecurringTemplates(showInactive);
      setTemplates(res.templates || []);
    } catch (err) { onToast?.('Failed to load templates: ' + err.message, 'error'); }
    setLoading(false);
  };

  useEffect(() => { load(); }, [showInactive]);

  const handleDelete = async (t) => {
    const cascade = confirm(
      'Delete template "' + t.name + '"?\n\n' +
      'OK = also delete every monthly row this template has already generated.\n' +
      'Cancel = keep generated rows but stop creating new ones.'
    );
    try {
      const res = await api.deleteRecurringTemplate(t.id, cascade);
      if (res.status === 'ok') {
        onToast?.('Template deleted' + (cascade ? ' (with generated rows)' : ' — generated rows kept'), 'success');
        load(); onChanged?.();
      }
    } catch (err) { onToast?.('Delete failed: ' + err.message, 'error'); }
  };

  return (
    <div style={{ backgroundColor: 'var(--card-bg)', border: '1px solid var(--border)', borderRadius: 8, overflow: 'hidden', marginBottom: 16 }}>
      <div style={{ padding: '12px 16px', borderBottom: '1px solid var(--border)', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <div>
          <div style={{ fontSize: 14, fontWeight: 600 }}>Recurring Templates ({templates.filter(t => t.active).length} active)</div>
          <div style={{ fontSize: 11, color: 'var(--text-muted)', marginTop: 2 }}>
            Predictable monthly costs (lease, payroll, software). New monthly rows materialize automatically.
          </div>
        </div>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
          <label style={{ display: 'flex', alignItems: 'center', gap: 4, fontSize: 11, color: 'var(--text-muted)', cursor: 'pointer' }}>
            <input type="checkbox" checked={showInactive} onChange={e => setShowInactive(e.target.checked)} />
            Show inactive
          </label>
          <button onClick={() => { setEditing(null); setShowForm(true); }} style={{
            padding: '6px 12px', borderRadius: 6, border: 'none', cursor: 'pointer',
            backgroundColor: '#6366f1', color: '#fff', fontWeight: 600, fontSize: 12,
          }}>Add Template</button>
        </div>
      </div>

      <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
        <thead>
          <tr>
            {['Name', 'Category', 'Day', 'Amount', 'CAD/mo', 'Vendor', 'Window', 'Active', ''].map(h => (
              <th key={h} style={{ padding: '8px 12px', fontSize: 11, fontWeight: 600, color: 'var(--text-muted)',
                textAlign: ['Day', 'Amount', 'CAD/mo'].includes(h) ? 'right' : 'left',
                borderBottom: '2px solid var(--border)' }}>{h}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {loading && <tr><td colSpan={9} style={{ padding: 16, textAlign: 'center', color: 'var(--text-muted)' }}>Loading...</td></tr>}
          {!loading && templates.length === 0 && (
            <tr><td colSpan={9} style={{ padding: 24, textAlign: 'center', color: 'var(--text-muted)' }}>
              No recurring templates yet. Add one for monthly costs like lease, payroll, or software subscriptions.
            </td></tr>
          )}
          {templates.map((t, i) => {
            const isForeign = t.currency && t.currency !== 'CAD';
            const cadPerMonth = isForeign ? null : t.amount_foreign;
            return (
              <tr key={t.id} style={{
                backgroundColor: i % 2 === 0 ? 'transparent' : 'var(--row-alt)',
                opacity: t.active ? 1 : 0.5,
              }}>
                <td style={{ padding: '6px 12px', fontWeight: 600 }}>{t.name}</td>
                <td style={{ padding: '6px 12px' }}>
                  <span style={{ fontSize: 11, padding: '2px 8px', borderRadius: 4, backgroundColor: 'var(--hover-bg)' }}>{t.category}</span>
                </td>
                <td style={{ padding: '6px 12px', textAlign: 'right' }}>{t.day_of_month}</td>
                <td style={{ padding: '6px 12px', textAlign: 'right' }}>
                  {fmtFull(t.amount_foreign)}{' '}
                  <span style={{ fontSize: 10, color: 'var(--text-muted)', fontWeight: 600 }}>{t.currency}</span>
                </td>
                <td style={{ padding: '6px 12px', textAlign: 'right', fontWeight: 600 }}>
                  {cadPerMonth != null ? fmtFull(cadPerMonth) : <span style={{ color: 'var(--text-muted)' }}>at FX</span>}
                </td>
                <td style={{ padding: '6px 12px', color: 'var(--text-muted)' }}>{t.vendor || '—'}</td>
                <td style={{ padding: '6px 12px', fontSize: 11, color: 'var(--text-muted)' }}>
                  {t.start_date}{t.end_date ? ' → ' + t.end_date : ' → open'}
                </td>
                <td style={{ padding: '6px 12px' }}>
                  {t.active ? (
                    <span style={{ padding: '2px 6px', borderRadius: 4, fontSize: 10, fontWeight: 600,
                      backgroundColor: '#dcfce7', color: '#16a34a' }}>active</span>
                  ) : (
                    <span style={{ padding: '2px 6px', borderRadius: 4, fontSize: 10, fontWeight: 600,
                      backgroundColor: 'var(--hover-bg)', color: 'var(--text-muted)' }}>paused</span>
                  )}
                </td>
                <td style={{ padding: '6px 12px', textAlign: 'right' }}>
                  <button onClick={() => { setEditing(t); setShowForm(true); }}
                    style={{ border: 'none', background: 'none', cursor: 'pointer', fontSize: 12, color: 'var(--text-muted)', marginRight: 8 }}>edit</button>
                  <button onClick={() => handleDelete(t)}
                    style={{ border: 'none', background: 'none', cursor: 'pointer', fontSize: 12, color: '#ef4444' }}>x</button>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>

      {showForm && (
        <TemplateFormModal
          initial={editing}
          categories={categories}
          onDone={() => { setShowForm(false); setEditing(null); load(); onChanged?.(); }}
          onClose={() => { setShowForm(false); setEditing(null); }}
          onToast={onToast}
        />
      )}
    </div>
  );
}


// --- ONE-OFF EXPENSES PANEL ---
function ExpensesPanel({ startDate, endDate, categories, onChanged, onToast }) {
  const [expenses, setExpenses] = useState([]);
  const [summary, setSummary] = useState(null);
  const [loading, setLoading] = useState(true);
  const [showForm, setShowForm] = useState(false);
  const [showUpload, setShowUpload] = useState(false);
  const [editing, setEditing] = useState(null);

  const load = async () => {
    setLoading(true);
    try {
      const [list, summ] = await Promise.all([
        api.fetchExpenses({ startDate, endDate, limit: 200 }),
        api.fetchExpensesSummary(startDate, endDate),
      ]);
      setExpenses(list.expenses || []);
      setSummary(summ);
    } catch (err) { onToast?.('Failed to load expenses: ' + err.message, 'error'); }
    setLoading(false);
  };

  useEffect(() => { load(); }, [startDate, endDate]);

  const handleDelete = async (id) => {
    if (!confirm('Delete this expense?')) return;
    try {
      const res = await api.deleteExpense(id);
      if (res.status === 'ok') {
        onToast?.('Expense deleted', 'success');
        load(); onChanged?.();
      }
    } catch (err) { onToast?.('Delete failed: ' + err.message, 'error'); }
  };

  return (
    <div style={{ backgroundColor: 'var(--card-bg)', border: '1px solid var(--border)', borderRadius: 8, overflow: 'hidden', marginBottom: 16 }}>
      <div style={{ padding: '12px 16px', borderBottom: '1px solid var(--border)', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <div>
          <div style={{ fontSize: 14, fontWeight: 600 }}>
            Expenses in window {summary && '— ' + fmtFull(summary.total_amount_cad) + ' total'}
          </div>
          {summary?.by_category?.length > 0 && (
            <div style={{ display: 'flex', gap: 8, marginTop: 6, flexWrap: 'wrap' }}>
              {summary.by_category.slice(0, 8).map(c => (
                <span key={c.category} style={{ fontSize: 11, padding: '2px 8px', borderRadius: 4,
                  backgroundColor: 'var(--hover-bg)', color: 'var(--text-muted)' }}>
                  {c.category}: <strong style={{ color: 'var(--text)' }}>{fmt(c.amount_cad)}</strong>
                </span>
              ))}
            </div>
          )}
        </div>
        <div style={{ display: 'flex', gap: 8 }}>
          <button onClick={() => setShowUpload(true)} style={{
            padding: '6px 12px', borderRadius: 6, border: '1px solid var(--border)',
            cursor: 'pointer', backgroundColor: 'transparent', color: 'var(--text)', fontSize: 12,
          }}>Bulk Upload</button>
          <button onClick={() => { setEditing(null); setShowForm(true); }} style={{
            padding: '6px 12px', borderRadius: 6, border: 'none', cursor: 'pointer',
            backgroundColor: '#6366f1', color: '#fff', fontWeight: 600, fontSize: 12,
          }}>Add Expense</button>
        </div>
      </div>

      <div style={{ maxHeight: 480, overflowY: 'auto' }}>
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
          <thead>
            <tr>
              {['Date', 'Category', 'Description', 'Vendor', 'Period', 'Amount', 'CAD', 'Source', ''].map(h => (
                <th key={h} style={{ padding: '8px 12px', fontSize: 11, fontWeight: 600, color: 'var(--text-muted)',
                  textAlign: ['Amount', 'CAD'].includes(h) ? 'right' : 'left',
                  borderBottom: '2px solid var(--border)', position: 'sticky', top: 0,
                  backgroundColor: 'var(--card-bg)', zIndex: 1 }}>{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {loading && <tr><td colSpan={9} style={{ padding: 16, textAlign: 'center', color: 'var(--text-muted)' }}>Loading...</td></tr>}
            {!loading && expenses.length === 0 && (
              <tr><td colSpan={9} style={{ padding: 24, textAlign: 'center', color: 'var(--text-muted)' }}>
                No expenses in this date range. Add one or set up a recurring template above.
              </td></tr>
            )}
            {expenses.map((e, i) => {
              const isForeign = e.currency && e.currency !== 'CAD';
              return (
                <tr key={e.id} style={{ backgroundColor: i % 2 === 0 ? 'transparent' : 'var(--row-alt)' }}>
                  <td style={{ padding: '6px 12px' }}>{e.expense_date}</td>
                  <td style={{ padding: '6px 12px' }}>
                    <span style={{ fontSize: 11, padding: '2px 8px', borderRadius: 4, backgroundColor: 'var(--hover-bg)' }}>{e.category}</span>
                  </td>
                  <td style={{ padding: '6px 12px' }}>{e.description || '—'}</td>
                  <td style={{ padding: '6px 12px', color: 'var(--text-muted)' }}>{e.vendor || '—'}</td>
                  <td style={{ padding: '6px 12px', fontSize: 11, color: 'var(--text-muted)' }}>
                    {e.period_start && e.period_end ? e.period_start + ' → ' + e.period_end : '—'}
                  </td>
                  <td style={{ padding: '6px 12px', textAlign: 'right' }}>
                    {fmtFull(e.amount_foreign)}{' '}
                    <span style={{ fontSize: 10, color: 'var(--text-muted)', fontWeight: 600 }}>{e.currency || 'CAD'}</span>
                    {isForeign && (
                      <div style={{ fontSize: 10, color: 'var(--text-muted)' }}>@ {Number(e.fx_rate).toFixed(4)}</div>
                    )}
                  </td>
                  <td style={{ padding: '6px 12px', textAlign: 'right', fontWeight: 600 }}>{fmtFull(e.amount_cad)}</td>
                  <td style={{ padding: '6px 12px' }}>
                    {e.template_id ? (
                      <span style={{ padding: '2px 6px', borderRadius: 4, fontSize: 10, fontWeight: 600,
                        backgroundColor: '#dbeafe', color: '#2563eb' }}>recurring</span>
                    ) : (
                      <span style={{ fontSize: 10, color: 'var(--text-muted)' }}>manual</span>
                    )}
                  </td>
                  <td style={{ padding: '6px 12px', textAlign: 'right' }}>
                    <button onClick={() => { setEditing(e); setShowForm(true); }}
                      style={{ border: 'none', background: 'none', cursor: 'pointer', fontSize: 12, color: 'var(--text-muted)', marginRight: 8 }}>edit</button>
                    <button onClick={() => handleDelete(e.id)}
                      style={{ border: 'none', background: 'none', cursor: 'pointer', fontSize: 12, color: '#ef4444' }}>x</button>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      {showForm && (
        <ExpenseFormModal
          initial={editing} categories={categories}
          onDone={() => { setShowForm(false); setEditing(null); load(); onChanged?.(); }}
          onClose={() => { setShowForm(false); setEditing(null); }}
          onToast={onToast}
        />
      )}
      {showUpload && (
        <ExpenseUploadModal
          onDone={() => { setShowUpload(false); load(); onChanged?.(); }}
          onClose={() => setShowUpload(false)}
          onToast={onToast}
        />
      )}
    </div>
  );
}


// --- MAIN TAB ---
export default function ExpensesTab({ onToast }) {
  const [startDate, setStartDate] = useState(daysAgoIso(90));
  const [endDate, setEndDate] = useState(todayIso());
  const [categories, setCategories] = useState([]);
  const [refreshKey, setRefreshKey] = useState(0);

  useEffect(() => {
    api.fetchExpenseCategories().then(c => setCategories(c.categories || []))
      .catch(err => onToast?.('Failed to load categories: ' + err.message, 'error'));
  }, []);

  const handleChange = () => setRefreshKey(k => k + 1);

  return (
    <div style={{ padding: 24, maxWidth: 1500 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 20 }}>
        <div>
          <h2 style={{ fontSize: 18, fontWeight: 700, margin: 0 }}>Operating Expenses</h2>
          <div style={{ fontSize: 12, color: 'var(--text-muted)', marginTop: 4 }}>
            Non-inventory costs (payroll, lease, utilities, …). Allocated across orders on the Sale Performance page.
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
        </div>
      </div>

      <RecurringTemplatesPanel
        key={'tpl-' + refreshKey}
        categories={categories}
        onChanged={handleChange}
        onToast={onToast}
      />

      <ExpensesPanel
        key={'exp-' + refreshKey}
        startDate={startDate} endDate={endDate}
        categories={categories}
        onChanged={handleChange}
        onToast={onToast}
      />
    </div>
  );
}
