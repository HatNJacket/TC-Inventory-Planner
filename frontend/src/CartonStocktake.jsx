import { useState } from 'react';
import * as api from './api';
import { packageDimensionsText } from './shippingUnits';

const action = {minHeight:46,padding:'12px 22px',borderRadius:9,border:'1px solid #cbd5e1',background:'#fff',fontSize:14,fontWeight:800,cursor:'pointer'};
const cellStyle = {padding:'10px 12px',borderBottom:'1px solid #e2e8f0'};
function countError(value) {
  if(value===''||value===undefined)return '';
  if(!String(value).trim()||!Number.isFinite(Number(value)))return 'Enter a valid number.';
  if(Number(value)<0)return 'Quantity cannot be negative. Enter 0 or more.';
  if(!Number.isInteger(Number(value)))return 'Boxes must be whole numbers; decimals are not allowed.';
  return '';
}

export default function CartonStocktake({ data, onSaved, onClose }) {
  const [counts,setCounts]=useState({});
  const [busy,setBusy]=useState(false);
  const [error,setError]=useState('');
  const rows=data.inventory;
  const counted=rows.filter(row=>counts[row.key]!==undefined&&counts[row.key]!=='');
  const invalid=counted.some(row=>countError(counts[row.key]));
  async function save(){
    if(busy||invalid||!counted.length)return;
    if(!window.confirm(`Replace stock with your actual count for ${counted.length} box sizes? Uncounted sizes will stay unchanged.`))return;
    setBusy(true);setError('');
    try{onSaved(await api.saveShippingStocktake(counted.map(row=>({dimensions:row.dimensions,quantity:Number(counts[row.key])})),data.revision));}
    catch(e){setError(e.message);}finally{setBusy(false);}
  }
  return <section aria-label="Box stocktake" style={{background:'#fff',border:'1px solid var(--border)',padding:18,borderRadius:12,marginBottom:16}}>
    <h3>Check shelf stock</h3>
    <p>Enter the total boxes you physically count. Leave unchecked sizes blank; enter 0 for an empty shelf. Pause receiving and packing these sizes while counting.</p>
    {error&&<p role="alert" style={{color:'#b91c1c'}}>{error} Your entries are kept here. If stock changed, cancel and start a fresh count.</p>}
    <div aria-label="Scrollable stock counts" tabIndex={0} style={{maxHeight:'min(45vh,420px)',overflow:'auto',border:'1px solid #e2e8f0',borderRadius:9}}>
    <table style={{width:'100%',minWidth:670,textAlign:'left',borderCollapse:'separate',borderSpacing:0,fontSize:13}}><thead><tr>{['Box size','Expected','Actual count','Difference','Last counted'].map(label=><th key={label} style={{...cellStyle,position:'sticky',top:0,background:'#f1f5f9',zIndex:1}}>{label}</th>)}</tr></thead>
      <tbody>{rows.map(row=>{
        const value=counts[row.key]??'';
        const validation=countError(value);
        const difference=value!==''&&!validation&&row.quantity!==null?Number(value)-row.quantity:null;
        return <tr key={row.key}>
          <td style={cellStyle}>{packageDimensionsText(row.dimensions,'in')}<small style={{display:'block'}}>{packageDimensionsText(row.dimensions,'cm')}</small></td>
          <td style={cellStyle}>{row.quantity??'Unknown'}</td>
          <td style={cellStyle}><input aria-label={`Actual count ${row.key}`} aria-invalid={!!validation} aria-describedby={validation?`count-error-${row.key}`:undefined} type="text" inputMode="decimal" placeholder="Not checked" value={value} disabled={busy} onChange={e=>setCounts(previous=>({...previous,[row.key]:e.target.value}))} style={{width:110,padding:10,border:`1px solid ${validation?'#dc2626':'#cbd5e1'}`,borderRadius:7}}/>{validation&&<div id={`count-error-${row.key}`} role="alert" style={{color:'#b91c1c',maxWidth:230,marginTop:5}}>{validation}</div>}</td>
          <td style={cellStyle}>{difference===null?'—':difference>0?`+${difference}`:difference}</td>
          <td style={cellStyle}>{row.last_counted?`${row.last_counted.recorded_at.replace('T',' ')} · ${row.last_counted.user_name}`:'Never recorded'}</td>
        </tr>;
      })}</tbody>
    </table></div>
    <div style={{paddingTop:14,background:'#fff'}}>
      <p aria-live="polite" style={{margin:'0 0 12px',color:invalid?'#b91c1c':'#475569'}}>{invalid?'Cannot save: correct the highlighted quantities. Use whole numbers of 0 or more.':!counted.length?'Enter at least one physical count to enable Save.':`${counted.length} sizes checked. Matching counts are recorded too.`}</p>
      <div style={{display:'flex',gap:12,flexWrap:'wrap'}}>
        <button style={{...action,background:busy||!counted.length||invalid?'#e2e8f0':'var(--green, #2aad51)',color:busy||!counted.length||invalid?'#475569':'#fff'}} disabled={busy||!counted.length||invalid} onClick={save}>{busy?'Saving…':'Save physical counts'}</button>
        <button style={action} disabled={busy} onClick={()=>{if(!counted.length||window.confirm('Discard these unsaved physical counts?'))onClose();}}>Cancel stocktake</button>
      </div>
    </div>
  </section>;
}
