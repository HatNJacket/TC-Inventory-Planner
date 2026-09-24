import { useEffect, useState } from 'react';
import * as api from './api';

export default function ShippingSignIn({ onSignIn }) {
  const [users, setUsers] = useState([]);
  const [name, setName] = useState('');
  const [initials, setInitials] = useState('');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  async function load() {
    setLoading(true); setError('');
    try { setUsers((await api.getShippingUsers()).users); }
    catch (e) { setError(e.message); }
    finally { setLoading(false); }
  }
  useEffect(() => { load(); }, []);
  async function create(e) {
    e.preventDefault(); setSaving(true); setError('');
    try { onSignIn((await api.createShippingUser({ name, initials })).user); }
    catch (e) { setError(e.message); }
    finally { setSaving(false); }
  }
  const input = { width: '100%', boxSizing: 'border-box', padding: 10, border: '1px solid var(--border)', borderRadius: 8, marginTop: 5 };
  const button = { padding: '12px 16px', border: '1px solid var(--border)', borderRadius: 8, cursor: 'pointer', background: '#fff', textAlign: 'left' };
  return <section aria-labelledby="shipping-sign-in-title" style={{ maxWidth: 620, margin: '45px auto', padding: 30, background: '#fff', border: '1px solid var(--border)', borderRadius: 14 }}>
    <p style={{ color: 'var(--green)', fontWeight: 800 }}>Warehouse identity</p>
    <h2 id="shipping-sign-in-title">Who is using Shipping Tools?</h2>
    <p>Select your profile each time you enter Shipping. Your name is attached to packing observations, carton counts, and new measurements.</p>
    {error && <p role="alert" style={{ color: '#b91c1c' }}>{error} <button onClick={load}>Retry</button></p>}
    {loading ? <p>Loading warehouse users…</p> : <div style={{ display: 'grid', gap: 8, margin: '20px 0' }}>
      {users.map(user => <button key={user.id} disabled={saving} style={button} onClick={() => onSignIn(user)}><strong style={{ color: 'var(--green)', marginRight: 12 }}>{user.initials}</strong><strong>{user.name}</strong><span style={{ display: 'block', marginTop: 4, fontSize: 12 }}>Sign in as this user</span></button>)}
      {!users.length && <p>No warehouse users yet. Add the first profile below.</p>}
    </div>}
    <form onSubmit={create} style={{ display: 'grid', gap: 12, borderTop: '1px solid var(--border)', paddingTop: 20 }}>
      <strong>Add warehouse user</strong>
      <label>Name<input autoFocus required maxLength={120} value={name} onChange={e => setName(e.target.value)} style={input} /></label>
      <label>Initials (optional)<input maxLength={12} value={initials} onChange={e => setInitials(e.target.value)} style={input} /></label>
      <button disabled={saving || loading || !name.trim()} style={{ ...button, background: 'var(--green)', color: '#fff', textAlign: 'center', fontWeight: 800 }}>{saving ? 'Saving…' : 'Add & Sign In'}</button>
    </form>
  </section>;
}
