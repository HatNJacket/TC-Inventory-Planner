import { useEffect, useRef, useState } from 'react';
import * as api from './api';

const field = {padding:10,border:'1px solid #cbd5e1',borderRadius:8,fontSize:14,boxSizing:'border-box',width:'100%'};
const button = {padding:'11px 16px',border:'1px solid #cbd5e1',borderRadius:8,background:'#fff',fontWeight:750,cursor:'pointer'};
const card = {padding:18,background:'#fff',border:'1px solid var(--border)',borderRadius:12,minWidth:0};
const blank = () => ({sku:'',components:[{sku:'',quantity:'1'}]});
const key = value => value.trim().toLowerCase();

export default function BundleManager({ onChanged }) {
  const [data,setData]=useState(null), [query,setQuery]=useState(''), [filter,setFilter]=useState(''), [offset,setOffset]=useState(0);
  const [draft,setDraft]=useState(blank), [original,setOriginal]=useState(null), [revision,setRevision]=useState(null);
  const [loading,setLoading]=useState(false), [saving,setSaving]=useState(false), [error,setError]=useState(''), [notice,setNotice]=useState('');
  const [search,setSearch]=useState(''), [matches,setMatches]=useState([]), [searching,setSearching]=useState(false), [searchNote,setSearchNote]=useState('');
  const [warnings,setWarnings]=useState([]);
  const baseline=useRef(JSON.stringify(blank())), listSequence=useRef(0), searchSequence=useRef(0);
  const dirty=JSON.stringify(draft)!==baseline.current;
  async function load(q=filter, start=offset) {
    const sequence=++listSequence.current;setLoading(true);
    try {const result=await api.getShippingBundles(q,start);if(sequence===listSequence.current){setData(result);setFilter(q);setOffset(start);}return result;}
    catch(e){setError(e.message);return null;}
    finally{if(sequence===listSequence.current)setLoading(false);}
  }
  useEffect(()=>{load('',0).then(result=>{if(result)setRevision(result.revision);});},[]);
  function reset(bundle=null) {
    if(dirty&&!window.confirm('Discard your unsaved bundle changes?'))return;
    const next=bundle?{sku:bundle.sku,components:bundle.components.map(c=>({...c,quantity:String(c.quantity)}))}:blank();
    setDraft(next);baseline.current=JSON.stringify(next);setOriginal(bundle?.sku??null);setRevision(data?.revision);
    setWarnings(bundle?.warnings||[]);setError('');setNotice('');setMatches([]);setSearch('');setSearchNote('');setSearching(false);searchSequence.current++;
  }
  function component(index, name, value){setDraft(d=>({...d,components:d.components.map((c,i)=>i===index?{...c,[name]:value}:c)}));setWarnings([]);}
  const problem=!key(draft.sku)?'Enter the bundle SKU.':draft.sku.length>100?'Bundle SKU must be at most 100 characters.':
    !draft.components.length?'Add at least one component.':draft.components.some(c=>!key(c.sku)||c.sku.length>100)?'Every component needs a SKU of up to 100 characters.':
    draft.components.some(c=>!Number.isInteger(Number(c.quantity))||Number(c.quantity)<1||Number(c.quantity)>100)?'Component quantities must be whole numbers from 1 to 100.':
    draft.components.some(c=>key(c.sku)===key(draft.sku))?'A bundle cannot contain itself.':
    new Set(draft.components.map(c=>key(c.sku))).size!==draft.components.length?'Each component SKU must appear once. Increase its quantity instead.':'';
  async function searchComponents(e){
    e.preventDefault();const sequence=++searchSequence.current;setSearching(true);setSearchNote('');
    try{const result=await api.getShippingPackageDatabase(search.trim());if(sequence===searchSequence.current){const unique=new Map(result.records.map(r=>[key(r.sku),r]));setMatches([...unique.values()]);setSearchNote(result.truncated?'Showing the first 50 package records. Refine your search.':!unique.size?'No matches. You may enter the exact component SKU below and add its measurements later.':'');}}
    catch(e){if(sequence===searchSequence.current)setSearchNote(e.message);}
    finally{if(sequence===searchSequence.current)setSearching(false);}
  }
  function addSku(sku=''){
    setDraft(d=>{const empty=d.components.findIndex(c=>!c.sku.trim());if(empty>=0&&sku)return {...d,components:d.components.map((c,i)=>i===empty?{sku,quantity:'1'}:c)};
      return {...d,components:[...d.components,{sku,quantity:'1'}]};});setWarnings([]);
  }
  async function save(e){
    e.preventDefault();if(problem||saving||!revision)return;setSaving(true);setError('');setNotice('');
    try{const result=await api.saveShippingBundle({sku:draft.sku.trim(),components:draft.components.map(c=>({sku:c.sku.trim(),quantity:Number(c.quantity)}))},revision,original);
      const next={sku:result.bundle.sku,components:result.bundle.components.map(c=>({...c,quantity:String(c.quantity)}))};
      setDraft(next);baseline.current=JSON.stringify(next);setOriginal(result.bundle.sku);setRevision(result.revision);setWarnings(result.bundle.warnings||[]);
      setNotice('Bundle saved. Reload your order or custom shipment to use the updated mapping.');onChanged?.();await load();
    }catch(e){setError(e.message);}finally{setSaving(false);}
  }
  async function remove(){
    if(!original||!window.confirm(`Remove the mapping for ${original}? Component package records stay unchanged. Future packing will use the parent SKU’s own package record, if present. Unsaved edits will be discarded.`))return;
    setSaving(true);setError('');
    try{await api.deleteShippingBundle(original,revision);const next=blank();setDraft(next);baseline.current=JSON.stringify(next);setOriginal(null);setWarnings([]);onChanged?.();
      const result=await load();if(result)setRevision(result.revision);setNotice('Mapping removed. Reload any order before planning.');
    }catch(e){setError(e.message);}finally{setSaving(false);}
  }
  async function refresh(){const result=await load();if(result){setRevision(result.revision);setNotice('List refreshed. Your draft is retained; compare it with the current mapping before saving.');}}
  return <div>
    <h2 style={{marginTop:0}}>Manage bundles</h2>
    <p>For separately packaged products only. Set quantities <strong>per one bundle sold</strong>. Combined retail packages should use their own Package Database record instead.</p>
    <p style={{fontSize:12,color:'#475569'}}>Mappings are saved on this planner’s backend, not in Shopify. New bundles are not detected automatically.</p>
    {error&&<p role="alert" style={{color:'#b91c1c'}}>{error}</p>}
    {notice&&<p role="status" style={{padding:12,background:'#edf8ef',borderRadius:8}}>{notice}</p>}
    <div style={{display:'grid',gridTemplateColumns:'repeat(auto-fit,minmax(min(100%,360px),1fr))',gap:18,alignItems:'start'}}>
      <section style={card} aria-label="Saved bundles">
        <div style={{display:'flex',gap:8,justifyContent:'space-between',marginBottom:14}}><strong>{data?.count??0} matching bundles</strong><button style={button} disabled={saving||loading} onClick={()=>reset()}>New bundle</button></div>
        <form onSubmit={e=>{e.preventDefault();load(query,0);}} style={{display:'flex',gap:8}}>
          <input aria-label="Search saved bundles" placeholder="Bundle or component SKU" value={query} onChange={e=>setQuery(e.target.value)} style={field}/>
          <button style={button} disabled={loading||saving}>Search</button>
        </form>
        <div style={{maxHeight:'50vh',overflowY:'auto',marginTop:12}}>
          {(data?.bundles||[]).map(b=><article key={b.sku} style={{padding:'14px 0',borderBottom:'1px solid #e2e8f0'}}>
            <strong style={{overflowWrap:'anywhere'}}>{b.sku}</strong>
            <ul>{b.components.map(c=><li key={c.sku}>{c.quantity} × {c.sku}</li>)}</ul>
            {!!b.warnings.length&&<div style={{color:'#92400e',fontSize:12,marginBottom:8}}>Component package data needs attention</div>}
            {b.updated_by&&<div style={{fontSize:12,marginBottom:8}}>Last saved by {b.updated_by}</div>}
            <button style={button} disabled={saving||loading} onClick={()=>reset(b)} aria-label={`Edit ${b.sku}`}>Edit mapping</button>
          </article>)}
          {data&&!data.count&&<p>No matching bundles. Use New bundle to add one.</p>}
        </div>
        <div style={{display:'flex',gap:8,marginTop:14,flexWrap:'wrap'}}>
          <button style={button} disabled={loading||saving||!offset} onClick={()=>load(filter,offset-50)}>Previous</button>
          <button style={button} disabled={loading||saving||offset+50>=(data?.count??0)} onClick={()=>load(filter,offset+50)}>Next</button>
          <button style={button} disabled={loading||saving} onClick={refresh}>Refresh list</button>
        </div>
      </section>
      <section style={card} aria-label="Bundle editor">
        <h3 style={{marginTop:0}}>{original?'Edit bundle':'New bundle'}</h3>
        <label>Bundle SKU<input aria-label="Bundle SKU" maxLength={100} readOnly={!!original} disabled={saving} value={draft.sku} onChange={e=>setDraft({...draft,sku:e.target.value})} style={{...field,marginTop:5}}/></label>
        {original&&<p style={{fontSize:12}}>To correct the parent SKU, remove this mapping and create the corrected one.</p>}
        <form onSubmit={searchComponents} style={{display:'flex',gap:8,margin:'14px 0'}}>
          <input aria-label="Find component SKU" placeholder="Find component SKU or product" style={field} value={search} onChange={e=>{setSearch(e.target.value);searchSequence.current++;setSearching(false);setMatches([]);}}/>
          <button style={button} disabled={saving||searching||!search.trim()}>{searching?'Searching…':'Find SKU'}</button>
        </form>
        {searchNote&&<p role="status">{searchNote}</p>}
        <div style={{maxHeight:180,overflowY:'auto'}}>{matches.map(r=><div key={r.sku} style={{display:'flex',gap:8,padding:'6px 0',alignItems:'center'}}><span style={{flex:1,fontSize:12}}><strong>{r.sku}</strong><br/>{r.product_name}</span><button type="button" style={button} disabled={saving||draft.components.length>=50||draft.components.some(c=>key(c.sku)===key(r.sku))} onClick={()=>addSku(r.sku)} aria-label={`Add component ${r.sku}`}>Add</button></div>)}</div>
        <form onSubmit={save}>
          <div style={{maxHeight:'40vh',overflowY:'auto',margin:'14px 0'}}>{draft.components.map((c,index)=><div key={index} style={{display:'grid',gridTemplateColumns:'minmax(0,1fr) 85px auto',gap:8,marginBottom:12,alignItems:'end'}}>
            <label style={{fontSize:12}}>Component SKU<input aria-label={`Component SKU ${index+1}`} maxLength={100} disabled={saving} style={field} value={c.sku} onChange={e=>component(index,'sku',e.target.value)}/></label>
            <label style={{fontSize:12}}>Qty / bundle<input aria-label={`Component quantity ${index+1}`} type="number" min="1" max="100" step="1" disabled={saving} style={field} value={c.quantity} onChange={e=>component(index,'quantity',e.target.value)}/></label>
            <button type="button" style={button} aria-label={`Remove component ${index+1}`} disabled={saving} onClick={()=>{setDraft(d=>({...d,components:d.components.filter((_,i)=>i!==index)}));setWarnings([]);}}>×</button>
          </div>)}</div>
          <button type="button" style={button} disabled={saving||draft.components.length>=50} onClick={()=>addSku()}>Add component row</button>
          {problem&&<p style={{color:'#92400e'}} role="status">{problem}</p>}
          {!!warnings.length&&<div style={{background:'#fffbeb',padding:12,marginTop:14,borderRadius:8}}><strong>Package data to review</strong><ul>{warnings.map(w=><li key={w}>{w}</li>)}</ul><p>Edit component measurements in Package Database, then reload your shipment.</p></div>}
          <div style={{display:'flex',gap:10,marginTop:18,flexWrap:'wrap',borderTop:'1px solid #e2e8f0',paddingTop:16}}>
            <button style={{...button,background:'#20813a',color:'#fff',opacity:problem||!revision||saving?0.7:1}} disabled={saving||!!problem||!revision}>{saving?'Saving…':'Save bundle'}</button>
            <button type="button" style={button} disabled={saving} onClick={()=>reset()}>Cancel</button>
            {original&&<button type="button" style={{...button,color:'#b91c1c'}} disabled={saving} onClick={remove}>Remove mapping</button>}
          </div>
        </form>
      </section>
    </div>
  </div>;
}
