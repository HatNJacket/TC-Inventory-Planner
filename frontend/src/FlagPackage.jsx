import { useState } from 'react';
import * as api from './api';
export default function FlagPackage({record,onFlagged}) {
  const [busy,setBusy]=useState(false),[error,setError]=useState('');
  async function flag(){const reason=window.prompt(`What looks wrong with ${record.sku}?`);if(!reason?.trim())return;
    setBusy(true);setError('');try{await api.reviewShippingPackage(record.registry_id,{action:'flag',revision:record.registry_revision,reason});onFlagged();}catch(e){setError(e.message);}finally{setBusy(false);}}
  return <div><button disabled={busy||!record.registry_id} onClick={flag} style={{marginTop:8,padding:'6px 9px',border:'1px solid #cbd5e1',borderRadius:6,background:'#fff',cursor:'pointer'}}>Package data looks wrong</button>{error&&<p role="alert" style={{color:'#b91c1c'}}>{error}</p>}</div>;
}
