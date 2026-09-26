import { useState } from 'react';
import * as api from './api';
import { packageDimensionsText } from './shippingUnits';

export default function CartonStocktake({ data, onSaved, onClose }) {
  const [counts,setCounts]=useState({});
  const [busy,setBusy]=useState(false);
  const [error,setError]=useState('');
  const rows=data.inventory;
  const counted=rows.filter(row=>counts[row.key]!==undefined&&counts[row.key]!=='');
  const invalid=counted.some(row=>!Number.isInteger(Number(counts[row.key]))||Number(counts[row.key])<0);
  async function save(){
    if(!window.confirm(`Replace stock with your actual count for ${counted.length} box sizes? Uncounted sizes will stay unchanged.`))return;
    setBusy(true);setError('');
    try{onSaved(await api.saveShippingStocktake(counted.map(row=>({dimensions:row.dimensions,quantity:Number(counts[row.key])})),data.revision));}
    catch(e){setError(e.message);}finally{setBusy(false);}
  }
  return <section aria-label="Box stocktake" style={{background:'#fff',border:'1px solid var(--border)',padding:18,borderRadius:12,marginBottom:16}}>
    <h3>Check shelf stock</h3>
    <p>Enter the total boxes you physically count. Leave unchecked sizes blank; enter 0 for an empty shelf. Pause receiving and packing these sizes while counting.</p>
    {error&&<p role="alert" style={{color:'#b91c1c'}}>{error} Your entries are kept here. If stock changed, cancel and start a fresh count.</p>}
    <table style={{width:'100%',textAlign:'left'}}><thead><tr>{['Box size','Expected','Actual count','Difference','Last counted'].map(label=><th key={label}>{label}</th>)}</tr></thead>
      <tbody>{rows.map(row=>{
        const value=counts[row.key]??'';
        const difference=value!==''&&row.quantity!==null?Number(value)-row.quantity:null;
        return <tr key={row.key}>
          <td>{packageDimensionsText(row.dimensions,'in')}<small style={{display:'block'}}>{packageDimensionsText(row.dimensions,'cm')}</small></td>
          <td>{row.quantity??'Unknown'}</td>
          <td><input aria-label={`Actual count ${row.key}`} type="number" min="0" step="1" placeholder="Not checked" value={value} disabled={busy} onChange={e=>setCounts(previous=>({...previous,[row.key]:e.target.value}))} style={{width:110,padding:8,margin:'8px 0'}}/></td>
          <td>{difference===null?'—':difference>0?`+${difference}`:difference}</td>
          <td>{row.last_counted?`${row.last_counted.recorded_at.replace('T',' ')} · ${row.last_counted.user_name}`:'Never recorded'}</td>
        </tr>;
      })}</tbody>
    </table>
    <p>{counted.length} sizes checked. Matching counts are recorded too.</p>
    <button disabled={busy||!counted.length||invalid} onClick={save}>{busy?'Saving…':'Save physical counts'}</button>{' '}
    <button disabled={busy} onClick={()=>{if(!counted.length||window.confirm('Discard these unsaved physical counts?'))onClose();}}>Cancel stocktake</button>
  </section>;
}
