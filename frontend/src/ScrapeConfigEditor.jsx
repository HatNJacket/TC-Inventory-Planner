/**
 * ScrapeConfigEditor — manages vendor website scraping configurations.
 * Embedded within VendorManagementPage, shown when a vendor is selected.
 * 
 * Features:
 *   - View/edit the JSON scrape config for the selected vendor
 *   - Load built-in defaults for known vendors
 *   - Test the config against a sample product URL
 *   - View extracted results (description, features, specs, documents, images)
 *   - Save config back to the database
 * 
 * Usage in VendorManagementPage.jsx:
 *   import ScrapeConfigEditor from './ScrapeConfigEditor';
 *   ...
 *   {selectedVendor && (
 *     <ScrapeConfigEditor vendor={selectedVendor} />
 *   )}
 */
import { useState, useEffect, useCallback } from 'react';
import {
  getVendorScrapeConfig,
  saveVendorScrapeConfig,
  resetVendorScrapeConfig,
  testVendorScrapeConfig,
  loadDefaultScrapeConfig,
} from './api';

export default function ScrapeConfigEditor({ vendor }) {
  const [config, setConfig] = useState(null);
  const [configSource, setConfigSource] = useState('none');  // saved | default | none
  const [configJson, setConfigJson] = useState('');
  const [jsonError, setJsonError] = useState('');
  const [saving, setSaving] = useState(false);
  const [loading, setLoading] = useState(false);

  // Test state
  const [testUrl, setTestUrl] = useState('');
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState(null);

  // Section expand/collapse
  const [expandedSections, setExpandedSections] = useState({});

  // UI mode: 'visual' or 'json'
  const [editMode, setEditMode] = useState('visual');

  const loadConfig = useCallback(async () => {
    if (!vendor) return;
    setLoading(true);
    try {
      const data = await getVendorScrapeConfig(vendor);
      setConfig(data.config);
      setConfigSource(data.source);
      setConfigJson(data.config ? JSON.stringify(data.config, null, 2) : '');
      setJsonError('');
      setTestResult(null);
    } catch (e) {
      console.error('Failed to load scrape config:', e);
    }
    setLoading(false);
  }, [vendor]);

  useEffect(() => { loadConfig(); }, [loadConfig]);

  const handleSave = async () => {
    let parsed;
    try {
      parsed = JSON.parse(configJson);
    } catch (e) {
      setJsonError('Invalid JSON: ' + e.message);
      return;
    }
    setJsonError('');
    setSaving(true);
    try {
      await saveVendorScrapeConfig(vendor, parsed);
      setConfig(parsed);
      setConfigSource('saved');
    } catch (e) {
      setJsonError('Save failed: ' + e.message);
    }
    setSaving(false);
  };

  const handleReset = async () => {
    if (!window.confirm(`Reset ${vendor} scrape config to defaults? This removes any customisations.`)) return;
    try {
      await resetVendorScrapeConfig(vendor);
      await loadConfig();
    } catch (e) {
      console.error('Reset failed:', e);
    }
  };

  const handleLoadDefault = async () => {
    try {
      const data = await loadDefaultScrapeConfig(vendor);
      if (data.config) {
        setConfig(data.config);
        setConfigSource('saved');
        setConfigJson(JSON.stringify(data.config, null, 2));
        setJsonError('');
      }
    } catch (e) {
      setJsonError('No built-in default for this vendor');
    }
  };

  const handleTest = async () => {
    if (!testUrl.trim()) return;
    setTesting(true);
    setTestResult(null);
    try {
      const result = await testVendorScrapeConfig(vendor, testUrl.trim());
      setTestResult(result);
    } catch (e) {
      setTestResult({ status: 'error', error: e.message });
    }
    setTesting(false);
  };

  const toggleSection = (key) => {
    setExpandedSections(prev => ({ ...prev, [key]: !prev[key] }));
  };

  // ── Visual config editor helpers ────────────────────────────

  const updateSectionField = (sectionName, field, value) => {
    try {
      const parsed = JSON.parse(configJson);
      if (!parsed.sections) parsed.sections = {};
      if (!parsed.sections[sectionName]) parsed.sections[sectionName] = {};
      parsed.sections[sectionName][field] = value;
      setConfigJson(JSON.stringify(parsed, null, 2));
      setJsonError('');
    } catch (e) {
      setJsonError('Config JSON is invalid — switch to JSON mode to fix');
    }
  };

  const updateTopLevelField = (field, value) => {
    try {
      const parsed = JSON.parse(configJson);
      parsed[field] = value;
      setConfigJson(JSON.stringify(parsed, null, 2));
      setJsonError('');
    } catch (e) {
      setJsonError('Config JSON is invalid — switch to JSON mode to fix');
    }
  };

  // ── Styles ──────────────────────────────────────────────────

  const styles = {
    container: {
      border: '1px solid #374151', borderRadius: 8, padding: 16,
      marginTop: 16, background: '#1a1f2e',
    },
    header: {
      display: 'flex', justifyContent: 'space-between', alignItems: 'center',
      marginBottom: 12,
    },
    title: { fontSize: 15, fontWeight: 600, color: '#e5e7eb' },
    badge: (source) => ({
      fontSize: 11, padding: '2px 8px', borderRadius: 10, fontWeight: 500,
      background: source === 'saved' ? '#065f46' : source === 'default' ? '#1e3a5f' : '#4a2020',
      color: source === 'saved' ? '#6ee7b7' : source === 'default' ? '#93c5fd' : '#fca5a5',
    }),
    btnRow: { display: 'flex', gap: 8, flexWrap: 'wrap', marginBottom: 12 },
    btn: (variant) => ({
      padding: '5px 12px', borderRadius: 5, border: 'none', cursor: 'pointer',
      fontSize: 12, fontWeight: 500,
      background: variant === 'primary' ? '#3b82f6' :
                  variant === 'success' ? '#059669' :
                  variant === 'danger' ? '#dc2626' :
                  variant === 'purple' ? '#7c3aed' : '#4b5563',
      color: '#fff',
      opacity: (saving || testing || loading) ? 0.6 : 1,
    }),
    tabRow: {
      display: 'flex', gap: 0, marginBottom: 12, borderBottom: '1px solid #374151',
    },
    tab: (active) => ({
      padding: '6px 16px', cursor: 'pointer', fontSize: 12, fontWeight: 500,
      color: active ? '#3b82f6' : '#9ca3af',
      borderBottom: active ? '2px solid #3b82f6' : '2px solid transparent',
      background: 'none', border: 'none',
    }),
    textarea: {
      width: '100%', minHeight: 300, fontFamily: 'monospace', fontSize: 12,
      background: '#111827', color: '#d1d5db', border: '1px solid #374151',
      borderRadius: 6, padding: 10, resize: 'vertical',
    },
    input: {
      flex: 1, padding: '6px 10px', fontSize: 13, borderRadius: 5,
      border: '1px solid #374151', background: '#111827', color: '#d1d5db',
    },
    inputSmall: {
      padding: '4px 8px', fontSize: 12, borderRadius: 4,
      border: '1px solid #374151', background: '#111827', color: '#d1d5db',
      width: '100%',
    },
    label: { fontSize: 11, color: '#9ca3af', marginBottom: 2, display: 'block' },
    error: { color: '#ef4444', fontSize: 12, marginTop: 4 },
    sectionCard: {
      border: '1px solid #2d3748', borderRadius: 6, marginBottom: 8,
      background: '#111827',
    },
    sectionHeader: {
      display: 'flex', justifyContent: 'space-between', alignItems: 'center',
      padding: '8px 12px', cursor: 'pointer', fontSize: 13, color: '#e5e7eb',
    },
    sectionBody: { padding: '8px 12px 12px', borderTop: '1px solid #2d3748' },
    fieldRow: {
      display: 'grid', gridTemplateColumns: '120px 1fr', gap: 8,
      alignItems: 'center', marginBottom: 6,
    },
    // Test result styles
    resultCard: {
      border: '1px solid #374151', borderRadius: 6, padding: 12,
      marginTop: 12, background: '#111827',
    },
    resultTitle: { fontSize: 13, fontWeight: 600, color: '#e5e7eb', marginBottom: 8 },
    resultItem: { fontSize: 12, color: '#d1d5db', marginBottom: 4 },
    resultBadge: (ok) => ({
      display: 'inline-block', fontSize: 11, padding: '1px 6px', borderRadius: 8,
      background: ok ? '#065f46' : '#4a2020',
      color: ok ? '#6ee7b7' : '#fca5a5',
      marginLeft: 6,
    }),
    preBlock: {
      background: '#0d1117', border: '1px solid #2d3748', borderRadius: 4,
      padding: 8, fontSize: 11, color: '#d1d5db', whiteSpace: 'pre-wrap',
      maxHeight: 200, overflow: 'auto', fontFamily: 'monospace',
    },
    imgPreview: {
      display: 'flex', gap: 8, flexWrap: 'wrap', marginTop: 8,
    },
    imgThumb: {
      width: 80, height: 80, objectFit: 'cover', borderRadius: 4,
      border: '1px solid #374151',
    },
  };

  if (!vendor) return null;

  // Parse config for visual editor
  let parsedConfig = null;
  try { parsedConfig = JSON.parse(configJson); } catch {}

  return (
    <div style={styles.container}>
      {/* Header */}
      <div style={styles.header}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <span style={styles.title}>🔍 Website Scrape Config</span>
          <span style={styles.badge(configSource)}>
            {configSource === 'saved' ? '✓ Saved' : configSource === 'default' ? '📦 Default' : '⚠ None'}
          </span>
        </div>
        <div style={{ display: 'flex', gap: 6 }}>
          <button style={styles.btn('default')} onClick={handleLoadDefault}
                  disabled={saving || loading}>
            Load Default
          </button>
          {configSource === 'saved' && (
            <button style={styles.btn('danger')} onClick={handleReset}
                    disabled={saving || loading}>
              Reset
            </button>
          )}
        </div>
      </div>

      {/* Edit mode tabs */}
      <div style={styles.tabRow}>
        <button style={styles.tab(editMode === 'visual')}
                onClick={() => setEditMode('visual')}>Visual</button>
        <button style={styles.tab(editMode === 'json')}
                onClick={() => setEditMode('json')}>JSON</button>
        <button style={styles.tab(editMode === 'test')}
                onClick={() => setEditMode('test')}>Test</button>
      </div>

      {/* ── Visual Editor ─────────────────────────────────────── */}
      {editMode === 'visual' && parsedConfig && (
        <div>
          {/* Top-level fields */}
          <div style={{ ...styles.fieldRow, marginBottom: 12 }}>
            <span style={styles.label}>Platform</span>
            <input style={styles.inputSmall} value={parsedConfig.platform || ''}
                   onChange={e => updateTopLevelField('platform', e.target.value)} />
          </div>
          <div style={{ ...styles.fieldRow, marginBottom: 12 }}>
            <span style={styles.label}>Base URL</span>
            <input style={styles.inputSmall} value={parsedConfig.base_url || ''}
                   onChange={e => updateTopLevelField('base_url', e.target.value)} />
          </div>
          <div style={{ ...styles.fieldRow, marginBottom: 16 }}>
            <span style={styles.label}>URL Pattern</span>
            <input style={styles.inputSmall} value={parsedConfig.url_pattern || ''}
                   onChange={e => updateTopLevelField('url_pattern', e.target.value)}
                   placeholder="https://vendor.com/product/{sku}.htm" />
          </div>

          {/* Sections */}
          {parsedConfig.sections && Object.entries(parsedConfig.sections).map(([name, sec]) => (
            <div key={name} style={styles.sectionCard}>
              <div style={styles.sectionHeader} onClick={() => toggleSection(name)}>
                <span>
                  {name === 'description' ? '📝' : name === 'features' ? '✨' :
                   name === 'specs' ? '📊' : name === 'documents' ? '📎' : '🖼️'}{' '}
                  {name.charAt(0).toUpperCase() + name.slice(1)}
                  <span style={{ color: '#6b7280', fontSize: 11, marginLeft: 8 }}>
                    {sec.method}
                  </span>
                </span>
                <span style={{ color: '#6b7280' }}>{expandedSections[name] ? '▼' : '▶'}</span>
              </div>
              {expandedSections[name] && (
                <div style={styles.sectionBody}>
                  <div style={styles.fieldRow}>
                    <span style={styles.label}>Method</span>
                    <select style={styles.inputSmall} value={sec.method || ''}
                            onChange={e => updateSectionField(name, 'method', e.target.value)}>
                      <option value="between_markers">Between Markers</option>
                      <option value="og_and_product_images">OG + Product Images</option>
                      <option value="shopify_product_json">Shopify Product JSON</option>
                      <option value="extract_from_description">Extract from Description</option>
                      <option value="link_scan">Link Scan</option>
                    </select>
                  </div>
                  {sec.method === 'between_markers' && (
                    <>
                      <div style={styles.fieldRow}>
                        <span style={styles.label}>Start marker</span>
                        <input style={styles.inputSmall} value={sec.start || ''}
                               onChange={e => updateSectionField(name, 'start', e.target.value)} />
                      </div>
                      <div style={styles.fieldRow}>
                        <span style={styles.label}>End marker</span>
                        <input style={styles.inputSmall} value={sec.end || ''}
                               onChange={e => updateSectionField(name, 'end', e.target.value)} />
                      </div>
                      <div style={styles.fieldRow}>
                        <span style={styles.label}>Fallback start</span>
                        <input style={styles.inputSmall} value={sec.fallback_start || ''}
                               onChange={e => updateSectionField(name, 'fallback_start', e.target.value)} />
                      </div>
                      <div style={styles.fieldRow}>
                        <span style={styles.label}>Fallback end</span>
                        <input style={styles.inputSmall} value={sec.fallback_end || ''}
                               onChange={e => updateSectionField(name, 'fallback_end', e.target.value)} />
                      </div>
                      <div style={styles.fieldRow}>
                        <span style={styles.label}>Extract as</span>
                        <select style={styles.inputSmall} value={sec.extract_as || 'text'}
                                onChange={e => updateSectionField(name, 'extract_as', e.target.value)}>
                          <option value="text">Text</option>
                          <option value="key_value_table">Key-Value Table</option>
                          <option value="bullet_list">Bullet List</option>
                          <option value="link_list">Link List</option>
                        </select>
                      </div>
                    </>
                  )}
                  {sec.notes && (
                    <div style={{ fontSize: 11, color: '#6b7280', marginTop: 6, fontStyle: 'italic' }}>
                      {sec.notes}
                    </div>
                  )}
                </div>
              )}
            </div>
          ))}

          <div style={{ ...styles.btnRow, marginTop: 12 }}>
            <button style={styles.btn('success')} onClick={handleSave} disabled={saving}>
              {saving ? 'Saving...' : 'Save Config'}
            </button>
          </div>
        </div>
      )}

      {editMode === 'visual' && !parsedConfig && (
        <div style={{ color: '#9ca3af', fontSize: 13, padding: 20, textAlign: 'center' }}>
          No config loaded. Click <strong>Load Default</strong> to start with a built-in
          config for {vendor}, or switch to <strong>JSON</strong> mode to paste one.
        </div>
      )}

      {/* ── JSON Editor ───────────────────────────────────────── */}
      {editMode === 'json' && (
        <div>
          <textarea
            style={styles.textarea}
            value={configJson}
            onChange={e => { setConfigJson(e.target.value); setJsonError(''); }}
            placeholder='{"platform": "volusion", "base_url": "...", "sections": {...}}'
          />
          {jsonError && <div style={styles.error}>{jsonError}</div>}
          <div style={{ ...styles.btnRow, marginTop: 8 }}>
            <button style={styles.btn('success')} onClick={handleSave} disabled={saving}>
              {saving ? 'Saving...' : 'Save Config'}
            </button>
          </div>
        </div>
      )}

      {/* ── Test Tab ──────────────────────────────────────────── */}
      {editMode === 'test' && (
        <div>
          <div style={{ display: 'flex', gap: 8, marginBottom: 12 }}>
            <input
              style={styles.input}
              value={testUrl}
              onChange={e => setTestUrl(e.target.value)}
              placeholder="Paste a manufacturer product URL to test..."
              onKeyDown={e => e.key === 'Enter' && handleTest()}
            />
            <button style={styles.btn('purple')} onClick={handleTest}
                    disabled={testing || !testUrl.trim()}>
              {testing ? 'Testing...' : '🧪 Test'}
            </button>
          </div>

          {testResult && testResult.status === 'error' && (
            <div style={styles.error}>{testResult.error}</div>
          )}

          {testResult && testResult.status === 'ok' && (
            <div>
              {/* Summary badges */}
              <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginBottom: 12 }}>
                <span style={styles.resultBadge(testResult.summary.has_description)}>
                  Description {testResult.summary.has_description ? `✓ (${testResult.summary.description_length} chars)` : '✗'}
                </span>
                {testResult.summary.highlight_count != null && (
                  <span style={styles.resultBadge(testResult.summary.highlight_count > 0)}>
                    Highlights: {testResult.summary.highlight_count}
                  </span>
                )}
                <span style={styles.resultBadge(testResult.summary.feature_count > 0)}>
                  Features: {testResult.summary.feature_count}
                </span>
                <span style={styles.resultBadge(testResult.summary.spec_count > 0)}>
                  Specs: {testResult.summary.spec_count}
                </span>
                <span style={styles.resultBadge(testResult.summary.document_categories?.length > 0)}>
                  Docs: {testResult.summary.document_categories?.join(', ') || 'none'}
                </span>
                <span style={styles.resultBadge(testResult.summary.image_count > 0)}>
                  Images: {testResult.summary.image_count}
                </span>
              </div>

              {/* Description preview */}
              {testResult.extraction.description && (
                <div style={styles.resultCard}>
                  <div style={styles.resultTitle}>📝 Description</div>
                  <div style={styles.preBlock}>
                    {typeof testResult.extraction.description === 'string'
                      ? testResult.extraction.description.slice(0, 1000)
                      : JSON.stringify(testResult.extraction.description, null, 2).slice(0, 1000)}
                  </div>
                </div>
              )}

              {/* Highlights preview (top bullets, e.g. Baader short_description) */}
              {testResult.extraction.highlights?.length > 0 && (
                <div style={styles.resultCard}>
                  <div style={styles.resultTitle}>⭐ Highlights ({testResult.extraction.highlights.length})</div>
                  <ul style={{ margin: 0, paddingLeft: 20, fontSize: 12, color: '#d1d5db' }}>
                    {testResult.extraction.highlights.map((h, i) => (
                      <li key={i} style={{ marginBottom: 3 }}>{h}</li>
                    ))}
                  </ul>
                </div>
              )}

              {/* Features preview */}
              {testResult.extraction.features?.length > 0 && (
                <div style={styles.resultCard}>
                  <div style={styles.resultTitle}>✨ Features ({testResult.extraction.features.length})</div>
                  <ul style={{ margin: 0, paddingLeft: 20, fontSize: 12, color: '#d1d5db' }}>
                    {testResult.extraction.features.map((f, i) => (
                      <li key={i} style={{ marginBottom: 3 }}>{f}</li>
                    ))}
                  </ul>
                </div>
              )}

              {/* Specs preview */}
              {testResult.extraction.specs?.length > 0 && (
                <div style={styles.resultCard}>
                  <div style={styles.resultTitle}>📊 Technical Specs ({testResult.extraction.specs.length})</div>
                  <table style={{ width: '100%', fontSize: 12, color: '#d1d5db' }}>
                    <tbody>
                      {testResult.extraction.specs.map((s, i) => (
                        <tr key={i} style={{ borderBottom: '1px solid #1f2937' }}>
                          <td style={{ padding: '3px 8px', fontWeight: 500, color: '#93c5fd', width: '40%' }}>
                            {s.key}
                          </td>
                          <td style={{ padding: '3px 8px' }}>{s.value}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}

              {/* Documents preview */}
              {testResult.extraction.documents &&
               Object.keys(testResult.extraction.documents).length > 0 && (
                <div style={styles.resultCard}>
                  <div style={styles.resultTitle}>📎 Support Documents</div>
                  {Object.entries(testResult.extraction.documents).map(([cat, links]) => (
                    <div key={cat} style={{ marginBottom: 6 }}>
                      <span style={{ fontSize: 11, fontWeight: 600, color: '#93c5fd', textTransform: 'uppercase' }}>
                        {cat}
                      </span>
                      {links.map((link, i) => (
                        <div key={i} style={{ fontSize: 12, color: '#d1d5db', paddingLeft: 12 }}>
                          <a href={link.url} target="_blank" rel="noopener noreferrer"
                             style={{ color: '#60a5fa' }}>{link.label}</a>
                        </div>
                      ))}
                    </div>
                  ))}
                </div>
              )}

              {/* Images preview */}
              {testResult.extraction.images?.length > 0 && (
                <div style={styles.resultCard}>
                  <div style={styles.resultTitle}>🖼️ Images ({testResult.extraction.images.length})</div>
                  <div style={styles.imgPreview}>
                    {testResult.extraction.images.slice(0, 5).map((url, i) => (
                      <img key={i} src={url} alt={`Product ${i+1}`} style={styles.imgThumb}
                           onError={e => { e.target.style.display = 'none'; }} />
                    ))}
                  </div>
                </div>
              )}

              {/* AI context preview */}
              <div style={styles.resultCard}>
                <div style={styles.resultTitle}>🤖 AI Context Preview</div>
                <div style={{ ...styles.preBlock, maxHeight: 300 }}>
                  {testResult.ai_context_preview}
                </div>
              </div>

              {/* Specs HTML preview */}
              {testResult.specs_html_preview && (
                <div style={styles.resultCard}>
                  <div style={styles.resultTitle}>📊 Specs HTML (appended to description)</div>
                  <div style={{ fontSize: 12, color: '#d1d5db', padding: 8, background: '#0d1117',
                                borderRadius: 4, maxHeight: 200, overflow: 'auto' }}
                       dangerouslySetInnerHTML={{ __html: testResult.specs_html_preview }} />
                </div>
              )}

              {/* Documents HTML preview */}
              {testResult.docs_html_preview && (
                <div style={styles.resultCard}>
                  <div style={styles.resultTitle}>📎 Documents HTML (appended to description)</div>
                  <div style={{ fontSize: 12, color: '#d1d5db', padding: 8, background: '#0d1117',
                                borderRadius: 4, maxHeight: 200, overflow: 'auto' }}
                       dangerouslySetInnerHTML={{ __html: testResult.docs_html_preview }} />
                </div>
              )}
            </div>
          )}

          {!testResult && !testing && (
            <div style={{ color: '#6b7280', fontSize: 12, textAlign: 'center', padding: 20 }}>
              Enter a manufacturer product URL and click Test to preview what the scraper extracts.
              <br />The config must be saved first — the test uses the saved config.
            </div>
          )}
        </div>
      )}
    </div>
  );
}
