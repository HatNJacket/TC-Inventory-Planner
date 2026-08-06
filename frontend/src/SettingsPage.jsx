import { useState, useEffect, useCallback } from 'react';
import * as api from './api';
import { queueTestLabel } from './labelPrinter';

const s = {
  card: { backgroundColor: 'var(--card-bg, #fff)', border: '1px solid var(--border)', borderRadius: 8, padding: '20px 24px', marginBottom: 16 },
  label: { fontSize: 12, fontWeight: 600, color: 'var(--text)', display: 'block', marginBottom: 4 },
  input: { padding: '7px 10px', borderRadius: 6, border: '1px solid var(--border)', fontSize: 13, width: 140, boxSizing: 'border-box' },
  btn: (variant) => ({
    padding: '8px 18px', borderRadius: 6, border: 'none', cursor: 'pointer', fontWeight: 600, fontSize: 12,
    backgroundColor: variant === 'primary' ? '#6366f1' : '#e2e8f0',
    color: variant === 'primary' ? '#fff' : 'var(--text)',
  }),
  help: { fontSize: 11, color: 'var(--text-muted)', marginTop: 4 },
};

export default function SettingsPage({ onToast }) {
  const [thresholds, setThresholds] = useState(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [edits, setEdits] = useState({});

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const data = await api.getThresholds();
      setThresholds(data);
      setEdits({});
    } catch (e) { onToast?.('Failed to load settings: ' + e.message, 'error'); }
    setLoading(false);
  }, [onToast]);

  useEffect(() => { load(); }, [load]);

  const setEdit = (key, val) => setEdits(prev => ({ ...prev, [key]: val }));
  const currentValue = (key) => edits[key] !== undefined ? edits[key] : thresholds?.[key]?.value;
  const isDirty = Object.keys(edits).length > 0;

  const handleSave = async () => {
    // Validate — convert to numbers
    const payload = {};
    for (const [key, val] of Object.entries(edits)) {
      const num = parseFloat(val);
      if (isNaN(num) || num < 0) {
        onToast?.(`Invalid value for ${key}`, 'error');
        return;
      }
      payload[key] = num;
    }
    setSaving(true);
    try {
      await api.updateThresholds(payload);
      onToast?.('Settings saved', 'success');
      await load();
    } catch (e) { onToast?.('Save failed: ' + e.message, 'error'); }
    setSaving(false);
  };

  const resetToDefault = (key) => setEdit(key, thresholds[key].default);

  if (loading || !thresholds) {
    return (
      <div style={{ padding: 40, textAlign: 'center', color: 'var(--text-muted)' }}>Loading...</div>
    );
  }

  return (
    <div style={{ padding: '24px 28px', overflow: 'auto', height: '100vh', maxWidth: 900 }}>
      <div style={{ marginBottom: 20 }}>
        <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>Settings</h1>
        <div style={{ fontSize: 12, color: 'var(--text-muted)', marginTop: 4 }}>
          Configure thresholds used by TC Planner's replenishment action lists. Changes take effect on the next action list refresh.
        </div>
      </div>

      <div style={s.card}>
        <div style={{ fontSize: 15, fontWeight: 700, marginBottom: 4 }}>Replenishment Thresholds</div>
        <div style={{ fontSize: 11, color: 'var(--text-muted)', marginBottom: 20 }}>
          Controls which products show up on the Buy More and Stop Buying action lists, and how aggressively the forecaster suggests quantities. Tune based on your seasonality and cash position.
        </div>

        <SettingRow
          label="Order cycle days"
          help={"How long each PO should last after it arrives. Default 30. Combined with the projection multiplier below to set the demand window: (cycle days × multiplier) + vendor lead time. Raise this if you order less often (every 6 weeks → set 42), lower if you order more often."}
          default={thresholds.replenishment_cycle_days?.default ?? 30}
          value={currentValue('replenishment_cycle_days')}
          onChange={v => setEdit('replenishment_cycle_days', v)}
          onReset={() => resetToDefault('replenishment_cycle_days')}
          suffix="days"
          step="1"
        />

        <SettingRow
          label="Projection multiplier"
          help={"Scales the order cycle. 1.0 = cycle days as-is (30 days at default); 1.5 = 1.5× cycle (45 days); 2.0 = 60 days. The vendor's lead time is added on top automatically as a fixed buffer — so with cycle=30, multiplier=1.5, and lead time=14, the system projects demand over 30×1.5 + 14 = 59 days. Higher = fewer stockouts but more capital tied up."}
          default={thresholds.projection_multiplier.default}
          value={currentValue('projection_multiplier')}
          onChange={v => setEdit('projection_multiplier', v)}
          onReset={() => resetToDefault('projection_multiplier')}
          suffix="×"
          step="0.1"
        />

        <SettingRow
          label="Waiter conversion rate"
          help={"Fraction of back-in-stock waiters expected to actually buy when restocked. Used as a hard floor on replenish_qty: if 10 customers are waiting and the rate is 0.6, the system orders at least 6 units regardless of velocity. Already-stocked and on-order units count toward fulfilling waiters. Set to 0 to disable. Items tagged Discontinued / Replacement Part are still excluded. Default: 0.6 (60%)."}
          default={thresholds.waiter_conversion_rate.default}
          value={currentValue('waiter_conversion_rate')}
          onChange={v => setEdit('waiter_conversion_rate', v)}
          onReset={() => resetToDefault('waiter_conversion_rate')}
          suffix="×"
          step="0.05"
        />

        <SettingRow
          label="Buy More — Max days of stock"
          help="Products with fewer days of stock than this qualify for the Buy More list. Lower values = stricter (only items near stockout). Default: 60."
          default={thresholds.buy_more_max_dos.default}
          value={currentValue('buy_more_max_dos')}
          onChange={v => setEdit('buy_more_max_dos', v)}
          onReset={() => resetToDefault('buy_more_max_dos')}
          suffix="days"
          step="5"
        />

        <SettingRow
          label="Buy More — Minimum daily velocity"
          help="Products selling slower than this won't appear on Buy More. 0.03 = roughly 1 sale every 33 days. Lower = more items flagged. Default: 0.03."
          default={thresholds.buy_more_min_velocity.default}
          value={currentValue('buy_more_min_velocity')}
          onChange={v => setEdit('buy_more_min_velocity', v)}
          onReset={() => resetToDefault('buy_more_min_velocity')}
          suffix="/day"
          step="0.01"
        />

        <SettingRow
          label="Buy More — Minimum margin"
          help="Products with margin below this percent won't appear on Buy More. Lower = include thin-margin items. Default: 10%."
          default={thresholds.buy_more_min_margin_pct.default}
          value={currentValue('buy_more_min_margin_pct')}
          onChange={v => setEdit('buy_more_min_margin_pct', v)}
          onReset={() => resetToDefault('buy_more_min_margin_pct')}
          suffix="%"
          step="1"
        />

        <SettingRow
          label="Stop Buying — Minimum days of stock"
          help="Products with more days of stock than this appear on Stop Buying. Higher = fewer items flagged as oversupply. Default: 180."
          default={thresholds.stop_buying_min_dos.default}
          value={currentValue('stop_buying_min_dos')}
          onChange={v => setEdit('stop_buying_min_dos', v)}
          onReset={() => resetToDefault('stop_buying_min_dos')}
          suffix="days"
          step="30"
        />

        <div style={{ display: 'flex', gap: 8, marginTop: 16, paddingTop: 16, borderTop: '1px solid var(--border)' }}>
          <button onClick={handleSave} disabled={!isDirty || saving} style={s.btn('primary')}>
            {saving ? 'Saving...' : 'Save Changes'}
          </button>
          <button onClick={() => setEdits({})} disabled={!isDirty} style={s.btn()}>Discard</button>
          {isDirty && (
            <span style={{ alignSelf: 'center', fontSize: 11, color: '#f59e0b', fontWeight: 600, marginLeft: 8 }}>
              • {Object.keys(edits).length} unsaved change{Object.keys(edits).length > 1 ? 's' : ''}
            </span>
          )}
        </div>
      </div>

      <BarcodeLabelDiagnostics onToast={onToast} cardStyle={s.card} btnStyle={s.btn} />

      <div style={{ ...s.card, backgroundColor: '#f8fafc' }}>
        <div style={{ fontSize: 12, fontWeight: 600, marginBottom: 8 }}>How thresholds interact</div>
        <div style={{ fontSize: 11, color: 'var(--text-light)', lineHeight: 1.6 }}>
          <strong>Order cycle</strong> + <strong>Projection multiplier</strong> together control quantities. The forecaster projects demand over <code>(cycle days × multiplier) + vendor lead time</code> and recommends ordering enough to cover that window minus what's already on hand or on order. With cycle=30 days and multiplier=1.5 you'll order roughly every 45 days plus the lead-time buffer.
          <br /><br />
          An item appears on <strong>Buy More</strong> when it passes all three Buy More tests: days-of-stock below the max, velocity above the minimum, AND margin above the minimum.
          That's why an item selling well at a thin margin won't be flagged — it passes velocity but fails margin.
          If you want that item considered, lower the margin threshold.
          <br /><br />
          <strong>Stop Buying</strong> is independent — any item with days-of-stock above its threshold appears there, regardless of velocity or margin. It's a pure "sitting on inventory" signal.
        </div>
      </div>
    </div>
  );
}

// Diagnostics for the Zebra Browser Print bridge. Lets the operator
// fire a single test label without needing to receive a real PO line —
// useful for confirming that Browser Print is running on this machine
// and the Zebra ZD420 is selected as the default printer.
function BarcodeLabelDiagnostics({ onToast, cardStyle, btnStyle }) {
  const [queuing, setQueuing] = useState(false);
  const handleTest = async () => {
    setQueuing(true);
    try {
      const res = await queueTestLabel();
      if (res.ok) {
        onToast?.('Test label queued (look for "TEST-SKU" — will print within a few seconds if the agent is running)', 'success');
      } else {
        onToast?.('Test queue failed: ' + (res.error || 'unknown error'), 'error');
      }
    } catch (e) {
      onToast?.('Test queue failed: ' + e.message, 'error');
    }
    setQueuing(false);
  };
  return (
    <div style={cardStyle}>
      <div style={{ fontSize: 15, fontWeight: 700, marginBottom: 4 }}>Barcode Label Printer</div>
      <div style={{ fontSize: 11, color: 'var(--text-muted)', marginBottom: 16 }}>
        Auto-prints a 2.25"×1.25" ZPL label on each unit received from a vendor whose
        <em> Print Labels </em>
        flag is enabled (set on the Vendors page). Architecture: TC-Planner queues label
        jobs server-side; a small Python agent on the receiving PC polls the queue and
        prints via Browser Print to your Zebra ZP505 (or any ZPL printer). The agent
        bypasses Chrome's Local Network Access restrictions because it isn't a browser.
      </div>
      <div style={{ fontSize: 11, color: 'var(--text-muted)', marginBottom: 16 }}>
        <strong>Setup (one-time):</strong>
        <ol style={{ marginTop: 6, marginBottom: 0, paddingLeft: 20 }}>
          <li>Install <strong>Zebra Browser Print</strong> on the receiving PC and select your ZP505 as default.</li>
          <li>Copy <code>agent/tc_label_agent.py</code> from the TC-Planner repo to the receiving PC.</li>
          <li>Set environment variables <code>TC_PLANNER_URL</code> and <code>TC_PLANNER_TOKEN</code>.</li>
          <li>Run <code>python tc_label_agent.py</code> — it polls every 3s and prints any queued labels.</li>
          <li>Optionally, configure it to start at logon via Task Scheduler.</li>
        </ol>
      </div>
      <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
        <button onClick={handleTest} disabled={queuing} style={btnStyle('primary')}>
          {queuing ? 'Queuing...' : 'Queue Test Label'}
        </button>
        <span style={{ fontSize: 11, color: 'var(--text-muted)' }}>
          Queues one label with sample barcode <code>0123456789012</code> and SKU <code>TEST-SKU</code>. The agent prints it within a few seconds.
        </span>
      </div>
    </div>
  );
}

function SettingRow({ label, help, value, default: defVal, onChange, onReset, suffix, step }) {
  const isModified = value !== defVal && value !== String(defVal);
  return (
    <div style={{ marginBottom: 18, paddingBottom: 14, borderBottom: '1px solid var(--border)' }}>
      <label style={s.label}>{label}</label>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 6 }}>
        <input type="number" step={step} value={value}
          onChange={e => onChange(e.target.value)}
          style={s.input} />
        {suffix && <span style={{ fontSize: 12, color: 'var(--text-muted)' }}>{suffix}</span>}
        <span style={{ fontSize: 11, color: 'var(--text-muted)', marginLeft: 8 }}>Default: {defVal}{suffix}</span>
        {isModified && (
          <button onClick={onReset}
            style={{ padding: '3px 8px', borderRadius: 4, border: '1px solid var(--border)',
              backgroundColor: 'transparent', fontSize: 10, color: 'var(--text-muted)', cursor: 'pointer' }}>
            ↺ Reset
          </button>
        )}
      </div>
      <div style={s.help}>{help}</div>
    </div>
  );
}
