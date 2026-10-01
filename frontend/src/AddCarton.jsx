import { useState } from 'react';
import * as api from './api';
import { dimensionToInches, dimensionForInput } from './shippingUnits';

const input={width:'100%',boxSizing:'border-box',padding:10,border:'1px solid #cbd5e1',borderRadius:8};
const button={padding:'12px 20px',border:'1px solid #cbd5e1',borderRadius:8,background:'#fff',fontWeight:800,cursor:'pointer'};
export default function AddCarton({revision,onSaved,onClose,onBusy}) {
  const [dims,setDims]=useState(['','','']), [unit,setUnit]=useState('cm');
  const [quantity,setQuantity]=useState(''), [minimum,setMinimum]=useState(''), [target,setTarget]=useState('');
  const [saving,setSaving]=useState(false), [error,setError]=useState('');
  const whole=v=>v.trim()===''||(Number.isSafeInteger(Number(v))&&Number(v)>=0);
  const problem=dims.some(v=>v.trim()===''||!Number.isFinite(Number(v))||Number(v)<=0)?'Enter a positive length, width, and height.':
    ![quantity,minimum,target].every(whole)?'Stock, minimum, and target must be whole numbers of zero or more.':
    (minimum==='')!==(target==='')||(minimum!==''&&Number(target)<=Number(minimum))?'Enter both minimum and target, with target greater than minimum, or leave both blank.':'';
  function units(next){setDims(dims.map(v=>dimensionForInput(dimensionToInches(v,unit),next)));setUnit(next);setError('');}
  async function save(e){e.preventDefault();if(problem||saving)return;setSaving(true);onBusy(true);setError('');
    try{const result=await api.addShippingCarton({dimensions:dims.map(Number),unit,quantity:quantity===''?null:Number(quantity),minimum:minimum===''?null:Number(minimum),target:target===''?null:Number(target),revision});onSaved(result);}
    catch(e){setError(e.message);}finally{setSaving(false);onBusy(false);}}
  return <section aria-label="Add new carton" style={{padding:20,border:'1px solid #b7e4c7',borderRadius:12,background:'#f4fbf6',marginBottom:18}}>
    <h3 style={{marginTop:0}}>Add new carton</h3>
    <p>Use the carton’s usable <strong>inside dimensions</strong>, not outside measurements. Enter total individual boxes for opening stock.</p>
    <form onSubmit={save}>
      <fieldset disabled={saving} style={{border:0,padding:0,margin:0}}>
        <label>Dimension units<select aria-label="New carton units" style={{...input,width:180,display:'block',margin:'6px 0 12px'}} value={unit} onChange={e=>units(e.target.value)}><option value="cm">Centimetres (cm)</option><option value="in">Inches (in)</option></select></label>
        <div style={{display:'grid',gridTemplateColumns:'repeat(auto-fit,minmax(150px,1fr))',gap:12}}>
          {['Length','Width','Height'].map((label,i)=><label key={label}>{label} ({unit})<input aria-label={`New carton ${label.toLowerCase()}`} style={input} type="number" step="any" value={dims[i]} onChange={e=>{setDims(dims.map((v,j)=>i===j?e.target.value:v));setError('');}}/></label>)}
        </div>
        <div style={{display:'grid',gridTemplateColumns:'repeat(auto-fit,minmax(150px,1fr))',gap:12,marginTop:14}}>
          <label>Opening stock<input aria-label="New carton opening stock" placeholder="Not counted" style={input} type="number" step="1" value={quantity} onChange={e=>setQuantity(e.target.value)}/></label>
          <label>Minimum<input aria-label="New carton minimum" placeholder="Off" style={input} type="number" step="1" value={minimum} onChange={e=>setMinimum(e.target.value)}/></label>
          <label>Restock target<input aria-label="New carton target" placeholder="Order up to" style={input} type="number" step="1" value={target} onChange={e=>setTarget(e.target.value)}/></label>
        </div>
        <p style={{fontSize:12}}>Blank stock means not counted; zero means out of stock. Leave both reorder fields blank to disable alerts. After creation, change quantities through Check shelf stock or stock imports.</p>
      </fieldset>
      {problem&&<p role="status" style={{color:'#92400e'}}>{problem}</p>}
      {error&&<p role="alert" style={{color:'#b91c1c'}}>{error}</p>}
      <div style={{display:'flex',gap:10,marginTop:16}}><button style={{...button,background:'#20813a',color:'#fff',opacity:problem||saving?0.7:1}} disabled={!!problem||saving}>{saving?'Saving…':'Save new carton'}</button><button type="button" style={button} disabled={saving} onClick={onClose}>Cancel</button></div>
    </form>
  </section>;
}
