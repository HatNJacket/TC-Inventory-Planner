import {useEffect,useState} from 'react';

export default function LettermailOrder({order,onOpenPackage}) {
  const [checks,setChecks]=useState({}),[grams,setGrams]=useState(''),[thickness,setThickness]=useState('');
  useEffect(()=>{setChecks({});setGrams('');setThickness('');},[order]);
  const warnings=order.lettermail?.warnings||[];
  const labels={pouch:'Correct pouch and protection; nothing fragile or unsuitable included.',size:'Sealed mailpiece length and width checked: 14–38 cm long and 9–27 cm wide.',review:'I reviewed every warning above and confirmed this order is suitable to send as selected.'};
  const validWeight=Number(grams)>0&&Number(grams)<=500;
  const validThickness=Number(thickness)>=0.018&&Number(thickness)<=2;
  const complete=checks.pouch&&checks.size&&validWeight&&validThickness&&(!warnings.length||checks.review);
  return <section aria-label="Lettermail packing" style={{padding:24,border:'2px solid #b45309',borderRadius:12,background:'#fffbeb',marginBottom:18}}>
    <h2 style={{marginTop:0}}>{order.name} · LETTERMAIL — NO TRACKING</h2>
    <p>Selected in Shopify: {order.shipping_method}. Use a pouch, not the carton optimizer. This checklist does not buy postage, fulfill the order, or change its shipping method.</p>
    <p>Merchandise value before discounts: {order.lettermail.merchandise_value_cad==null?'Needs review':`$${order.lettermail.merchandise_value_cad} CAD`} · Ceiling: $50 CAD</p>
    {!!warnings.length&&<div role="alert"><strong>Review needed before dispatch</strong><ul>{warnings.map(w=><li key={w}>{w}</li>)}</ul><p>If unsuitable, stop and arrange the appropriate service. Do not silently switch the customer's method.</p></div>}
    <ul>{(order.line_items||[]).filter(i=>i.requires_shipping!==false&&i.pack_quantity>0).map((i,n)=><li key={i.id||n}><button type="button" onClick={()=>onOpenPackage?.({sku:i.sku,product:i.title})}>{i.sku||'Missing SKU'}</button> × {i.pack_quantity} — {i.title}</li>)}</ul>
    {Object.entries(labels).filter(([key])=>key!=='review'||warnings.length).map(([key,label])=><label key={key} style={{display:'block',margin:'12px 0'}}><input type="checkbox" checked={!!checks[key]} onChange={e=>setChecks(c=>({...c,[key]:e.target.checked}))}/> {label}</label>)}
    <label style={{display:'block',margin:'12px 0'}}>Final sealed pouch weight (g) <input type="number" min="0" max="500" step="any" value={grams} onChange={e=>setGrams(e.target.value)}/></label>
    <label style={{display:'block',margin:'12px 0'}}>Maximum sealed pouch thickness (cm) <input type="number" min="0.018" max="2" step="any" value={thickness} onChange={e=>setThickness(e.target.value)}/></label>
    {grams!==''&&!validWeight&&<p role="alert">Enter a positive final weight no greater than 500 g.</p>}
    {thickness!==''&&!validThickness&&<p role="alert">Final thickness must be between 0.018 and 2 cm, including protection.</p>}
    <p role="status">{complete?'Checklist checked for this session — confirm postage before dispatch.':'Complete the pouch checks before dispatch.'}</p>
    <small>Checklist is not saved to packing history. It resets when the order is reloaded. Final dimensions and weight include the envelope and all protection; postage depends on the mailpiece.</small>
  </section>;
}
