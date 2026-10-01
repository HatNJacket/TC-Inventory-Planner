import { useEffect, useRef, useState } from 'react';
import * as api from './api';
import BundleBreakdown from './BundleBreakdown';
import { dimensionToInches, dimensionForInput, packageDimensionsText } from './shippingUnits';

const input={width:'100%',boxSizing:'border-box',padding:10,border:'1px solid #cbd5e1',borderRadius:8};
const button={padding:'10px 15px',border:'1px solid #cbd5e1',borderRadius:8,background:'#fff',fontWeight:750,cursor:'pointer'};
const card={background:'#fff',border:'1px solid var(--border)',borderRadius:12,padding:18,minWidth:0};
const blank=()=>({sku:'',product_name:'',part:'',dimensions_in:['','',''],weight_kg:'',verification_status:'Shopify — Unverified',shipping_behavior:'standard',packages_per_unit:1,source_parts_per_unit:1,notes:'',carrier_notes:''});
const labels={all:'All records',verified:'Verified dimensions',unverified:'Unverified dimensions',missing_dimensions:'Missing / invalid dimensions',missing_weights:'Missing weights',needs_review:'Needs review',ready:'Ready package records'};

export default function PackageDatabase({onToast,bundleVersion,onChanged}) {
  const [data,setData]=useState(null),[query,setQuery]=useState(''),[filter,setFilter]=useState('all'),[offset,setOffset]=useState(0);
  const [edit,setEdit]=useState(blank),[unit,setUnit]=useState('cm'),[dims,setDims]=useState(['','','']);
  const [loading,setLoading]=useState(false),[busy,setBusy]=useState(false),[error,setError]=useState('');
  const [checked,setChecked]=useState(false),[reason,setReason]=useState('');
  const baseline=useRef(JSON.stringify(blank())),sequence=useRef(0);
  const dirty=JSON.stringify(edit)!==baseline.current;
  async function load(q=query,f=filter,start=offset){const id=++sequence.current;setLoading(true);
    try{const result=await api.getShippingPackageDatabase(q,50,f,start);if(id===sequence.current){setData(result);setFilter(f);setOffset(start);}}
    catch(e){setError(e.message);}finally{if(id===sequence.current)setLoading(false);}}
  useEffect(()=>{load();},[bundleVersion]);
  function select(record,ask=true){if(ask&&dirty&&!window.confirm('Discard unsaved package changes?'))return;
    const next=record?{...record,dimensions_in:[...(record.dimensions_in||['','',''])]}:blank();
    setEdit(next);baseline.current=JSON.stringify(next);setDims(next.dimensions_in.map(v=>dimensionForInput(v,unit)));setChecked(false);setReason('');setError('');}
  function field(name,value){setEdit(e=>({...e,[name]:value}));setChecked(false);}
  function dimension(i,value){setDims(d=>d.map((v,j)=>i===j?value:v));setEdit(e=>({...e,dimensions_in:e.dimensions_in.map((v,j)=>i===j?dimensionToInches(value,unit):v)}));setChecked(false);}
  function changeUnit(next){setDims(edit.dimensions_in.map(v=>dimensionForInput(v,next)));setUnit(next);}
  const validDims=edit.dimensions_in.length===3&&edit.dimensions_in.every(v=>v!==''&&Number.isFinite(Number(v))&&Number(v)>0);
  const validWeight=edit.weight_kg!==''&&edit.weight_kg!=null&&Number.isFinite(Number(edit.weight_kg))&&Number(edit.weight_kg)>0;
  const old=JSON.parse(baseline.current);
  const warnings=[];
  if(validDims&&(Math.min(...edit.dimensions_in.map(Number))<0.1||Math.max(...edit.dimensions_in.map(Number))>100))warnings.push('Unusual size: check centimetres versus inches.');
  if(old.id&&validDims&&old.dimensions_in?.every(v=>Number(v)>0)){
    const a=edit.dimensions_in.map(Number).sort((x,y)=>x-y),b=old.dimensions_in.map(Number).sort((x,y)=>x-y);
    if(a.some((v,i)=>v/b[i]>=2||v/b[i]<=0.5))warnings.push('A dimension changed by at least a factor of two. Check the units before saving.');}
  if(validWeight&&(Number(edit.weight_kg)>100||(Number(old.weight_kg)>0&&(Number(edit.weight_kg)/Number(old.weight_kg)>=2||Number(edit.weight_kg)/Number(old.weight_kg)<=0.5))))warnings.push('Unusual weight or large weight change. Check kilograms and packaged weight.');
  async function save(){setBusy(true);setError('');try{
    const result=await api.saveShippingPackageRecord({...edit,dimensions_in:edit.dimensions_in.map(Number),weight_kg:edit.weight_kg===''||edit.weight_kg==null?null:Number(edit.weight_kg),packages_per_unit:Number(edit.packages_per_unit||1)});
    select(result.record,false);onToast?.('Package saved. Verification is a separate action. Reload any shipment to use the changes.');onChanged?.();await load();
  }catch(e){setError(e.message);}finally{setBusy(false);}}
  async function review(action){setBusy(true);setError('');try{
    const result=await api.reviewShippingPackage(edit.id,{revision:edit._revision,action,reason,physically_checked:checked});
    select(result.record,false);onToast?.(action==='verify'?'Physical verification recorded':'Package flagged for review');onChanged?.();await load();
  }catch(e){setError(e.message);}finally{setBusy(false);}}
  async function remove(){if(!window.confirm(`Delete ${edit.sku}? This removes this package record and its attached change history.`))return;
    setBusy(true);try{await api.deleteShippingPackageRecord(edit.id);select(null,false);onChanged?.();await load();}catch(e){setError(e.message);}finally{setBusy(false);}}
  function restore(before){if(!before)return;setEdit({...before,id:edit.id,_revision:edit._revision,change_history:edit.change_history});setDims((before.dimensions_in||['','','']).map(v=>dimensionForInput(v,unit)));setChecked(false);onToast?.('Previous values loaded into the editor. Review and save; verification will be reset if measurements changed.');}
  const health=data?.health;
  return <div>
    {health&&<section style={{...card,marginBottom:18}} aria-label="Package database health">
      <h2 style={{marginTop:0}}>Package database health</h2>
      <p><strong>{health.verified_percent}% dimensions verified</strong> · {health.ready_percent}% ready with verified dimensions and a stored weight.</p>
      <div style={{display:'grid',gridTemplateColumns:'repeat(auto-fit,minmax(155px,1fr))',gap:10}}>{Object.entries(labels).map(([id,label])=><button key={id} style={{...button,textAlign:'left',borderColor:filter===id?'#20813a':'#cbd5e1',background:filter===id?'#edf8ef':'#fff'}} disabled={loading||busy} onClick={()=>{setQuery('');load('',id,0);}}><span style={{display:'block',fontSize:23,fontWeight:900}}>{id==='all'?health.registry_records:health[id]}</span>{label}</button>)}</div>
      <p style={{fontSize:12}}>Counts cover the entire local registry, not just these 50 results. Physical package records (including separate parts): {health.total}. Bundle-parent measurements are excluded from health counts. {health.ready_bundles} of {health.bundles} bundles have ready component records. Missing weights and review flags may overlap other counts. Products absent from this registry are not counted.</p>
    </section>}
    <BundleBreakdown bundles={data?.bundles} registry/>
    {error&&<p role="alert" style={{color:'#b91c1c'}}>{error}</p>}
    <div style={{display:'grid',gridTemplateColumns:'repeat(auto-fit,minmax(min(100%,390px),1fr))',gap:18,alignItems:'start'}}>
      <section style={card}>
        <form onSubmit={e=>{e.preventDefault();load(query,filter,0);}} style={{display:'flex',gap:8}}><input aria-label="Search package database" placeholder="Search SKU, product, or package part" style={input} value={query} onChange={e=>setQuery(e.target.value)}/><button style={button} disabled={busy||loading}>{loading?'Searching…':'Search'}</button></form>
        <p style={{fontSize:12}}>{data?.count??0} matching records · {labels[filter]} · showing up to 50</p>
        <div style={{maxHeight:650,overflow:'auto'}}><table style={{width:'100%',fontSize:12,textAlign:'left'}}><thead><tr><th>SKU / part</th><th>Dimensions</th><th>Status</th><th/></tr></thead><tbody>{(data?.records||[]).map(r=><tr key={r.id} style={{background:edit.id===r.id?'#edf8ef':''}}>
          <td style={{padding:'12px 4px'}}><strong>{r.sku}</strong><div>{r.part||r.product_name}</div></td><td>{packageDimensionsText(r.dimensions_in,unit)}</td><td>{r.verification_status}{r.needs_review&&<div style={{color:'#92400e'}}>{r.review_reason||'Measurements need review'}</div>}</td><td><button style={button} disabled={busy} onClick={()=>select(r)} aria-label={`Edit package ${r.sku}${r.part?' '+r.part:''}`}>Edit</button></td>
        </tr>)}</tbody></table></div>
        <div style={{display:'flex',gap:8,marginTop:14}}><button style={button} disabled={busy||loading||!offset} onClick={()=>load(query,filter,Math.max(0,offset-50))}>Previous</button><button style={button} disabled={busy||loading||!data?.truncated} onClick={()=>load(query,filter,offset+50)}>Next</button><button style={button} disabled={busy||loading} onClick={()=>load()}>Refresh</button></div>
      </section>
      <section style={card} aria-label="Package editor">
        <div style={{display:'flex',justifyContent:'space-between'}}><h3 style={{marginTop:0}}>{edit.id?'Edit package record':'Add package record'}</h3><button style={button} disabled={busy} onClick={()=>select(null)}>New</button></div>
        <fieldset disabled={busy} style={{padding:0,margin:0,border:0,display:'grid',gap:12}}>
          {['sku','product_name','part'].map(name=><label key={name}>{name==='sku'?'SKU':name==='part'?'Part':'Product name'}<input aria-label={`Package ${name}`} style={input} value={edit[name]||''} onChange={e=>field(name,e.target.value)}/></label>)}
          <label>Dimension units<select aria-label="Dimension units" style={input} value={unit} onChange={e=>changeUnit(e.target.value)}><option value="cm">Centimetres (cm)</option><option value="in">Inches (in)</option></select></label>
          <div style={{display:'grid',gridTemplateColumns:'repeat(3,1fr)',gap:8}}>{['Length','Width','Height'].map((name,i)=><label key={name}>{name} {unit}<input aria-label={`Package ${name.toLowerCase()}`} type="number" step="any" style={input} value={dims[i]??''} onChange={e=>dimension(i,e.target.value)}/></label>)}</div>
          <div style={{fontSize:12}}>Equivalent: {packageDimensionsText(edit.dimensions_in,unit==='cm'?'in':'cm')}</div>
          <label>Packaged weight (kg)<input aria-label="Package weight" type="number" step="any" style={input} value={edit.weight_kg??''} onChange={e=>field('weight_kg',e.target.value)}/></label>
          <label>Copies per ordered unit<input aria-label="Package copies" type="number" step="1" min="1" style={input} value={edit.packages_per_unit??1} onChange={e=>field('packages_per_unit',e.target.value)}/></label>
          <label>Shipping behavior<select style={input} value={edit.shipping_behavior||'standard'} onChange={e=>field('shipping_behavior',e.target.value)}><option value="standard">Standard</option><option value="carrier_ready">Carrier-ready</option><option value="accessory_carrier">Accessory carrier</option><option value="must_ship_alone">Must ship alone</option></select></label>
          {edit.shipping_behavior==='accessory_carrier'&&<label>Carrier notes<textarea style={input} value={edit.carrier_notes||''} onChange={e=>field('carrier_notes',e.target.value)}/></label>}
          <label>Notes<textarea style={input} value={edit.notes||''} onChange={e=>field('notes',e.target.value)}/></label>
        </fieldset>
        {!!warnings.length&&<div role="alert" style={{background:'#fff7dd',padding:12,marginTop:12}}>{warnings.map(w=><p key={w}>{w}</p>)}Saving these values will mark the record Needs review.</div>}
        <p style={{fontSize:12}}>Saving does not verify measurements. Changes to dimensions, weight, SKU, part, copies, or shipping behavior reset verification. No time-based expiry.</p>
        <button style={{...button,background:'#20813a',color:'#fff'}} disabled={busy} onClick={save}>{busy?'Working…':'Save Package'}</button>
        {edit.id&&<>
          <section style={{borderTop:'1px solid #cbd5e1',marginTop:18,paddingTop:14}} aria-label="Physical verification">
            <strong>{edit.verification_status}</strong><p style={{fontSize:12}}>Last recorded verification: {edit.last_verified||'Not recorded'} · {edit.measured_by||'Verifier not recorded'}</p>
            {edit.review_reason&&<p role="alert">Review requested: {edit.review_reason}</p>}
            {(edit.review_warnings||[]).map(w=><p key={w} style={{color:'#92400e'}}>{w}</p>)}
            {dirty&&<p>Save or discard edits before recording verification or a review flag.</p>}
            <label style={{display:'block'}}><input type="checkbox" checked={checked} disabled={dirty||busy} onChange={e=>setChecked(e.target.checked)}/> I physically checked this packaged product’s dimensions and weight.</label>
            <label style={{display:'block',marginTop:12}}>Review / verification note<textarea aria-label="Verification note" style={input} value={reason} disabled={busy} onChange={e=>setReason(e.target.value)}/></label>
            {(!validDims||!validWeight)&&<p style={{color:'#92400e'}}>Valid dimensions and a positive package weight are required to verify.</p>}
            <div style={{display:'flex',gap:8,flexWrap:'wrap',marginTop:12}}><button style={button} disabled={busy||dirty||!checked||!validDims||!validWeight||((edit.needs_review||edit.review_warnings?.length)&&!reason.trim())} onClick={()=>review('verify')}>Mark physically verified</button><button style={button} disabled={busy||dirty||!reason.trim()} onClick={()=>review('flag')}>Flag for review</button></div>
            <p style={{fontSize:12}}>Flagged records need a note explaining the checks before they can be verified again. This is a staff confirmation, not an independent measurement check.</p>
          </section>
          <details style={{marginTop:18}}><summary>Change history ({edit.change_history?.length||0})</summary>
            {!edit.change_history?.length&&<p>No recorded edits yet. Historical verification labels are retained, not retroactively certified.</p>}
            <div style={{maxHeight:320,overflow:'auto'}}>{[...(edit.change_history||[])].reverse().map((event,i)=><article key={i} style={{borderBottom:'1px solid #cbd5e1',padding:'10px 0',fontSize:12}}><strong>{event.action} · {event.by}</strong><div>{event.at}</div><p>{event.reason}</p><div>Before: {packageDimensionsText(event.before?.dimensions_in,'cm')} · {event.before?.weight_kg??'—'} kg · {event.before?.verification_status||'New record'}</div><div>After: {packageDimensionsText(event.after?.dimensions_in,'cm')} · {event.after?.weight_kg??'—'} kg · {event.after?.verification_status}</div>{event.before&&<button style={{...button,marginTop:8}} disabled={busy} onClick={()=>{if(!dirty||window.confirm('Replace unsaved edits with these previous values?'))restore(event.before);}}>Use previous values</button>}</article>)}</div>
          </details>
          <button style={{...button,color:'#b91c1c',marginTop:18}} disabled={busy} onClick={remove}>Delete package record</button>
        </>}
      </section>
    </div>
  </div>;
}
