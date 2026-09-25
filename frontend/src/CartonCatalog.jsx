import { useEffect, useState } from 'react';
import * as api from './api';
import { packageDimensionsText } from './shippingUnits';

const card = { background:'#fff', border:'1px solid var(--border)', borderRadius:12, padding:18, marginBottom:16 };
const input = { width:'100%', boxSizing:'border-box', padding:8, border:'1px solid var(--border)', borderRadius:7 };
const button = { padding:'9px 13px', border:'1px solid var(--border)', borderRadius:8, background:'#fff', cursor:'pointer', fontWeight:700 };
const primary = { ...button, background:'var(--green)', color:'#fff' };
const dimensions = row => packageDimensionsText(row.dimensions,'in');
const fields = ['quantity','minimum','target'];
const draftFor = data => Object.fromEntries((data?.inventory||[]).map(row=>[row.key,Object.fromEntries(fields.map(field=>[field,row[field]??'']))]));
const differs = (draft,row) => fields.some(field=>String(draft?.[field]??'')!==String(row[field]??''));
function download(name, rows) {
  const csv=rows.map(row=>row.map(value=>`"${String(value??'').replaceAll('"','""')}"`).join(',')).join('\r\n');
  const url=URL.createObjectURL(new Blob([csv],{type:'text/csv;charset=utf-8'}));
  const link=document.createElement('a');link.href=url;link.download=name;link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
}

export default function CartonCatalog({ onToast, active }) {
  const [data,setData]=useState(api.getCachedShippingCartons);
  const [draft,setDraft]=useState(()=>draftFor(api.getCachedShippingCartons()));
  const [loading,setLoading]=useState(true);
  const [saving,setSaving]=useState(false);
  const [error,setError]=useState('');
  const [importOpen,setImportOpen]=useState(false);
  const [mode,setMode]=useState('receive');
  const [unit,setUnit]=useState('');
  const [file,setFile]=useState(null);
  const [source,setSource]=useState('');
  const [preview,setPreview]=useState(null);
  const [importBusy,setImportBusy]=useState(false);
  const [importError,setImportError]=useState('');
  const rows=data?.inventory||[];
  const dirty=rows.some(row=>differs(draft[row.key],row));
  const busy=loading||saving||importBusy;
  const shopping=data?.shopping_list||[];
  function accept(result){setData(result);setDraft(draftFor(result));setPreview(null);}
  async function load(){setLoading(true);setError('');try{accept(await api.getShippingCartons());}catch(e){setError(e.message);}finally{setLoading(false);}}
  useEffect(()=>{if(active&&!dirty)load();},[active]);
  function change(row,field,value){setDraft(previous=>({...previous,[row.key]:{...previous[row.key],[field]:value}}));setPreview(null);}
  async function save(){
    setSaving(true);setError('');
    try{
      const changes=rows.filter(row=>differs(draft[row.key],row)).map(row=>({dimensions:row.dimensions,...Object.fromEntries(fields.map(field=>[field,draft[row.key][field]===''?null:Number(draft[row.key][field])]))}));
      accept(await api.saveShippingCartonStock(changes,data.revision));onToast?.('Carton stock and reorder levels saved');
    }catch(e){setError(e.message);}finally{setSaving(false);}
  }
  async function previewFile(){
    setImportBusy(true);setImportError('');setPreview(null);
    try{const result=await api.previewShippingStockFile(file,mode,unit);setSource(result.csv);setPreview({...result,request_id:crypto.randomUUID()});}
    catch(e){setImportError(e.message);}finally{setImportBusy(false);}
  }
  async function previewEdits(){
    setImportBusy(true);setImportError('');setPreview(null);
    try{const result=await api.previewShippingStockImport(source,mode);setPreview({...result,csv:source,request_id:crypto.randomUUID()});}
    catch(e){setImportError(e.message);}finally{setImportBusy(false);}
  }
  async function apply(){
    setImportBusy(true);setImportError('');
    try{accept(await api.applyShippingStockImport({csv:preview.csv,mode:preview.mode,revision:preview.revision,request_id:preview.request_id}));setSource('');setFile(null);onToast?.('Stock import applied');}
    catch(e){setImportError(e.message);}finally{setImportBusy(false);}
  }
  function template(){download('carton-stock-template.csv',[
    ['length','width','height','unit','quantity','minimum','target'],
    ...rows.map(row=>[...row.dimensions,'in','','','']),
  ]);}
  function exportList(){download('carton-shopping-list.csv',[
    ['length','width','height','unit','on_hand','minimum','target','order_quantity'],
    ...shopping.map(row=>[...row.dimensions,'in',row.quantity,row.minimum,row.target,row.order_quantity]),
  ]);}
  if(loading&&!data)return <div style={card}>Loading carton catalog…</div>;
  return <div>
    {loading&&<p role="status">Showing saved catalog · checking latest stock…</p>}
    {error&&<p role="alert" style={{color:'#b91c1c'}}>{error} <button style={button} onClick={()=>{if(!dirty||window.confirm('Discard unsaved carton edits and load current stock?'))load();}}>Reload current stock</button></p>}
    <div style={{display:'flex',justifyContent:'space-between',alignItems:'center',gap:12,marginBottom:16,flexWrap:'wrap'}}>
      <div><h2 style={{margin:0}}>Warehouse carton catalog</h2><p>{rows.length} sizes · {data?.low_stock_count||0} low · {data?.out_of_stock_count||0} out of stock · {data?.uncounted_count||0} not counted</p></div>
      <div style={{display:'flex',gap:8}}><button style={button} onClick={()=>setImportOpen(value=>!value)}>Import box stock</button><button style={primary} onClick={save} disabled={busy||!data||!dirty}>{saving?'Saving…':'Save Carton Stock'}</button></div>
    </div>
    <section style={{...card,borderColor:shopping.length?'#f59e0b':'var(--border)'}} aria-label="Box shopping list">
      <div style={{display:'flex',justifyContent:'space-between',gap:12}}><h3 style={{marginTop:0}}>Box shopping list</h3><button style={button} disabled={!shopping.length||busy} onClick={exportList}>Download shopping list</button></div>
      <p>At or below the minimum, order enough boxes to reach the restock target. This list uses saved stock counts.</p>
      {dirty&&<p>Save your edits to update the shopping list.</p>}
      {shopping.length?<table style={{width:'100%',textAlign:'left'}}><thead><tr>{['Box size','On hand','Minimum','Restock target','Order'].map(text=><th key={text}>{text}</th>)}</tr></thead><tbody>{shopping.map(row=><tr key={row.key}><td>{dimensions(row)}{row.quantity===0?' · Out of stock':''}</td><td>{row.quantity}</td><td>{row.minimum}</td><td>{row.target}</td><td><strong>{row.order_quantity} boxes</strong></td></tr>)}</tbody></table>:<p>No counted box sizes with configured minimums are due for reorder.</p>}
      {!!data?.needs_count_count&&<p style={{color:'#92400e'}}>{data.needs_count_count} monitored sizes need a stock count before a reorder quantity can be calculated.</p>}
    </section>
    {importOpen&&<section style={card} aria-label="Import box stock">
      <h3>Import a PDF or CSV</h3>
      <p>Review extracted sizes and quantities before applying. Quantities must be individual boxes, not bundles or cases. Unknown sizes and duplicate rows must be corrected.</p>
      {dirty&&<p role="alert">Save or reload your carton edits before importing.</p>}
      <div style={{display:'flex',gap:12,flexWrap:'wrap',alignItems:'end'}}>
        <label>Import action<select aria-label="Import action" style={input} disabled={importBusy} value={mode} onChange={e=>{setMode(e.target.value);setPreview(null);}}><option value="receive">Receive delivery — add to stock</option><option value="count">Physical count — replace stock</option></select></label>
        <label>PDF dimension units<select aria-label="PDF dimension units" style={input} disabled={importBusy} value={unit} onChange={e=>{setUnit(e.target.value);setSource('');setPreview(null);}}><option value="">Select units on PDF</option><option value="in">Inches</option><option value="cm">Centimetres</option></select></label>
        <input aria-label="Stock import file" type="file" accept=".pdf,.csv" disabled={busy||dirty} onChange={e=>{setFile(e.target.files?.[0]||null);setSource('');setPreview(null);setImportError('');}}/>
        <button style={button} disabled={busy||dirty||!file||(file.name.toLowerCase().endsWith('.pdf')&&!unit)} onClick={previewFile}>Preview file</button>
        <button style={button} disabled={!data} onClick={template}>Download CSV template</button>
      </div>
      <p style={{fontSize:12}}>PDFs need selectable text and a size/quantity table. For unsupported layouts or scans, use the CSV template. Blank minimum/target cells in an import keep your existing settings.</p>
      {importBusy&&<p role="status">Processing stock import…</p>}
      {importError&&<p role="alert" style={{color:'#b91c1c'}}>{importError}</p>}
      {source&&<details><summary>Review or correct extracted rows</summary><textarea aria-label="Extracted stock CSV" rows={8} style={{...input,fontFamily:'monospace',marginTop:10}} disabled={importBusy} value={source} onChange={e=>{setSource(e.target.value);setPreview(null);}}/><button style={button} disabled={busy||dirty} onClick={previewEdits}>Preview corrected rows</button></details>}
      {preview&&<div style={{marginTop:14}}>
        <strong>{preview.mode==='receive'?'Delivery quantities will be added to existing stock.':'Physical counts will replace stock for these sizes only.'}</strong>
        {!!preview.errors.length&&<div role="alert" style={{color:'#b91c1c'}}>{preview.errors.map((item,index)=><p key={index}>Row {item.row}: {item.message}</p>)}</div>}
        <table style={{width:'100%',textAlign:'left',margin:'12px 0'}}><thead><tr>{['Box size','Current','After import','Minimum','Target'].map(text=><th key={text}>{text}</th>)}</tr></thead><tbody>{preview.rows.map(row=><tr key={row.key}><td>{dimensions(row)}</td><td>{row.old_quantity??'Not counted'}</td><td>{row.new_quantity}</td><td>{row.minimum??'Off'}</td><td>{row.target??'—'}</td></tr>)}</tbody></table>
        <button style={primary} disabled={busy||dirty||!preview.can_apply} onClick={apply}>Apply {preview.rows.length} stock updates</button>
      </div>}
    </section>}
    <p>Set a minimum and a higher restock target for sizes you always want available. Leave both blank to disable reorder alerts. Counts currently change through manual edits and imports; building a packing plan does not consume boxes.</p>
    <div style={{display:'grid',gridTemplateColumns:'repeat(auto-fill,minmax(230px,1fr))',gap:12}}>{rows.map(row=><section key={row.key} style={{...card,borderColor:row.low_stock?'#f59e0b':'var(--border)'}}>
      <strong>{dimensions(row)}</strong><div style={{fontSize:11,color:'var(--text-light)',marginTop:4}}>{packageDimensionsText(row.dimensions,'cm')}</div>
      <div style={{color:row.quantity===0?'#b91c1c':row.low_stock?'#92400e':'var(--text-light)',fontSize:12,margin:'8px 0'}}>{row.quantity===null?'Not counted':row.quantity===0?'Out of stock':row.low_stock?'Low stock':'Counted'}</div>
      <label>On hand<div style={{display:'grid',gridTemplateColumns:'35px 1fr 35px',gap:5,margin:'5px 0 10px'}}><button aria-label={`Decrease ${row.key}`} style={button} disabled={busy} onClick={()=>change(row,'quantity',Math.max(0,Number(draft[row.key]?.quantity||0)-1))}>−</button><input aria-label={`On hand ${row.key}`} type="number" min="0" step="1" placeholder="Not counted" style={input} disabled={busy} value={draft[row.key]?.quantity??''} onChange={e=>change(row,'quantity',e.target.value)}/><button aria-label={`Increase ${row.key}`} style={button} disabled={busy} onClick={()=>change(row,'quantity',Number(draft[row.key]?.quantity||0)+1)}>+</button></div></label>
      <label>Minimum<input aria-label={`Minimum ${row.key}`} type="number" min="0" step="1" placeholder="Off" style={{...input,margin:'5px 0 10px'}} disabled={busy} value={draft[row.key]?.minimum??''} onChange={e=>change(row,'minimum',e.target.value)}/></label>
      <label>Restock target<input aria-label={`Restock target ${row.key}`} type="number" min="1" step="1" placeholder="Order up to" style={{...input,marginTop:5}} disabled={busy} value={draft[row.key]?.target??''} onChange={e=>change(row,'target',e.target.value)}/></label>
    </section>)}</div>
  </div>;
}
