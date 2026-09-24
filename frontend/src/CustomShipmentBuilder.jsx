import { useRef, useState } from 'react';
import * as api from './api';

const input = { padding: '9px 10px', border: '1px solid var(--border)', borderRadius: 8, fontSize: 13 };
const button = { ...input, background: '#fff', cursor: 'pointer', fontWeight: 700 };

export default function CustomShipmentBuilder({ onShipment, onChange, busy }) {
  const [query, setQuery] = useState('');
  const [results, setResults] = useState(null);
  const [items, setItems] = useState([]);
  const [reference, setReference] = useState('');
  const [searching, setSearching] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const sequence = useRef(0);
  const disabled = busy || loading;
  async function search(e) {
    e.preventDefault();
    if (!query.trim()) return;
    const id = ++sequence.current;
    setSearching(true); setError('');
    try {
      const data = await api.getShippingPackageDatabase(query.trim(), 50);
      if (id === sequence.current) {
        const unique = new Map();
        data.records.forEach(record => { const key = record.sku.toLowerCase(); if (!unique.has(key)) unique.set(key, record); });
        setResults({ records: [...unique.values()], truncated: data.truncated });
      }
    } catch (e) { if (id === sequence.current) setError(e.message); }
    finally { if (id === sequence.current) setSearching(false); }
  }
  function update(next) { setItems(next); setError(''); onChange(); }
  function add(record) {
    const index = items.findIndex(item => item.sku.toLowerCase() === record.sku.toLowerCase());
    update(index < 0 ? [...items, { sku: record.sku, name: record.product_name, quantity: 1 }]
      : items.map((item, i) => i === index ? { ...item, quantity: Number(item.quantity) + 1 } : item));
  }
  async function load(e) {
    e.preventDefault(); setLoading(true); setError('');
    try {
      const result = await api.loadCustomShippingShipment(items.map(({sku,quantity}) => ({sku,quantity:Number(quantity)})), reference.trim() || 'Custom shipment');
      await onShipment(result);
    } catch (e) { setError(e.message); }
    finally { setLoading(false); }
  }
  const valid = items.length > 0 && items.length <= 50 && items.every(item => Number.isInteger(Number(item.quantity)) && Number(item.quantity) > 0 && Number(item.quantity) <= 100) && items.reduce((sum,item)=>sum+Number(item.quantity),0) <= 100;
  return <section style={{ background:'#fff', border:'1px solid var(--border)', borderRadius:12, padding:20, marginBottom:18 }}>
    <h2 style={{marginTop:0}}>Build a custom shipment</h2>
    <p>Search your Package Database by SKU or product name. Each product includes all its physical packages. No Shopify order is required.</p>
    <form onSubmit={search} style={{display:'flex',gap:8}}>
      <input aria-label="Search shipment SKU or product" placeholder="Search SKU or product name" value={query} onChange={e=>setQuery(e.target.value)} style={{...input,flex:1}} />
      <button disabled={searching||disabled||!query.trim()} style={button}>{searching?'Searching…':'Search SKUs'}</button>
    </form>
    {error&&<p role="alert" style={{color:'#b91c1c'}}>{error}</p>}
    {results&&<div style={{maxHeight:260,overflow:'auto',marginTop:12}}>
      {!results.records.length&&<p>No matching SKUs. Add the product’s dimensions in Package Database first.</p>}
      {results.truncated&&<p>Showing matches from the first 50 package records. Refine your search to find a specific SKU.</p>}
      {results.records.map(record=><div key={record.sku} style={{display:'flex',alignItems:'center',gap:12,padding:'8px 0',borderBottom:'1px solid var(--border)'}}><div style={{flex:1}}><strong>{record.sku}</strong> · {record.product_name}</div><button type="button" disabled={disabled} onClick={()=>add(record)} style={button} aria-label={`Add ${record.sku}`}>Add</button></div>)}
    </div>}
    <form onSubmit={load} style={{marginTop:20}}>
      <label>Shipment reference (optional)<input maxLength={80} value={reference} onChange={e=>{setReference(e.target.value);onChange();}} disabled={disabled} placeholder="Test shipment" style={{...input,display:'block',marginTop:5,width:'100%',boxSizing:'border-box'}} /></label>
      {items.map(item=><div key={item.sku} style={{display:'flex',alignItems:'center',gap:12,marginTop:12}}>
        <span style={{flex:1}}><strong>{item.sku}</strong> · {item.name}</span>
        <label>Qty <input aria-label={`Quantity for ${item.sku}`} type="number" min="1" max="100" step="1" value={item.quantity} disabled={disabled} onChange={e=>update(items.map(row=>row.sku===item.sku?{...row,quantity:e.target.value}:row))} style={{...input,width:70}} /></label>
        <button type="button" disabled={disabled} onClick={()=>update(items.filter(row=>row.sku!==item.sku))} style={button} aria-label={`Remove ${item.sku}`}>Remove</button>
      </div>)}
      {!!items.length&&!valid&&<p role="alert">Use positive whole quantities, up to 50 SKUs and 100 product units.</p>}
      <button disabled={disabled||!valid} style={{...button,background:'var(--green)',color:'#fff',marginTop:16}}>{loading?'Loading shipment…':'Load Custom Shipment'}</button>
      <p style={{fontSize:12,color:'var(--text-light)'}}>Load your items, then use Build Packing Plan below. Editing items clears the previous plan.</p>
    </form>
  </section>;
}
