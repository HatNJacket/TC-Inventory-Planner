import { useEffect, useState } from 'react';
import * as api from './api';
import { packageDimensionsText as dims } from './shippingUnits';

const card={background:'#fff',border:'1px solid var(--border)',borderRadius:12,padding:18,marginBottom:18};
const button={padding:'10px 16px',borderRadius:8,border:'1px solid var(--border)',background:'#fff',fontWeight:700,cursor:'pointer'};
const cell={padding:10,textAlign:'left',borderBottom:'1px solid #e2e8f0',verticalAlign:'top'};

export default function ShipmentIntelligence(){
  const [history,setHistory]=useState(null);
  const [analysis,setAnalysis]=useState(null);
  const [offset,setOffset]=useState(0);
  const [busy,setBusy]=useState(false);
  const [error,setError]=useState('');
  const [view,setView]=useState('shipments');
  async function load(nextOffset=offset){
    setBusy(true);setError('');
    try{const [h,a]=await Promise.all([api.getShippingShipmentHistory(nextOffset),api.getShippingBoxSuggestions()]);setHistory(h);setAnalysis(a);setOffset(nextOffset);}
    catch(e){setError(e.message);}finally{setBusy(false);}
  }
  useEffect(()=>{load(0);},[]);
  return <section style={card} aria-label="Shipment intelligence">
    <div style={{display:'flex',justifyContent:'space-between',gap:12,flexWrap:'wrap'}}><h2 style={{marginTop:0}}>Packing history & box opportunities</h2><button style={button} disabled={busy} onClick={()=>load()}>{busy?'Loading…':'Refresh shipment history'}</button></div>
    <div style={{display:'flex',gap:10,marginBottom:16}}>{[['shipments','Confirmed shipments'],['suggestions','Suggested Box Sizes']].map(([key,label])=><button key={key} style={{...button,background:view===key?'#dcfce7':'#fff'}} aria-pressed={view===key} onClick={()=>setView(key)}>{label}</button>)}</div>
    {error&&<p role="alert" style={{color:'#b91c1c'}}>{error}</p>}
    {analysis&&<p>{analysis.eligible_count} eligible shipments · {analysis.minimum_shipments} needed for analysis · at least {analysis.minimum_benefit} benefiting shipments per suggested size.</p>}
    {view==='shipments'&&history&&<>
      <p>Confirmed box usage only—not generated plans. Legacy records, tests, custom shipments, and missing/unverified layouts do not contribute to box recommendations.</p>
      <div style={{maxHeight:450,overflow:'auto'}}><table style={{width:'100%',borderCollapse:'collapse'}}><thead><tr>{['Recorded / packer','Shipment','Warehouse boxes','Empty space','Analysis / items'].map(h=><th style={cell} key={h}>{h}</th>)}</tr></thead><tbody>{history.shipments.map(r=><tr key={r.reference}>
        <td style={cell}>{r.recorded_at?.replace('T',' ')}<div>{r.user_name}</div></td><td style={cell}>{r.reference}</td>
        <td style={cell}>{r.changes.map((c,i)=><div key={i}>{dims(c.dimensions,'in')} × {c.quantity}</div>)}</td>
        <td style={cell}>{r.packing?.eligible?<>{r.packing.empty_percent}%<div>{(r.packing.empty_volume_in3*0.016387064).toFixed(2)} L</div></>:'Unavailable'}</td>
        <td style={cell}>{r.packing?.eligible?(r.packing.stockout_substitution?'Existing box unavailable — excluded from suggestions':'Eligible'):r.packing?.exclusion_reason||'Legacy record: no item layout captured'}
          {!!r.packing?.items?.length&&<details><summary>View packed items</summary>{r.packing.items.map((item,i)=><div key={i}>{item.sku||'Item'} · {dims(item.dimensions,'in')}</div>)}</details>}
        </td></tr>)}</tbody></table></div>
      {!history.count&&<p>No confirmed warehouse shipments yet. Confirm boxes used after packing a real order to start building history.</p>}
      <div style={{display:'flex',gap:12,alignItems:'center',marginTop:12}}><button style={button} disabled={busy||offset===0} onClick={()=>load(Math.max(0,offset-50))}>Previous shipments</button><span>{history.count?`${offset+1}–${Math.min(offset+50,history.count)} of ${history.count}`:'0 shipments'}</span><button style={button} disabled={busy||offset+50>=history.count} onClick={()=>load(offset+50)}>Next shipments</button></div>
    </>}
    {view==='suggestions'&&analysis&&<>
      <p>Practical evidence thresholds, not a claim of statistical significance. Analysis uses up to the latest {analysis.window_limit} eligible shipments. {analysis.excluded_count} records are ineligible; {analysis.stockout_count} stockout substitutions are excluded from benefits.</p>
      {analysis.period_start&&<p>Sample period: {analysis.period_start.replace('T',' ')} to {analysis.period_end.replace('T',' ')}.</p>}
      <p>Proposed internal dimensions preserve a proven item arrangement, add {analysis.clearance_in} in total clearance on each axis, and round up to whole inches. Space reduction is a potential filler-saving indicator—not measured paper usage or a shipping-price estimate. Check supplier availability, protection needs, and physical fit before purchasing.</p>
      {analysis.eligible_count<analysis.minimum_shipments?<p role="status">Collecting evidence: {analysis.minimum_shipments-analysis.eligible_count} more eligible shipments needed. No box suggestions yet.</p>:!analysis.suggestions.length?<p role="status">No new size meets the benefit threshold yet. Existing catalog sizes and stockout substitutions are not treated as new purchasing opportunities.</p>:<>
        <p>Ranked by total empty-volume reduction. Suggestions may cover the same orders—do not add their savings together. No catalog or shopping-list changes are made automatically.</p>
        {analysis.suggestions.map(s=><article key={s.dimensions_in.join('x')} style={{...card,background:'#f8faf9'}}>
          <h3 style={{marginTop:0}}>{dims(s.dimensions_in,'in')} <small>({dims(s.dimensions_in,'cm')})</small></h3>
          <p>{s.shipment_count} shipments ({s.share_percent}% of the sample) could benefit · {s.empty_volume_reduction_l} L less empty volume in total.</p>
          <p>Average unused space for affected shipments: {s.avg_empty_percent_before}% → {s.avg_empty_percent_after}%.</p>
          <details><summary>Show affected shipments</summary><div style={{maxHeight:250,overflow:'auto'}}><table style={{width:'100%'}}><thead><tr>{['Shipment','Box used','Empty space before / after','Volume reduction'].map(h=><th key={h} style={cell}>{h}</th>)}</tr></thead><tbody>{s.affected_shipments.map(r=><tr key={r.reference}><td style={cell}>{r.reference}</td><td style={cell}>{dims(r.current_carton,'in')}</td><td style={cell}>{r.empty_percent_before}% / {r.empty_percent_after}%</td><td style={cell}>{r.empty_volume_reduction_l} L</td></tr>)}</tbody></table></div></details>
        </article>)}
      </>}
    </>}
  </section>;
}
