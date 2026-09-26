import { useState } from 'react';
import * as api from './api';
import { packageDimensionsText } from './shippingUnits';

const keyFor=dims=>[...dims].map(Number).sort((a,b)=>a-b).map(v=>Number(v.toFixed(6))).join('x');
export default function ConfirmBoxUsage({ packages, reference, custom, onToast }) {
  const [preview,setPreview]=useState(null);
  const [shipment,setShipment]=useState(custom?'':reference);
  const [busy,setBusy]=useState(false);
  const [done,setDone]=useState(false);
  const [error,setError]=useState('');
  const grouped=new Map();
  packages.filter(p=>p.package_type==='warehouse_carton').forEach(p=>{
    const key=keyFor(p.dimensions_in);
    if(!grouped.has(key))grouped.set(key,{key,dimensions:p.dimensions_in,quantity:0});
    grouped.get(key).quantity++;
  });
  const rows=[...grouped.values()];
  if(!rows.length)return <p>No warehouse boxes to deduct: this plan uses factory packaging.</p>;
  async function review(){
    setBusy(true);setError('');
    try{setPreview(await api.getShippingCartons());}catch(e){setError(e.message);}finally{setBusy(false);}
  }
  async function confirm(){
    setBusy(true);setError('');
    try{await api.confirmShippingBoxUsage({packages,shipment_reference:shipment,revision:preview.revision});setDone(true);onToast?.('Boxes used recorded');}
    catch(e){setError(e.message);}finally{setBusy(false);}
  }
  return <section aria-label="Shipment box usage" style={{background:'#fff',border:'1px solid var(--border)',borderRadius:12,padding:18,marginBottom:18}}>
    <h3>Record boxes used</h3>
    {done?<p role="status">Boxes used recorded for {shipment}. This shipment reference cannot deduct the same boxes twice.</p>:<>
      <p>Only confirm after packing with the warehouse boxes shown below. Planning and test shipments do not change stock. Factory packaging is not deducted. This does not mark the Shopify order fulfilled.</p>
      {!preview?<button disabled={busy} onClick={review}>{busy?'Loading…':'Review boxes used'}</button>:<>
        <label>Unique shipment reference <input aria-label="Shipment reference" maxLength={150} disabled={busy||!custom} value={shipment} onChange={e=>setShipment(e.target.value)}/></label>
        {custom&&<p>For a real custom shipment, enter its unique reference. Do not confirm test plans.</p>}
        <table style={{width:'100%',textAlign:'left',margin:'12px 0'}}><thead><tr><th>Box size</th><th>On hand</th><th>Used</th><th>After confirmation</th></tr></thead><tbody>{rows.map(row=>{
          const current=preview.inventory.find(item=>item.key===row.key)?.quantity;
          return <tr key={row.key}><td>{packageDimensionsText(row.dimensions,'in')}</td><td>{current??'Not counted'}</td><td>{row.quantity}</td><td>{current==null?'Count stock first':current-row.quantity}</td></tr>;
        })}</tbody></table>
        <button disabled={busy||!shipment.trim()} onClick={confirm}>{busy?'Saving…':'Confirm boxes used'}</button>{' '}
        <button disabled={busy} onClick={review}>Refresh stock preview</button>
      </>}
      {error&&<p role="alert" style={{color:'#b91c1c'}}>{error}</p>}
    </>}
  </section>;
}
