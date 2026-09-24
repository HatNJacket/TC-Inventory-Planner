import { useEffect, useMemo, useRef, useState } from 'react';
import * as api from './api';
import ShippingSignIn from './ShippingSignIn';

const GREEN = '#2aad51';
const VERIFIED = new Set(['Verified — Warehouse', 'Confirmed — Vendor/Label']);
const COLORS = ['#2aad51', '#0f8a5f', '#7c3aed', '#d97706', '#db2777', '#0891b2', '#4f46e5', '#65a30d', '#dc2626', '#9333ea'];
const BEHAVIOR_LABELS = {
  standard: 'Standard',
  carrier_ready: 'Carrier-ready',
  accessory_carrier: 'Accessory carrier',
  must_ship_alone: 'Must ship alone',
};

const card = { background: '#fff', border: '1px solid var(--border)', borderRadius: 12 };
const th = { padding: '10px 12px', borderBottom: '1px solid var(--border)', color: 'var(--text-light)', fontSize: 11, textTransform: 'uppercase', textAlign: 'left', whiteSpace: 'nowrap' };
const td = { padding: '11px 12px', borderBottom: '1px solid var(--border)', verticalAlign: 'top' };
const primaryButton = { border: 0, borderRadius: 8, padding: '10px 16px', background: 'var(--green)', color: '#fff', fontWeight: 800, cursor: 'pointer' };
const secondaryButton = { border: '1px solid var(--border)', borderRadius: 8, padding: '9px 13px', background: '#fff', color: 'var(--text)', fontWeight: 700, cursor: 'pointer' };
const inputStyle = { width: '100%', boxSizing: 'border-box', padding: '9px 10px', borderRadius: 8, border: '1px solid var(--border)', fontSize: 13, background: '#fff' };

function fmt(value, digits = 2) {
  const n = Number(value);
  if (!Number.isFinite(n)) return '—';
  if (Number.isInteger(n)) return String(n);
  return n.toFixed(digits).replace(/0+$/, '').replace(/\.$/, '');
}

function dimsText(dims) {
  if (!Array.isArray(dims) || dims.length !== 3) return '—';
  return dims.map(v => fmt(v)).join(' × ') + ' in';
}

function weightText(value) {
  if (value === null || value === undefined || value === '') return '—';
  const kg = Number(value);
  if (!Number.isFinite(kg)) return '—';
  return `${kg.toFixed(2)} kg / ${(kg * 2.2046226218).toFixed(2)} lb`;
}

function StatusBadge({ status }) {
  const provisional = status === 'Shopify — Unverified';
  const verified = VERIFIED.has(status);
  const bg = verified ? '#dcfce7' : provisional ? '#fef3c7' : '#e5e7eb';
  const fg = verified ? '#166534' : provisional ? '#92400e' : '#374151';
  return <span style={{ display: 'inline-block', padding: '4px 8px', borderRadius: 999, background: bg, color: fg, fontSize: 11, fontWeight: 800 }}>{status || 'Direct / review'}</span>;
}

function BehaviorBadge({ behavior }) {
  const label = BEHAVIOR_LABELS[behavior] || behavior || 'Standard';
  return <span style={{ display: 'inline-block', padding: '4px 8px', borderRadius: 999, background: behavior === 'accessory_carrier' ? '#eafaf1' : '#f3f4f6', color: behavior === 'accessory_carrier' ? '#087151' : '#4b5563', fontSize: 11, fontWeight: 800 }}>{label}</span>;
}

function projectIso(x, y, z) {
  return { x: (x - y) * 0.8660254, y: (x + y) * 0.5 - z };
}
function corners(x, y, z, dx, dy, dz) {
  return { a:[x,y,z], b:[x+dx,y,z], c:[x+dx,y+dy,z], d:[x,y+dy,z], e:[x,y,z+dz], f:[x+dx,y,z+dz], g:[x+dx,y+dy,z+dz], h:[x,y+dy,z+dz] };
}
function rgba(hex, alpha) {
  const value = parseInt(hex.replace('#', ''), 16);
  return `rgba(${(value>>16)&255}, ${(value>>8)&255}, ${value&255}, ${alpha})`;
}
function drawPoly(ctx, pts, fill, stroke) {
  ctx.beginPath(); ctx.moveTo(pts[0].x, pts[0].y); pts.slice(1).forEach(p => ctx.lineTo(p.x, p.y)); ctx.closePath();
  if (fill) { ctx.fillStyle = fill; ctx.fill(); }
  if (stroke) { ctx.strokeStyle = stroke; ctx.lineWidth = 1.2; ctx.stroke(); }
}

function PackingDiagram({ result }) {
  const ref = useRef(null);
  useEffect(() => {
    const canvas = ref.current;
    if (!canvas || !result?.carton || !result?.placements?.length) return undefined;
    function draw() {
      const ctx = canvas.getContext('2d');
      const dpr = window.devicePixelRatio || 1;
      const cssWidth = Math.max(420, canvas.parentElement?.clientWidth || 900);
      const cssHeight = 470;
      canvas.width = Math.round(cssWidth * dpr); canvas.height = Math.round(cssHeight * dpr);
      canvas.style.width = '100%'; canvas.style.height = `${cssHeight}px`;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0); ctx.clearRect(0, 0, cssWidth, cssHeight);
      const carton = (result.carton || []).map(Number).sort((a,b)=>a-b);
      const [cx,cy,cz] = carton;
      const outline = corners(0,0,0,cx,cy,cz);
      const projected = Object.fromEntries(Object.entries(outline).map(([k,p]) => [k, projectIso(...p)]));
      const vals = Object.values(projected); const minX=Math.min(...vals.map(p=>p.x)); const maxX=Math.max(...vals.map(p=>p.x)); const minY=Math.min(...vals.map(p=>p.y)); const maxY=Math.max(...vals.map(p=>p.y));
      const padding=48; const scale=Math.min((cssWidth-padding*2)/Math.max(1,maxX-minX),(cssHeight-padding*2)/Math.max(1,maxY-minY));
      const ox=(cssWidth-(maxX-minX)*scale)/2-minX*scale; const oy=(cssHeight-(maxY-minY)*scale)/2-minY*scale;
      const P = c => { const p=projectIso(...c); return {x:ox+p.x*scale,y:oy+p.y*scale}; };
      const placements=(result.placements||[]).map((p,i)=>({...p,_i:i})).sort((a,b)=>a.position.reduce((x,y)=>x+y,0)-b.position.reduce((x,y)=>x+y,0));
      placements.forEach(p => {
        const [x,y,z]=p.position.map(Number); const [dx,dy,dz]=p.dimensions.map(Number); const c=corners(x,y,z,dx,dy,dz); const color=COLORS[p._i%COLORS.length];
        drawPoly(ctx,[P(c.e),P(c.f),P(c.g),P(c.h)],rgba(color,.74),color);
        drawPoly(ctx,[P(c.a),P(c.b),P(c.f),P(c.e)],rgba(color,.54),color);
        drawPoly(ctx,[P(c.b),P(c.c),P(c.g),P(c.f)],rgba(color,.42),color);
        const center=P([x+dx/2,y+dy/2,z+dz]); ctx.fillStyle='#fff'; ctx.font='700 12px system-ui'; ctx.textAlign='center'; ctx.textBaseline='middle';
        const label=(p.sku||p.part||String(p._i+1)); ctx.fillText(label.length>18?`${label.slice(0,16)}…`:label,center.x,center.y);
      });
      ctx.save(); ctx.strokeStyle='#334155'; ctx.lineWidth=1.7; ctx.setLineDash([7,5]);
      [['a','b'],['b','c'],['c','d'],['d','a'],['e','f'],['f','g'],['g','h'],['h','e'],['a','e'],['b','f'],['c','g'],['d','h']].forEach(([a,b])=>{const p1=P(outline[a]),p2=P(outline[b]);ctx.beginPath();ctx.moveTo(p1.x,p1.y);ctx.lineTo(p2.x,p2.y);ctx.stroke();}); ctx.restore();
      ctx.fillStyle='#475569'; ctx.font='600 12px system-ui'; ctx.textAlign='left'; ctx.fillText(`Solver orientation: ${carton.map(fmt).join(' × ')} in`,18,24);
    }
    draw();
    const ro = new ResizeObserver(draw); if (canvas.parentElement) ro.observe(canvas.parentElement); return () => ro.disconnect();
  }, [result]);
  if (!result?.carton || !result?.placements?.length) return null;
  return <div style={{ marginTop: 18 }}>
    <div style={{ fontWeight: 800, marginBottom: 8 }}>Visual packing diagram</div>
    <div style={{ fontSize: 12, color: 'var(--text-light)', marginBottom: 8 }}>Isometric view of the solver’s actual package positions and rotations.</div>
    <div style={{ border: '1px solid var(--border)', borderRadius: 10, overflow: 'hidden', background: '#fbfcfd' }}><canvas ref={ref} /></div>
    <div style={{ display:'flex', gap:10, flexWrap:'wrap', marginTop:10 }}>
      {(result.placements||[]).map((p,i)=><div key={i} style={{ display:'flex', gap:6, alignItems:'center', fontSize:11 }}><span style={{ width:10,height:10,borderRadius:2,background:COLORS[i%COLORS.length] }} />{p.sku || p.part || `Package ${i+1}`} · {dimsText(p.dimensions)}</div>)}
    </div>
  </div>;
}

function groupPhysicalPackages(packages) {
  const map = new Map();
  for (const p of packages || []) {
    const key = `${p.registry_id || ''}||${p.sku || ''}||${p.part || ''}||${p.shipping_behavior || 'standard'}`;
    if (!map.has(key)) map.set(key, { ...p, key, quantity: 0, packed_inside_count: 0, packed_into: '', rejected_carrier_ids: [] });
    map.get(key).quantity += 1;
  }
  return [...map.values()];
}

function historyFor(summary, row) {
  const match=(summary?.accessories||[]).find(x => String(x.sku||'').toLowerCase()===String(row.sku||'').toLowerCase() && String(x.part||'').toLowerCase()===String(row.part||'').toLowerCase());
  if (!match) return { label:'No history', cls:'unknown', autoReject:false };
  if (match.auto_reject) return { label:`Excluded · ${match.failed_attempts} failures`, cls:'fail', autoReject:true };
  if (match.fit_attempts && match.failed_attempts) return { label:`Mixed · ${match.fit_attempts} fit / ${match.failed_attempts} fail`, cls:'mixed', autoReject:false };
  if (match.fit_attempts) return { label:`Confirmed fit · ${match.fit_attempts}×`, cls:'fit', autoReject:false };
  if (match.failed_attempts) return { label:`Didn't fit · ${match.failed_attempts}×`, cls:'fail', autoReject:false };
  return { label:'No history', cls:'unknown', autoReject:false };
}

function combinationText(combo) {
  if (!combo) return null;
  if (combo.strongly_confirmed) return `Strongly confirmed combination · ${combo.exact_success_count} clean successes`;
  if (combo.combination_status === 'exact_confirmed') return `Exact combination confirmed · ${combo.exact_success_count} success`;
  if (combo.combination_status === 'subset_supported') return `Supported as a subset of ${combo.covering_success_count} larger successful combination${combo.covering_success_count===1?'':'s'}`;
  if (combo.combination_status === 'dimensionally_inferred') return `Dimensionally inferred fit · ${combo.dimensionally_inferred_count} supporting configuration${combo.dimensionally_inferred_count===1?'':'s'}`;
  if (combo.combination_status === 'individual_only') return 'Combination not proven — items have fit separately, not together';
  return 'New / unproven combination';
}

function PackingView({ onToast, currentUser }) {
  const [orderNumber,setOrderNumber]=useState(''); const [order,setOrder]=useState(null); const [rows,setRows]=useState([]); const [loading,setLoading]=useState(false); const [error,setError]=useState('');
  const [plan,setPlan]=useState(null); const [planLoading,setPlanLoading]=useState(false); const [carrierData,setCarrierData]=useState({}); const [fitDraft,setFitDraft]=useState({}); const [notes,setNotes]=useState({}); const [detailsOpen,setDetailsOpen]=useState(false); const [finalWeights,setFinalWeights]=useState({}); const firstLookup=useRef(true);
  const destination=order?.shipping_address||{}; const readiness=order?.packing_readiness||{};
  const hostRows=useMemo(()=>rows.filter(r=>r.shipping_behavior==='accessory_carrier'),[rows]);
  const standardRows=useMemo(()=>rows.filter(r=>r.shipping_behavior==='standard'),[rows]);

  async function refreshCarrierData(nextRows=rows) {
    const hosts=nextRows.filter(r=>r.shipping_behavior==='accessory_carrier' && r.registry_id);
    const standards=nextRows.filter(r=>r.shipping_behavior==='standard');
    const entries=await Promise.all(hosts.map(async host=>{
      try {
        const summary=await api.getShippingPackingHistorySummary(host.registry_id);
        const eligible=standards.filter(r=>Math.max(0,r.quantity-r.packed_inside_count)>0 && !r.rejected_carrier_ids.includes(host.registry_id) && !historyFor(summary,r).autoReject && VERIFIED.has(r.verification_status));
        const accessories=eligible.map(r=>({ sku:r.sku, part:r.part, quantity:Math.max(0,r.quantity-r.packed_inside_count), dimensions_in:r.dimensions_in, verification_status:r.verification_status, registry_id:r.registry_id }));
        const combo=accessories.length?await api.getShippingCombinationSummary(host.registry_id,accessories):null;
        return [host.key,{summary,combo}];
      } catch(err) { return [host.key,{error:err?.message||String(err)}]; }
    }));
    setCarrierData(Object.fromEntries(entries));
  }

  async function loadOrder(e) {
    e?.preventDefault(); const value=orderNumber.trim(); if(!value)return; setLoading(true); setError(''); setPlan(null);
    try { const result=await api.getShippingOrder(value); setOrder(result); const grouped=groupPhysicalPackages(result.physical_packages); setRows(grouped); setFitDraft({}); setFinalWeights({}); await refreshCarrierData(grouped); onToast?.(`Loaded ${result.name||value} from Shopify`); }
    catch(err){setOrder(null);setRows([]);setError(err?.message||'Could not load Shopify order');onToast?.(err?.message||'Could not load Shopify order','error');}
    finally{setLoading(false);firstLookup.current=false;}
  }

  function planPayload(nextRows=rows) { return { order_reference:order?.name||orderNumber, items:nextRows.map(r=>({ sku:r.sku, product_name:r.product_name, part:r.part, registry_id:r.registry_id, dimensions_in:r.dimensions_in, verification_status:r.verification_status, weight_kg:r.weight_kg, shipping_behavior:r.shipping_behavior, quantity:r.quantity, packed_inside_count:r.packed_inside_count, packed_into:r.packed_into })) }; }
  async function buildPlan(nextRows=rows) { setPlanLoading(true);setError('');try{const result=await api.buildShippingPlanFromLoadedOrder(planPayload(nextRows));setPlan(result);setFinalWeights({});onToast?.(result.message||'Packing plan created');return result;}catch(err){setError(err?.message||'Could not build packing plan');onToast?.(err?.message||'Could not build packing plan','error');return null;}finally{setPlanLoading(false);} }

  function draftKey(host,row,kind){return `${host.key}::${row.key}::${kind}`;}
  function setDraft(host,row,kind,value){setFitDraft(prev=>({...prev,[draftKey(host,row,kind)]:value}));}

  async function saveObservation(host) {
    const data=carrierData[host.key]||{}; const candidates=standardRows.filter(r=>Math.max(0,r.quantity-r.packed_inside_count)>0 && !r.rejected_carrier_ids.includes(host.registry_id) && !historyFor(data.summary,r).autoReject);
    const changes=[];
    for(const row of candidates){const available=Math.max(0,row.quantity-row.packed_inside_count);const fit=Number(fitDraft[draftKey(host,row,'fit')]||0);const fail=Number(fitDraft[draftKey(host,row,'fail')]||0);if(!Number.isInteger(fit)||!Number.isInteger(fail)||fit<0||fail<0||fit+fail>available){setError(`Fit + didn't fit cannot exceed available quantity for ${row.sku}.`);return;}if(fit>0&&!VERIFIED.has(row.verification_status)){setError(`${row.sku} must be measured and verified before it can be packed inside a carrier.`);return;}if(fit+fail>0)changes.push({row,fit,fail});}
    if(!changes.length){setError('Enter at least one Fit qty or Didn’t fit quantity before saving.');return;}
    const resultMap=new Map();
    rows.forEach(r=>{if(r.packed_into===host.registry_id&&r.packed_inside_count>0)resultMap.set(r.key,{row:r,fit_qty:r.packed_inside_count,failed_qty:0});});
    changes.forEach(({row,fit,fail})=>{const ex=resultMap.get(row.key)||{row,fit_qty:0,failed_qty:0};ex.fit_qty+=fit;ex.failed_qty+=fail;resultMap.set(row.key,ex);});
    const results=[...resultMap.values()].map(({row,fit_qty,failed_qty})=>({sku:row.sku,product_name:row.product_name,part:row.part,registry_id:row.registry_id,verification_status:row.verification_status,dimensions_in:row.dimensions_in,fit_qty,failed_qty}));
    try { await api.saveShippingPackingObservation({host_registry_id:host.registry_id,host_sku:host.sku,host_part:host.part,order_reference:order?.name||orderNumber,notes:notes[host.key]||'',results});
      const next=rows.map(r=>{const c=changes.find(x=>x.row.key===r.key);if(!c)return r;return {...r,packed_inside_count:r.packed_inside_count+c.fit,packed_into:c.fit?host.registry_id:r.packed_into,rejected_carrier_ids:c.fail?[...new Set([...r.rejected_carrier_ids,host.registry_id])]:r.rejected_carrier_ids};});
      setRows(next);setFitDraft({});await refreshCarrierData(next);await buildPlan(next);onToast?.(`Packing observation saved as ${currentUser||'Unknown'}`);
    } catch(err){setError(err?.message||'Could not save packing observation');}
  }

  function useLearned(host) { const data=carrierData[host.key]; if(!data?.combo)return; const candidates=standardRows.filter(r=>Math.max(0,r.quantity-r.packed_inside_count)>0 && VERIFIED.has(r.verification_status) && !r.rejected_carrier_ids.includes(host.registry_id) && !historyFor(data.summary,r).autoReject); const updates={...fitDraft};candidates.forEach(r=>{updates[draftKey(host,r,'fit')]=String(Math.max(0,r.quantity-r.packed_inside_count));updates[draftKey(host,r,'fail')]='0';});setFitDraft(updates); }

  async function copySummary() {
    if(!plan?.shipping_summary?.packages?.length)return; const lines=[`Order ${order?.name||orderNumber}`];
    plan.shipping_summary.packages.forEach(p=>{const final=finalWeights[p.package_number]||'';lines.push(`Package ${p.package_number}: ${dimsText(p.dimensions_in)} | Final weight: ${final||'CONFIRM ON SCALE'} kg | ${p.contents.map(c=>`${c.sku||c.name}${c.quantity>1?` x${c.quantity}`:''}`).join(', ')}${p.stamp_accessories_inside?' | ACCESSORIES INSIDE':''}`);});
    try{await navigator.clipboard.writeText(lines.join('\n'));onToast?.('Shipping summary copied');}catch{onToast?.('Could not copy shipping summary','error');}
  }
  function downloadCsv(){if(!plan?.shipping_summary?.packages?.length)return;const esc=v=>`"${String(v??'').replaceAll('"','""')}"`;const rowsCsv=[['Package','Type','Length in','Width in','Height in','Calculated kg','Final scale kg','Contents','Reminder']];plan.shipping_summary.packages.forEach(p=>rowsCsv.push([p.package_number,p.package_type,...(p.dimensions_in||['','','']),p.calculated_weight_kg??'',finalWeights[p.package_number]||'',p.contents.map(c=>`${c.sku||c.name}${c.quantity>1?` x${c.quantity}`:''}`).join('; '),p.stamp_accessories_inside?'ACCESSORIES INSIDE':'']));const blob=new Blob([rowsCsv.map(r=>r.map(esc).join(',')).join('\n')],{type:'text/csv'});const url=URL.createObjectURL(blob);const a=document.createElement('a');a.href=url;a.download=`${(order?.name||orderNumber||'shipment').replace('#','')}-shipping.csv`;a.click();URL.revokeObjectURL(url);}

  const loose=plan?.loose_result; const packages=plan?.shipping_summary?.packages||[];
  return <div>
    <div style={{...card,padding:20,marginBottom:18}}><form onSubmit={loadOrder} style={{display:'flex',gap:10,alignItems:'end',flexWrap:'wrap'}}><div style={{flex:'1 1 320px'}}><label style={{display:'block',fontSize:12,fontWeight:800,marginBottom:6,color:'var(--text-light)'}}>Shopify order number</label><input value={orderNumber} onChange={e=>setOrderNumber(e.target.value)} placeholder="#51234 or 51234" style={inputStyle}/></div><button type="submit" disabled={loading||!orderNumber.trim()} style={{...primaryButton,minWidth:150,opacity: loading ? 0.7 : 1}}>{loading?(firstLookup.current?'Connecting to Shopify…':'Loading…'):'Load order'}</button></form>{error&&<div style={{marginTop:12,color:'#b91c1c',fontSize:13,fontWeight:700}}>{error}</div>}</div>
    {!order&&<div style={{...card,padding:50,textAlign:'center',color:'var(--text-light)'}}><div style={{fontSize:44}}>📦</div><div style={{fontWeight:800,color:'var(--text)',marginTop:8}}>Pack a Shopify order</div><div style={{fontSize:13,marginTop:6}}>Load the order once, then all packing-plan rebuilds happen locally against the loaded package state.</div></div>}
    {order&&<>
      <div style={{display:'grid',gridTemplateColumns:'repeat(auto-fit,minmax(250px,1fr))',gap:14,marginBottom:18}}>
        <div style={{...card,padding:18}}><div style={{fontSize:11,textTransform:'uppercase',fontWeight:800,color:'var(--text-light)'}}>Order</div><div style={{fontSize:24,fontWeight:900,marginTop:4}}>{order.name}</div><div style={{fontSize:13,marginTop:7}}>{order.shipping_method||'No shipping method recorded'}</div></div>
        <div style={{...card,padding:18}}><div style={{fontSize:11,textTransform:'uppercase',fontWeight:800,color:'var(--text-light)'}}>Ship to</div><div style={{fontWeight:800,marginTop:5}}>{destination.name||'No shipping address'}</div><div style={{fontSize:13}}>{[destination.city,destination.province_code,destination.zip].filter(Boolean).join(', ')}</div><div style={{fontSize:13}}>{destination.country||''}</div></div>
        <div style={{...card,padding:18}}><div style={{fontSize:11,textTransform:'uppercase',fontWeight:800,color:'var(--text-light)'}}>Package readiness</div><div style={{fontSize:25,fontWeight:900,color:readiness.ready_for_verified_packing?GREEN:'#b45309',marginTop:4}}>{readiness.physical_package_count||0} packages</div><div style={{fontSize:12,color:'var(--text-light)'}}>{(readiness.provisional_skus||[]).length} provisional SKU · {readiness.unresolved_count||0} unresolved</div></div>
      </div>
      <div style={{...card,overflow:'hidden',marginBottom:18}}><div style={{padding:'14px 18px',borderBottom:'1px solid var(--border)',fontWeight:900}}>Physical packages</div><div style={{overflowX:'auto'}}><table style={{width:'100%',borderCollapse:'collapse',fontSize:13}}><thead><tr style={{background:'#f8faf9'}}>{['SKU / package','Qty','Dimensions','Weight','Behavior','Verification','Packed inside'].map(h=><th key={h} style={th}>{h}</th>)}</tr></thead><tbody>{rows.map(r=><tr key={r.key}><td style={td}><strong>{r.sku}</strong><div style={{fontSize:11,color:'var(--text-light)'}}>{r.part||r.product_name}</div></td><td style={td}>{r.quantity}</td><td style={td}>{dimsText(r.dimensions_in)}</td><td style={td}>{weightText(r.weight_kg)}</td><td style={td}><BehaviorBadge behavior={r.shipping_behavior}/></td><td style={td}><StatusBadge status={r.verification_status}/></td><td style={td}>{r.packed_inside_count?`${r.packed_inside_count} × inside carrier`:'—'}</td></tr>)}</tbody></table></div></div>
      {(readiness.unresolved||[]).length>0&&<div style={{...card,padding:16,marginBottom:18,border:'1px solid #fecaca',background:'#fff7f7'}}><div style={{fontWeight:900,color:'#991b1b'}}>Needs package data before final packing</div><div style={{fontSize:12,color:'#7f1d1d',marginTop:4}}>These order lines could not be converted into usable physical packages. The final shipping plan is incomplete until they are fixed.</div><div style={{display:'grid',gap:7,marginTop:10}}>{readiness.unresolved.map((u,i)=><div key={`${u.line_item_id||u.sku||'unresolved'}-${i}`} style={{padding:'8px 10px',borderRadius:8,background:'#fff',border:'1px solid #fee2e2',fontSize:12}}><strong>{u.sku||'Missing SKU'}{u.part?` — ${u.part}`:''}</strong><span style={{color:'#7f1d1d'}}> · {u.reason||'Package data needs review'}</span></div>)}</div></div>}
      {hostRows.length>0&&<div style={{...card,padding:18,marginBottom:18}}><div style={{display:'flex',justifyContent:'space-between',gap:12,alignItems:'start',marginBottom:12}}><div><div style={{fontWeight:900,fontSize:16}}>Accessory carrier suggestions</div><div style={{fontSize:12,color:'var(--text-light)',marginTop:4}}>Record real fit / didn’t-fit results before building the final plan. Verified accessories only.</div></div><div style={{fontSize:12,color:'var(--text-light)'}}>Recorded as <strong style={{color:GREEN}}>{currentUser||'Unknown'}</strong></div></div>
        <div style={{display:'grid',gap:14}}>{hostRows.map(host=>{const data=carrierData[host.key]||{};const candidates=standardRows.filter(r=>Math.max(0,r.quantity-r.packed_inside_count)>0 && !r.rejected_carrier_ids.includes(host.registry_id));const excluded=candidates.filter(r=>historyFor(data.summary,r).autoReject);const visible=candidates.filter(r=>!historyFor(data.summary,r).autoReject);const eligible=visible.filter(r=>VERIFIED.has(r.verification_status));const blocked=visible.filter(r=>!VERIFIED.has(r.verification_status));if(!visible.length&&!excluded.length)return null;return <div key={host.key} style={{border:'1px solid var(--border)',borderRadius:10,overflow:'hidden'}}><div style={{padding:'12px 14px',background:'#fbfcfd',borderBottom:'1px solid var(--border)',display:'flex',justifyContent:'space-between',gap:10}}><div><strong>{host.sku}{host.part?` — ${host.part}`:''}</strong><div style={{fontSize:11,color:'var(--text-light)',marginTop:3}}>{host.carrier_notes||'Accessory carrier'}</div></div><span style={{fontSize:10,fontWeight:900,padding:'5px 8px',borderRadius:999,background:'#fff8dc',color:'#845a06'}}>STAMP: ACCESSORIES INSIDE</span></div>
          {data.error&&<div style={{padding:12,color:'#b91c1c'}}>{data.error}</div>}
          {data.combo&&eligible.length>0&&<div style={{padding:'10px 14px',background:'#f6fbf7',borderBottom:'1px solid var(--border)',fontSize:12}}><strong>{combinationText(data.combo)}</strong>{(data.combo.strongly_confirmed||data.combo.combination_status==='dimensionally_inferred')&&<button onClick={()=>useLearned(host)} style={{...secondaryButton,marginLeft:10,padding:'5px 9px',fontSize:11}}>{data.combo.combination_status==='dimensionally_inferred'?'Use inferred fit quantities':'Fill confirmed fit quantities'}</button>}</div>}
          {eligible.map(r=>{const hist=historyFor(data.summary,r);const available=Math.max(0,r.quantity-r.packed_inside_count);return <div key={r.key} style={{display:'grid',gridTemplateColumns:'minmax(260px,1fr) 110px 110px',gap:10,padding:'10px 14px',alignItems:'end',borderBottom:'1px solid #edf0f3'}}><div><strong>{r.sku}{r.part?` — ${r.part}`:''} ×{available}</strong><div style={{fontSize:10,marginTop:5,fontWeight:800,color:hist.cls==='fit'?'#087151':hist.cls==='fail'?'#a13e3e':hist.cls==='mixed'?'#8a5b08':'#64748b'}}>{hist.label}</div></div><label style={{fontSize:10,fontWeight:800,textTransform:'uppercase'}}>Fit qty<input type="number" min="0" max={available} value={fitDraft[draftKey(host,r,'fit')]||'0'} onChange={e=>setDraft(host,r,'fit',e.target.value)} style={{...inputStyle,marginTop:4}}/></label><label style={{fontSize:10,fontWeight:800,textTransform:'uppercase'}}>Didn’t fit<input type="number" min="0" max={available} value={fitDraft[draftKey(host,r,'fail')]||'0'} onChange={e=>setDraft(host,r,'fail',e.target.value)} style={{...inputStyle,marginTop:4}}/></label></div>})}
          {blocked.length>0&&<div style={{padding:'10px 14px',background:'#fffaf0',fontSize:11,color:'#8a5b08'}}><strong>Measure & verify before carrier use</strong><div style={{marginTop:4}}>{blocked.map(r=>r.sku).join(' · ')}</div></div>}
          {excluded.length>0&&<div style={{padding:'10px 14px',background:'#fff1f2',fontSize:11,color:'#9f1239',borderTop:'1px solid #ffe4e6'}}><strong>Automatically excluded from this carrier</strong><div style={{marginTop:4}}>{excluded.map(r=>`${r.sku}${r.part?` — ${r.part}`:''} (${historyFor(data.summary,r).label})`).join(' · ')}</div></div>}
          {eligible.length>0&&<div style={{display:'grid',gridTemplateColumns:'1fr auto',gap:10,padding:12}}><input value={notes[host.key]||''} onChange={e=>setNotes(prev=>({...prev,[host.key]:e.target.value}))} placeholder="Optional note: foam gap, orientation…" style={inputStyle}/><button onClick={()=>saveObservation(host)} style={secondaryButton}>Save Packing Result</button></div>}
        </div>})}</div>
      </div>}
      <div style={{...card,padding:18,marginBottom:18}}><div style={{display:'flex',justifyContent:'space-between',alignItems:'center',gap:12,flexWrap:'wrap'}}><div><div style={{fontWeight:900,fontSize:16}}>Build packing plan</div><div style={{fontSize:12,color:'var(--text-light)',marginTop:4}}>V5.7 carrier assignments + stock-aware exact 3D carton optimization.</div></div><button onClick={()=>buildPlan()} disabled={planLoading||!rows.length} style={{...primaryButton,opacity: planLoading ? 0.7 : 1}}>{planLoading?'Planning…':'Build Packing Plan'}</button></div></div>
      {plan&&<>
        {plan.status!=='ok'&&<div style={{...card,padding:14,marginBottom:18,border:'1px solid #fecaca',background:'#fff7f7',color:'#991b1b',fontSize:12}}><strong>Partial packing plan.</strong> {plan.message||'Remaining loose items need review before shipping.'}</div>}
        <div style={{...card,padding:20,marginBottom:18}}><div style={{display:'flex',justifyContent:'space-between',gap:16,alignItems:'start',flexWrap:'wrap'}}><div><div style={{fontSize:11,fontWeight:900,textTransform:'uppercase',letterSpacing:'.06em',color:'var(--text-light)'}}>{loose?.recommendation_tier==='next_best_available'?'Next best available warehouse carton':'Warehouse carton for remaining loose items'}</div><div style={{fontSize:36,fontWeight:900,letterSpacing:'-.03em',marginTop:5}}>{loose?.carton?dimsText(loose.carton):'No warehouse carton needed'}</div></div><div style={{display:'flex',gap:7,flexWrap:'wrap'}}>{loose?.status==='ok'&&<span style={{padding:'6px 9px',borderRadius:999,background:'#dcfce7',color:'#166534',fontSize:11,fontWeight:900}}>Valid 3D fit</span>}{loose?.confidence&&<span style={{padding:'6px 9px',borderRadius:999,background:loose.confidence==='provisional'?'#fef3c7':'#dcfce7',color:loose.confidence==='provisional'?'#92400e':'#166534',fontSize:11,fontWeight:900}}>{loose.confidence==='provisional'?'Provisional recommendation':'All dimensions verified'}</span>}</div></div>
          {loose&&loose.status!=='ok'&&<div style={{marginTop:14,padding:11,background:'#fff7f7',border:'1px solid #fecaca',borderRadius:8,color:'#991b1b',fontSize:12}}>{loose.message||'No catalog carton could be proven for the remaining loose items.'}</div>}
          {loose?.recommendation_tier==='next_best_available'&&<div style={{marginTop:14,padding:11,background:'#fff7ed',border:'1px solid #fdba74',borderRadius:8,color:'#92400e',fontSize:12}}>NEXT BEST FIT: {dimsText(loose.carton)} selected because best-fit {dimsText(loose.ideal_carton)} is out of stock.</div>}
          {loose?.confidence==='provisional'&&<div style={{marginTop:14,padding:11,background:'#fef3c7',border:'1px solid #f3d889',borderRadius:8,color:'#805009',fontSize:12}}>Uses Shopify-unverified dimensions for {(loose.provisional_skus||[]).join(', ')}. Measure before relying on this recommendation for production shipping.</div>}
          {loose?.status==='ok'&&<div style={{display:'grid',gridTemplateColumns:'repeat(auto-fit,minmax(150px,1fr))',gap:10,marginTop:16}}>{[['Empty space',`${loose.empty_space_percent}%`],['Package count',loose.placements?.length||0],['Product volume',`${fmt((loose.placements||[]).reduce((sum,p)=>sum+p.dimensions.reduce((a,b)=>a*b,1),0))} in³`],['Carton volume',`${fmt((loose.carton||[]).reduce((a,b)=>a*b,1))} in³`]].map(([k,v])=><div key={k} style={{background:'#f8faf9',border:'1px solid var(--border)',borderRadius:9,padding:12}}><div style={{fontSize:10,textTransform:'uppercase',fontWeight:800,color:'var(--text-light)'}}>{k}</div><div style={{fontSize:20,fontWeight:900,marginTop:4}}>{v}</div></div>)}</div>}
          <PackingDiagram result={loose}/>
          {loose?.placements?.length>0&&<div style={{marginTop:15}}><button onClick={()=>setDetailsOpen(v=>!v)} style={secondaryButton}>{detailsOpen?'Hide Solver Details':'Show Solver Details'}</button>{detailsOpen&&<div style={{overflowX:'auto',marginTop:10}}><table style={{width:'100%',borderCollapse:'collapse',fontSize:12}}><thead><tr><th style={th}>Package</th><th style={th}>Rotated dimensions</th><th style={th}>Position (x, y, z)</th></tr></thead><tbody>{loose.placements.map((p,i)=><tr key={i}><td style={td}>{p.sku||p.name||`Package ${i+1}`}{p.part?` — ${p.part}`:''}</td><td style={td}>{dimsText(p.dimensions)}</td><td style={td}>{(p.position||[]).map(v=>fmt(v)).join(', ')}</td></tr>)}</tbody></table></div>}</div>}
        </div>
        <div style={{...card,padding:18,marginBottom:18}}><div style={{display:'flex',justifyContent:'space-between',gap:12,alignItems:'center',flexWrap:'wrap',marginBottom:12}}><div><div style={{fontWeight:900,fontSize:16}}>Final shipping summary</div><div style={{fontSize:12,color:'var(--text-light)',marginTop:3}}>{packages.length} shipping package{packages.length===1?'':'s'} · final scale weight wins.</div></div><div style={{display:'flex',gap:8}}><button onClick={copySummary} style={secondaryButton}>Copy for ShipStation</button><button onClick={downloadCsv} style={secondaryButton}>Download CSV</button></div></div>
          <div style={{overflowX:'auto'}}><table style={{width:'100%',borderCollapse:'collapse',fontSize:12,minWidth:1000}}><thead><tr style={{background:'#f8faf9'}}>{['#','Type','Dimensions','Calculated weight','Final scale kg','Contents','Reminder'].map(h=><th key={h} style={th}>{h}</th>)}</tr></thead><tbody>{packages.map(p=><tr key={p.package_number}><td style={{...td,fontWeight:900}}>{p.package_number}</td><td style={td}>{p.package_type==='warehouse_carton'?'Warehouse carton':'Factory carton'}</td><td style={td}>{dimsText(p.dimensions_in)}</td><td style={td}>{weightText(p.calculated_weight_kg)}{!p.weight_complete&&<div style={{fontSize:10,color:'#92400e',marginTop:3}}>Confirm on scale</div>}</td><td style={td}><input value={finalWeights[p.package_number]||''} onChange={e=>setFinalWeights(prev=>({...prev,[p.package_number]:e.target.value}))} placeholder="Scale" style={{...inputStyle,minWidth:95}}/></td><td style={td}>{(p.contents||[]).map((c,i)=><div key={i}>{c.sku||c.name}{c.part?` — ${c.part}`:''}{c.quantity>1?` ×${c.quantity}`:''}</div>)}</td><td style={td}>{p.stamp_accessories_inside?<strong style={{color:'#845a06'}}>ACCESSORIES INSIDE</strong>:'—'}</td></tr>)}</tbody></table></div>
        </div>
      </>}
    </>}
  </div>;
}

function CartonCatalogView({ onToast, active }) {
  const [data,setData]=useState(api.getCachedShippingCartons);
  const [draft,setDraft]=useState(()=>Object.fromEntries((api.getCachedShippingCartons()?.inventory||[]).map(x=>[x.key,x.quantity??''])));
  const [loading,setLoading]=useState(true);const [saving,setSaving]=useState(false);const [error,setError]=useState('');
  const dirty = Object.keys(draft).some(key => String(draft[key]) !== String(data?.inventory?.find(row=>row.key===key)?.quantity??''));
  async function load(){setLoading(true);setError('');try{const r=await api.getShippingCartons();setData(r);setDraft(Object.fromEntries((r.inventory||[]).map(x=>[x.key,x.quantity??''])));}catch(e){setError(e.message);onToast?.(e.message,'error');}finally{setLoading(false);}}
  useEffect(()=>{if(active&&!dirty)load();},[active]);
  function change(key,delta){setDraft(prev=>{const current=prev[key]===''?0:Number(prev[key]||0);return {...prev,[key]:String(Math.max(0,current+delta))};});}
  async function save(){setSaving(true);try{const r=await api.saveShippingCartonStock((data.inventory||[]).filter(x=>String(draft[x.key])!==String(x.quantity??'')).map(x=>({dimensions:x.dimensions,quantity:draft[x.key]===''?null:Number(draft[x.key])})));setData(r);setDraft(Object.fromEntries((r.inventory||[]).map(x=>[x.key,x.quantity??''])));onToast?.('Carton stock saved');}catch(e){onToast?.(e.message,'error');}finally{setSaving(false);}}
  if(loading&&!data)return <div style={{...card,padding:30}}>Loading carton catalog…</div>;
  return <div>{loading&&<p role="status">Showing saved catalog · checking latest stock…</p>}{error&&<p role="alert" style={{color:'#b91c1c'}}>Could not refresh stock: {error} <button onClick={load} disabled={dirty}>Retry</button></p>}<div style={{display:'flex',justifyContent:'space-between',alignItems:'start',gap:14,marginBottom:14}}><div><h2 style={{margin:0,fontSize:20}}>Warehouse carton catalog</h2><div style={{fontSize:12,color:'var(--text-light)',marginTop:4}}>{data?.count||0} sizes · {data?.tracked_count||0} counted · {data?.out_of_stock_count||0} out of stock · {data?.uncounted_count||0} not counted</div></div><button onClick={save} disabled={saving||loading||!!error||!data||!dirty} style={{...primaryButton,opacity: saving ? 0.7 : 1}}>{saving?'Saving…':'Save Carton Stock'}</button></div><div style={{display:'grid',gridTemplateColumns:'repeat(auto-fill,minmax(190px,1fr))',gap:10}}>{(data?.inventory||[]).map(r=><div key={r.key} style={{...card,padding:12,opacity: r.quantity === 0 ? 0.65 : 1}}><div style={{fontWeight:900}}>{dimsText(r.dimensions)}</div><div style={{display:'grid',gridTemplateColumns:'40px 1fr 40px',gap:6,marginTop:9}}><button disabled={loading||saving} onClick={()=>change(r.key,-1)} style={secondaryButton}>−</button><input disabled={loading||saving} value={draft[r.key]??''} onChange={e=>setDraft(prev=>({...prev,[r.key]:e.target.value}))} placeholder="blank" type="number" min="0" step="1" style={{...inputStyle,textAlign:'center'}}/><button disabled={loading||saving} onClick={()=>change(r.key,1)} style={secondaryButton}>+</button></div><div style={{fontSize:10,color:r.quantity===0?'#b91c1c':'var(--text-light)',marginTop:7}}>{r.quantity===0?'Out of stock':r.quantity==null?'Not counted':'Counted'}</div></div>)}</div></div>;
}

function HistoryView({ onToast }) {
  const [data,setData]=useState(null);async function load(){try{setData(await api.getShippingPackingHistory(200));}catch(e){onToast?.(e.message,'error');}}useEffect(()=>{load();},[]);
  return <div><div style={{display:'flex',justifyContent:'space-between',alignItems:'center',marginBottom:14}}><div><h2 style={{margin:0,fontSize:20}}>Packing history</h2><div style={{fontSize:12,color:'var(--text-light)',marginTop:4}}>{data?.count||0} accessory-carrier observations.</div></div><button onClick={load} style={secondaryButton}>Refresh</button></div><div style={{...card,overflow:'hidden'}}><div style={{overflowX:'auto'}}><table style={{width:'100%',borderCollapse:'collapse',fontSize:12,minWidth:900}}><thead><tr style={{background:'#f8faf9'}}>{['Recorded','Order','Packer','Carrier','Results','Notes'].map(h=><th key={h} style={th}>{h}</th>)}</tr></thead><tbody>{(data?.observations||[]).map(o=><tr key={o.id}><td style={td}>{o.recorded_at?.replace('T',' ')}</td><td style={td}>{o.order_reference||'—'}</td><td style={td}>{o.packed_by||'Unknown'}</td><td style={td}><strong>{o.host_sku}</strong>{o.host_part?<div>{o.host_part}</div>:null}</td><td style={td}>{(o.results||[]).map((r,i)=><div key={i}>{r.sku}{r.part?` / ${r.part}`:''}: {r.fit_qty?`${r.fit_qty} fit`:''}{r.fit_qty&&r.failed_qty?' · ':''}{r.failed_qty?`${r.failed_qty} failed`:''}</div>)}</td><td style={td}>{o.notes||'—'}</td></tr>)}{!data?.observations?.length&&<tr><td colSpan="6" style={{...td,textAlign:'center',padding:30,color:'var(--text-light)'}}>No observations recorded yet.</td></tr>}</tbody></table></div></div></div>;
}

const emptyRecord={sku:'',product_name:'',part:'',dimensions_in:['','',''],verification_status:'Verified — Warehouse',weight_kg:'',shipping_behavior:'standard',carrier_notes:'',notes:'',packages_per_unit:1,source_parts_per_unit:1};
function PackageDatabaseView({ onToast, currentUser }) {
  const [query,setQuery]=useState('');const [data,setData]=useState(null);const [loading,setLoading]=useState(false);const [edit,setEdit]=useState(emptyRecord);const [saving,setSaving]=useState(false);const requestId=useRef(0);
  async function load(q=query){const id=++requestId.current;setLoading(true);try{const result=await api.getShippingPackageDatabase(q,50);if(id===requestId.current)setData(result);}catch(e){if(id===requestId.current)onToast?.(e.message,'error');}finally{if(id===requestId.current)setLoading(false);}}
  useEffect(()=>{load('');},[]);
  function choose(r){setEdit({...r,dimensions_in:[...(r.dimensions_in||['','',''])]});}
  function field(name,value){setEdit(prev=>({...prev,[name]:value}));}
  async function save(){setSaving(true);try{const rec={...edit,dimensions_in:(edit.dimensions_in||[]).map(Number),weight_kg:edit.weight_kg===''?null:Number(edit.weight_kg),packages_per_unit:Number(edit.packages_per_unit||1),source_parts_per_unit:Number(edit.source_parts_per_unit||1),measured_by:edit.measured_by||currentUser||''};const r=await api.saveShippingPackageRecord(rec);onToast?.(`Saved ${r.record.sku}`);setEdit(emptyRecord);await load();}catch(e){onToast?.(e.message,'error');}finally{setSaving(false);}}
  async function del(){if(!edit.id)return;if(!window.confirm(`Delete package record ${edit.sku}${edit.part?` / ${edit.part}`:''}?`))return;try{await api.deleteShippingPackageRecord(edit.id);onToast?.('Package record deleted');setEdit(emptyRecord);await load();}catch(e){onToast?.(e.message,'error');}}
  return <div><div style={{display:'grid',gridTemplateColumns:'minmax(0,1.7fr) minmax(320px,.8fr)',gap:16,alignItems:'start'}}><div style={card}><div style={{padding:14,borderBottom:'1px solid var(--border)',display:'flex',gap:8}}><input value={query} onChange={e=>setQuery(e.target.value)} onKeyDown={e=>{if(e.key==='Enter')load();}} placeholder="Search SKU, product, or package part" style={inputStyle}/><button onClick={()=>load()} style={secondaryButton}>{loading?'Searching…':'Search'}</button></div><div style={{fontSize:11,color:'var(--text-light)',padding:'8px 12px'}}>{data?.count||0} matching records{data?.truncated?' · showing first 50 · search to narrow results':''}</div><div style={{overflow:'auto',maxHeight:650}}><table style={{width:'100%',borderCollapse:'collapse',fontSize:12}}><thead><tr style={{background:'#f8faf9'}}>{['SKU','Product / part','Dimensions','Status','Behavior'].map(h=><th key={h} style={th}>{h}</th>)}</tr></thead><tbody>{(data?.records||[]).map(r=><tr key={r.id} onClick={()=>choose(r)} style={{cursor:'pointer',background:edit.id===r.id?'#f0fbf4':'transparent'}}><td style={{...td,fontWeight:800}}>{r.sku}</td><td style={td}>{r.product_name}<div style={{fontSize:10,color:'var(--text-light)'}}>{r.part||'Primary package'}</div></td><td style={td}>{dimsText(r.dimensions_in)}</td><td style={td}><StatusBadge status={r.verification_status}/></td><td style={td}><BehaviorBadge behavior={r.shipping_behavior}/></td></tr>)}</tbody></table></div></div>
    <div style={{...card,padding:16,position:'sticky',top:12}}><div style={{display:'flex',justifyContent:'space-between',alignItems:'center'}}><strong>{edit.id?'Edit package record':'Add package record'}</strong>{edit.id&&<button onClick={()=>setEdit(emptyRecord)} style={{...secondaryButton,padding:'5px 8px'}}>New</button>}</div><div style={{display:'grid',gap:9,marginTop:12}}><label style={{fontSize:11,fontWeight:800}}>SKU<input value={edit.sku||''} onChange={e=>field('sku',e.target.value)} style={{...inputStyle,marginTop:4}}/></label><label style={{fontSize:11,fontWeight:800}}>Product name<input value={edit.product_name||''} onChange={e=>field('product_name',e.target.value)} style={{...inputStyle,marginTop:4}}/></label><label style={{fontSize:11,fontWeight:800}}>Part<input value={edit.part||''} onChange={e=>field('part',e.target.value)} style={{...inputStyle,marginTop:4}}/></label><div style={{display:'grid',gridTemplateColumns:'repeat(3,1fr)',gap:6}}>{['L','W','H'].map((label,i)=><label key={label} style={{fontSize:11,fontWeight:800}}>{label} in<input type="number" step="0.01" value={edit.dimensions_in?.[i]??''} onChange={e=>{const d=[...(edit.dimensions_in||['','',''])];d[i]=e.target.value;field('dimensions_in',d);}} style={{...inputStyle,marginTop:4}}/></label>)}</div><label style={{fontSize:11,fontWeight:800}}>Verification<select value={edit.verification_status||''} onChange={e=>field('verification_status',e.target.value)} style={{...inputStyle,marginTop:4}}><option>Verified — Warehouse</option><option>Confirmed — Vendor/Label</option><option>Shopify — Unverified</option></select></label><label style={{fontSize:11,fontWeight:800}}>Shipping behavior<select value={edit.shipping_behavior||'standard'} onChange={e=>field('shipping_behavior',e.target.value)} style={{...inputStyle,marginTop:4}}><option value="standard">Standard</option><option value="carrier_ready">Carrier-ready — ships in own package</option><option value="accessory_carrier">Accessory carrier — own package + may hold accessories</option><option value="must_ship_alone">Must ship alone</option></select></label><label style={{fontSize:11,fontWeight:800}}>Weight kg<input type="number" step="0.001" value={edit.weight_kg??''} onChange={e=>field('weight_kg',e.target.value)} style={{...inputStyle,marginTop:4}}/></label>{edit.shipping_behavior==='accessory_carrier'&&<label style={{fontSize:11,fontWeight:800}}>Carrier notes<textarea value={edit.carrier_notes||''} onChange={e=>field('carrier_notes',e.target.value)} rows="3" style={{...inputStyle,marginTop:4}}/></label>}<label style={{fontSize:11,fontWeight:800}}>Notes<textarea value={edit.notes||''} onChange={e=>field('notes',e.target.value)} rows="3" style={{...inputStyle,marginTop:4}}/></label><div style={{fontSize:10,color:'var(--text-light)'}}>Measured by: {edit.measured_by||currentUser||'Unknown'}</div><div style={{display:'flex',gap:8}}><button onClick={save} disabled={saving} style={{...primaryButton,flex:1}}>{saving?'Saving…':'Save Package'}</button>{edit.id&&<button onClick={del} style={{...secondaryButton,color:'#b91c1c'}}>Delete</button>}</div></div></div></div></div>;
}

export default function ShippingPage({ onToast, entrySignal }) {
  const [view,setView]=useState('packing');
  const [user,setUser]=useState(null);
  const [visited,setVisited]=useState(new Set(['packing']));
  useEffect(()=>{
    setUser(null); api.setShippingUser(null);
    return ()=>api.setShippingUser(null);
  },[entrySignal]);
  function signIn(profile) { api.setShippingUser(profile); setUser(profile); }
  function selectView(id) { setVisited(previous=>new Set([...previous,id])); setView(id); }
  const tabs=[['packing','Pack an Order'],['registry','Package Database'],['catalog','Carton Catalog'],['history','Packing History']];
  // Keep visited views mounted so tab changes preserve the order, plan and drafts.
  // Hiding the workspace during identity selection also prevents unattributed saves.
  return <div style={{padding:'28px 34px 60px',maxWidth:1500,margin:'0 auto'}}>
    {!user&&<ShippingSignIn onSignIn={signIn}/>}
    <div hidden={!user}>
      <div style={{display:'flex',justifyContent:'space-between',gap:16,marginBottom:18}}>
        <div><h1 style={{margin:0,fontSize:25,fontWeight:900}}>Shipping</h1><p>Warehouse packing tools</p></div>
        <button style={secondaryButton} onClick={()=>{setUser(null);api.setShippingUser(null);}}>Signed in as {user?.name} · Switch user</button>
      </div>
      <div style={{display:'flex',gap:7,flexWrap:'wrap',marginBottom:18}}>{tabs.map(([id,label])=><button key={id} onClick={()=>selectView(id)} style={{border:'1px solid',borderColor:view===id?GREEN:'var(--border)',borderRadius:999,padding:'8px 13px',background:view===id?'#eefbf3':'#fff',color:view===id?'#16723b':'var(--text)',fontWeight:800,cursor:'pointer'}}>{label}</button>)}</div>
      <div hidden={view!=='packing'}><PackingView onToast={onToast} currentUser={user?.name}/></div>
      {visited.has('registry')&&<div hidden={view!=='registry'}><PackageDatabaseView onToast={onToast} currentUser={user?.name}/></div>}
      {visited.has('catalog')&&<div hidden={view!=='catalog'}><CartonCatalogView onToast={onToast} active={view==='catalog'&&!!user}/></div>}
      {view==='history'&&<HistoryView onToast={onToast}/>}
    </div>
  </div>;
}
