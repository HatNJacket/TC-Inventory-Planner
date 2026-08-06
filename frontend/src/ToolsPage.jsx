import { useState, useRef } from 'react';
import * as api from './api';

export default function ToolsPage({ onToast }) {
  const [uploading, setUploading] = useState(false);
  const [results, setResults] = useState(null);
  const [fileName, setFileName] = useState('');
  const [dryRun, setDryRun] = useState(true);
  const [mode, setMode] = useState('metafields'); // 'metafields' or 'correct_skus'
  const [filterStatus, setFilterStatus] = useState('all');
  const [availableHeaders, setAvailableHeaders] = useState([]);
  const [titleCol, setTitleCol] = useState('');
  const [detectingHeaders, setDetectingHeaders] = useState(false);
  const fileRef = useRef(null);

  const handleFileSelect = async (file) => {
    setFileName(file?.name || '');
    setResults(null);
    setAvailableHeaders([]);
    setTitleCol('');
    if (!file) return;
    setDetectingHeaders(true);
    try {
      const { headers } = await api.detectFileHeaders(file);
      setAvailableHeaders(headers || []);
      // Auto-select a likely title column
      const titleGuess = (headers || []).find(h => {
        const lower = h.trim().toLowerCase();
        return lower === 'title' || lower === 'product title' || lower === 'product_title'
          || lower === 'product name' || lower === 'name' || lower === 'description'
          || lower === 'item description' || lower === 'item';
      });
      if (titleGuess) setTitleCol(titleGuess);
    } catch (err) {
      // non-fatal: user just won't see a title column
      console.warn('Header detection failed:', err.message);
    }
    setDetectingHeaders(false);
  };

  const handleUpload = async () => {
    const file = fileRef.current?.files?.[0];
    if (!file) { onToast('Please select a CSV or XLSX file', 'error'); return; }
    setUploading(true);
    setResults(null);
    try {
      let result;
      if (mode === 'correct_skus') {
        result = await api.correctSkus(file, dryRun, titleCol || null);
      } else {
        result = await api.uploadMetafields(file, dryRun, titleCol || null);
      }
      setResults({ ...result, mode });
      onToast(result.message);
      setFilterStatus('all');
    } catch (err) { onToast('Upload failed: ' + err.message, 'error'); }
    setUploading(false);
  };

  const handleApplyNow = async () => {
    const file = fileRef.current?.files?.[0];
    if (!file) { onToast('File no longer available, please re-select', 'error'); return; }
    setDryRun(false);
    setUploading(true);
    setResults(null);
    try {
      let result;
      if ((results?.mode || mode) === 'correct_skus') {
        result = await api.correctSkus(file, false, titleCol || null);
      } else {
        result = await api.uploadMetafields(file, false, titleCol || null);
      }
      setResults({ ...result, mode: results?.mode || mode });
      onToast(result.message);
      setFilterStatus('all');
    } catch (err) { onToast('Apply failed: ' + err.message, 'error'); }
    setUploading(false);
  };

  const handleModeChange = (newMode) => {
    setMode(newMode);
    setResults(null);
    setFilterStatus('all');
  };

  const isSkuMode = (results?.mode || mode) === 'correct_skus';
  const isDryRun = results?.dry_run;

  // Filter results
  const allResults = results?.results || [];
  const filtered = allResults.filter(r => {
    if (filterStatus === 'all') return true;
    if (isSkuMode) {
      if (filterStatus === 'correction') return r.needs_correction;
      if (filterStatus === 'exact') return r.match_type === 'exact';
      if (filterStatus === 'not_found') return r.match_type === 'not_found';
    } else {
      if (filterStatus === 'matched') return r.found_in_shopify;
      if (filterStatus === 'not_found') return !r.found_in_shopify;
      if (filterStatus === 'error') return r.found_in_shopify && !r.success;
    }
    return true;
  });

  // Counts
  const skuCorrectionCount = allResults.filter(r => r.needs_correction).length;
  const skuExactCount = allResults.filter(r => r.match_type === 'exact').length;
  const notFoundCount = isSkuMode
    ? allResults.filter(r => r.match_type === 'not_found').length
    : allResults.filter(r => !r.found_in_shopify).length;
  const matchedCount = allResults.filter(r => r.found_in_shopify).length;
  const updatedCount = allResults.filter(r => r.success || r.corrected).length;
  const errorCount = allResults.filter(r => r.found_in_shopify && !r.success && r.action === 'error').length;

  return (
    <div style={{ padding: '28px 32px', maxWidth: 1000, overflow: 'auto', height: '100vh' }}>
      <h1 style={{ fontSize: 22, fontWeight: 700, marginBottom: 24 }}>Tools</h1>

      <div style={{ backgroundColor: 'var(--white)', borderRadius: 10, border: '1px solid var(--border)', padding: '24px 28px' }}>
        <h3 style={{ fontSize: 16, fontWeight: 700, margin: '0 0 8px 0' }}>Upload Vendor CSV</h3>
        <p style={{ fontSize: 13, color: 'var(--text-light)', margin: '0 0 20px 0', lineHeight: 1.6 }}>
          Upload a CSV with a <strong>SKU</strong> column. Choose a mode below to either update metafields or correct mismatched SKUs.
        </p>

        {/* Mode selector */}
        <div style={{ display: 'flex', gap: 12, marginBottom: 20 }}>
          <label onClick={() => handleModeChange('metafields')} style={{
            display: 'flex', alignItems: 'center', gap: 8, padding: '10px 16px', borderRadius: 8, cursor: 'pointer',
            border: mode === 'metafields' ? '2px solid var(--green)' : '2px solid var(--border)',
            backgroundColor: mode === 'metafields' ? '#e8f5f0' : 'transparent',
          }}>
            <input type="radio" checked={mode === 'metafields'} onChange={() => handleModeChange('metafields')}
              style={{ accentColor: 'var(--green)' }} />
            <div>
              <div style={{ fontSize: 13, fontWeight: 600 }}>Update Metafields</div>
              <div style={{ fontSize: 11, color: 'var(--text-muted)' }}>Apply Cost USD / System Code to variants</div>
            </div>
          </label>

          <label onClick={() => handleModeChange('correct_skus')} style={{
            display: 'flex', alignItems: 'center', gap: 8, padding: '10px 16px', borderRadius: 8, cursor: 'pointer',
            border: mode === 'correct_skus' ? '2px solid var(--green)' : '2px solid var(--border)',
            backgroundColor: mode === 'correct_skus' ? '#e8f5f0' : 'transparent',
          }}>
            <input type="radio" checked={mode === 'correct_skus'} onChange={() => handleModeChange('correct_skus')}
              style={{ accentColor: 'var(--green)' }} />
            <div>
              <div style={{ fontSize: 13, fontWeight: 600 }}>Correct SKUs</div>
              <div style={{ fontSize: 11, color: 'var(--text-muted)' }}>Fix mismatched Shopify SKUs (e.g., extra dashes)</div>
            </div>
          </label>
        </div>

        {/* File + options */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 12, flexWrap: 'wrap' }}>
          <label style={{
            padding: '8px 20px', borderRadius: 6, border: '1px solid var(--border)',
            backgroundColor: '#fafafa', cursor: 'pointer', fontSize: 13, fontWeight: 500,
            display: 'flex', alignItems: 'center', gap: 8,
          }}>
            {'\uD83D\uDCC1'} {fileName || 'Choose CSV or XLSX file...'}
            <input ref={fileRef} type="file" accept=".csv,.txt,.xlsx,.xls" style={{ display: 'none' }}
              onChange={e => handleFileSelect(e.target.files?.[0])} />
          </label>

          <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 13, cursor: 'pointer', userSelect: 'none' }}>
            <input type="checkbox" checked={dryRun} onChange={e => { setDryRun(e.target.checked); setResults(null); }}
              style={{ width: 16, height: 16, accentColor: 'var(--green)', cursor: 'pointer' }} />
            <span style={{ fontWeight: 500 }}>Dry run</span>
            <span style={{ color: 'var(--text-muted)', fontSize: 12 }}>(preview only)</span>
          </label>

          <button onClick={handleUpload} disabled={uploading || !fileName} style={{
            padding: '8px 24px', borderRadius: 6, border: 'none',
            backgroundColor: fileName ? (dryRun ? '#3498db' : 'var(--green)') : '#ccc',
            color: '#fff', fontWeight: 600, fontSize: 13,
            cursor: fileName ? 'pointer' : 'not-allowed',
          }}>
            {uploading ? 'Processing...' : dryRun ? 'Preview Changes' : (mode === 'correct_skus' ? 'Correct SKUs' : 'Upload & Apply')}
          </button>
        </div>

        {/* Title column picker (shown once headers have been detected) */}
        {(availableHeaders.length > 0 || detectingHeaders) && (
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 16, padding: '8px 12px', backgroundColor: '#f8f9fa', borderRadius: 6 }}>
            <span style={{ fontSize: 12, color: 'var(--text-light)' }}>Product title column:</span>
            {detectingHeaders ? (
              <span style={{ fontSize: 12, color: 'var(--text-muted)' }}>Detecting columns...</span>
            ) : (
              <>
                <select value={titleCol} onChange={e => setTitleCol(e.target.value)}
                  style={{ padding: '4px 8px', borderRadius: 4, border: '1px solid var(--border)', fontSize: 12 }}>
                  <option value="">(none — don't show)</option>
                  {availableHeaders.map(h => <option key={h} value={h}>{h}</option>)}
                </select>
                <span style={{ fontSize: 11, color: 'var(--text-muted)' }}>
                  Adds a column to the preview so you can verify SKUs match the right products.
                </span>
              </>
            )}
          </div>
        )}

        {/* Expected format hint */}
        <div style={{ backgroundColor: '#f8f9fa', borderRadius: 6, padding: '10px 16px', fontSize: 12, color: 'var(--text-light)', marginBottom: 16 }}>
          {mode === 'correct_skus' ? (
            <><strong>CSV or XLSX needs a SKU column.</strong> The tool compares each SKU against Shopify and finds near-matches where the Shopify SKU has an extra dash or other minor difference. Only SKUs are corrected — no metafields are changed.</>
          ) : (
            <><strong>CSV or XLSX needs SKU + at least one of Cost USD, System Code.</strong> Values are written to Shopify variant metafields. SKUs not present in your file are left untouched. Blank cells preserve the existing value.</>
          )}
        </div>

        {/* Results */}
        {results && (
          <div>
            {/* Summary banner */}
            <div style={{
              padding: '12px 16px', borderRadius: 8, marginBottom: 16, fontSize: 13, fontWeight: 600,
              backgroundColor: isDryRun ? '#eaf4fd' : (notFoundCount > 0 ? '#fef3e2' : '#e8f5f0'),
              border: `1px solid ${isDryRun ? '#3498db' : (notFoundCount > 0 ? '#e67e22' : '#1a7e5a')}`,
              color: isDryRun ? '#2471a3' : (notFoundCount > 0 ? '#b7770a' : '#15664a'),
            }}>
              {isDryRun ? '\uD83D\uDD0D DRY RUN \u2014 ' : '\u2705 APPLIED \u2014 '}
              {results.message}
            </div>

            {/* Filter tabs */}
            <div style={{ display: 'flex', gap: 2, backgroundColor: '#f0f1f3', borderRadius: 6, padding: 2, marginBottom: 12, width: 'fit-content' }}>
              {isSkuMode ? (
                [['all', `All (${results.total})`],
                 ['exact', `Exact (${skuExactCount})`],
                 ['correction', `Corrections (${skuCorrectionCount})`],
                 ['not_found', `Not found (${notFoundCount})`],
                ].map(([val, label]) => (
                  <button key={val} onClick={() => setFilterStatus(val)} style={{
                    padding: '5px 12px', borderRadius: 4, border: 'none', fontSize: 12, fontWeight: 600, cursor: 'pointer',
                    backgroundColor: filterStatus === val ? 'var(--white)' : 'transparent',
                    color: filterStatus === val ? (val === 'not_found' ? '#e74c3c' : val === 'correction' ? '#e67e22' : 'var(--green)') : 'var(--text-light)',
                    boxShadow: filterStatus === val ? '0 1px 3px rgba(0,0,0,0.1)' : 'none',
                  }}>{label}</button>
                ))
              ) : (
                [['all', `All (${results.total})`],
                 ['matched', `Matched (${matchedCount})`],
                 ['not_found', `Not found (${notFoundCount})`],
                 ...(errorCount > 0 ? [['error', `Errors (${errorCount})`]] : []),
                ].map(([val, label]) => (
                  <button key={val} onClick={() => setFilterStatus(val)} style={{
                    padding: '5px 12px', borderRadius: 4, border: 'none', fontSize: 12, fontWeight: 600, cursor: 'pointer',
                    backgroundColor: filterStatus === val ? 'var(--white)' : 'transparent',
                    color: filterStatus === val ? (val === 'not_found' ? '#e74c3c' : 'var(--green)') : 'var(--text-light)',
                    boxShadow: filterStatus === val ? '0 1px 3px rgba(0,0,0,0.1)' : 'none',
                  }}>{label}</button>
                ))
              )}
            </div>

            {/* Results table */}
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
              <thead>
                <tr style={{ borderBottom: '2px solid var(--border)' }}>
                  <th style={{ padding: '8px 12px', textAlign: 'left', color: 'var(--text-light)', fontWeight: 500 }}>Status</th>
                  <th style={{ padding: '8px 12px', textAlign: 'left', color: 'var(--text-light)', fontWeight: 500 }}>CSV SKU</th>
                  {isSkuMode && <th style={{ padding: '8px 12px', textAlign: 'left', color: 'var(--text-light)', fontWeight: 500 }}>Shopify SKU</th>}
                  {titleCol && <th style={{ padding: '8px 12px', textAlign: 'left', color: 'var(--text-light)', fontWeight: 500 }}>From File ({titleCol})</th>}
                  <th style={{ padding: '8px 12px', textAlign: 'left', color: 'var(--text-light)', fontWeight: 500 }}>Shopify Product</th>
                  <th style={{ padding: '8px 12px', textAlign: 'left', color: 'var(--text-light)', fontWeight: 500 }}>
                    {isSkuMode ? 'Correction' : 'Changes'}
                  </th>
                </tr>
              </thead>
              <tbody>
                {filtered.map((r, i) => {
                  const rowBg = (r.match_type === 'not_found' || r.found_in_shopify === false) ? '#fef5f5'
                    : r.needs_correction ? '#fef9e7' : 'transparent';
                  return (
                    <tr key={i} style={{ borderBottom: '1px solid var(--border)', backgroundColor: rowBg }}>
                      <td style={{ padding: '6px 12px' }}>
                        {isSkuMode ? (<>
                          {r.action === 'exact' && <span style={{ color: 'var(--green)', fontWeight: 600 }}>{'\u2713'} Exact match</span>}
                          {r.action === 'would_correct' && <span style={{ color: '#e67e22', fontWeight: 600 }}>{'\uD83D\uDD04'} Would correct</span>}
                          {r.action === 'corrected' && <span style={{ color: 'var(--green)', fontWeight: 600 }}>{'\u2713'} Corrected</span>}
                          {r.action === 'not_found' && <span style={{ color: '#e74c3c', fontWeight: 600 }}>{'\u2717'} Not found</span>}
                          {r.action === 'error' && <span style={{ color: '#e74c3c', fontWeight: 600 }}>{'\u26A0'} Error</span>}
                        </>) : (<>
                          {r.action === 'would_update' && <span style={{ color: '#3498db', fontWeight: 600 }}>{'\uD83D\uDD0D'} Would update</span>}
                          {r.action === 'no_change' && <span style={{ color: 'var(--text-muted)', fontWeight: 600 }}>{'\u2014'} No change</span>}
                          {r.action === 'updated' && <span style={{ color: 'var(--green)', fontWeight: 600 }}>{'\u2713'} Updated</span>}
                          {r.action === 'no_match' && <span style={{ color: '#e74c3c', fontWeight: 600 }}>{'\u2717'} Not found</span>}
                          {r.action === 'error' && <span style={{ color: '#e67e22', fontWeight: 600 }}>{'\u26A0'} Error</span>}
                        </>)}
                      </td>
                      <td style={{ padding: '6px 12px', fontFamily: 'monospace' }}>{r.csv_sku || r.sku}</td>
                      {isSkuMode && (
                        <td style={{ padding: '6px 12px', fontFamily: 'monospace', color: r.needs_correction ? '#e67e22' : 'var(--text-light)' }}>
                          {r.shopify_sku || '\u2014'}
                        </td>
                      )}
                      {titleCol && (
                        <td style={{ padding: '6px 12px', fontSize: 11, color: '#2c3e50', fontWeight: 500, maxWidth: 280, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}
                            title={r.csv_title || ''}>
                          {r.csv_title || '\u2014'}
                        </td>
                      )}
                      <td style={{ padding: '6px 12px', color: 'var(--text-light)', fontSize: 11 }}>
                        {r.title || r.shopify_title || ''}
                      </td>
                      <td style={{ padding: '6px 12px', fontSize: 11 }}>
                        {isSkuMode ? (
                          r.needs_correction ? (
                            <span>
                              <span style={{ textDecoration: 'line-through', color: '#e74c3c' }}>{r.shopify_sku}</span>
                              {' \u2192 '}
                              <span style={{ color: 'var(--green)', fontWeight: 600 }}>{r.csv_sku}</span>
                            </span>
                          ) : (r.error || '')
                        ) : (
                          r.found_in_shopify === false ? (
                            <span style={{ color: '#e74c3c' }}>{r.error || 'Not found'}</span>
                          ) : r.metafield_changes ? (
                            // Dry run: show prior → new per field with color coding
                            <div style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
                              {Object.entries(r.metafield_changes).map(([k, v]) => (
                                <div key={k} style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
                                  <span style={{ fontWeight: 600, color: 'var(--text-light)', minWidth: 85 }}>{k}:</span>
                                  <span style={{
                                    padding: '1px 5px', borderRadius: 3,
                                    backgroundColor: '#f5f5f5', color: 'var(--text-light)',
                                    textDecoration: v.changed ? 'line-through' : 'none',
                                  }}>{v.prior || '—'}</span>
                                  <span style={{ color: 'var(--text-muted)' }}>{'\u2192'}</span>
                                  <span style={{
                                    padding: '1px 5px', borderRadius: 3, fontWeight: 500,
                                    backgroundColor: v.changed ? '#e8f5f0' : '#f5f5f5',
                                    color: v.changed ? 'var(--green)' : 'var(--text-muted)',
                                  }}>{v.new || '—'}</span>
                                  {v.keep_prior && (
                                    <span style={{ fontSize: 10, color: 'var(--text-muted)', fontStyle: 'italic' }}>(keep prior)</span>
                                  )}
                                </div>
                              ))}
                              {Object.keys(r.metafield_changes).length === 0 && (
                                <span style={{ color: 'var(--text-muted)' }}>(no values to set)</span>
                              )}
                            </div>
                          ) : r.metafields && Object.keys(r.metafields).length > 0 ? (
                            // Apply mode (non-dry-run) — legacy display
                            Object.entries(r.metafields).map(([k, v]) => (
                              <span key={k} style={{
                                display: 'inline-block', padding: '1px 6px', borderRadius: 3, marginRight: 4,
                                backgroundColor: '#e8f5f0', color: 'var(--green)', fontWeight: 500,
                              }}>{k}={v}</span>
                            ))
                          ) : (
                            <span style={{ color: '#e74c3c' }}>{r.error || ''}</span>
                          )
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>

            {/* Apply button after dry run */}
            {isDryRun && (isSkuMode ? skuCorrectionCount > 0 : updatedCount > 0 || matchedCount > 0) && (
              <div style={{ marginTop: 16, padding: '12px 16px', backgroundColor: '#f8f9fa', borderRadius: 6, display: 'flex', alignItems: 'center', gap: 12 }}>
                <span style={{ fontSize: 13, color: 'var(--text-light)' }}>
                  {isSkuMode
                    ? `Ready to correct ${skuCorrectionCount} SKUs?`
                    : `Ready to apply changes?`
                  }
                </span>
                <button onClick={handleApplyNow} disabled={uploading} style={{
                  padding: '8px 20px', borderRadius: 6, border: 'none', backgroundColor: 'var(--green)',
                  color: '#fff', fontWeight: 600, fontSize: 13, cursor: 'pointer',
                }}>
                  {uploading ? 'Applying...' : 'Apply changes now'}
                </button>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
