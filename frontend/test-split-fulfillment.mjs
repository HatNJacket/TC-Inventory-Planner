// Run against Vite on port 3011; all API responses are mocked.
import assert from 'node:assert/strict';
const {chromium}=await import(process.env.PLAYWRIGHT_MODULE||'playwright');
const browser=await chromium.launch({executablePath:process.env.CHROME_PATH||'C:/Program Files/Google/Chrome/Application/chrome.exe',headless:true});
try {
 const page=await browser.newPage(),errors=[],requests=[];
 page.on('pageerror',e=>errors.push(e.message));
 await page.route('http://127.0.0.1:3011/',r=>r.fulfill({contentType:'text/html',body:`<div id="root"></div><script type="module">
 import RefreshRuntime from '/@react-refresh';RefreshRuntime.injectIntoGlobalHook(window);window.$RefreshReg$=()=>{};window.$RefreshSig$=()=>t=>t;window.__vite_plugin_react_preamble_installed__=true;
 const React=(await import('/node_modules/.vite/deps/react.js')).default;
 const {createRoot}=(await import('/node_modules/.vite/deps/react-dom_client.js')).default;
 const {default:Shipping}=await import('/src/ShippingPage.jsx');
 createRoot(document.getElementById('root')).render(React.createElement(Shipping));</script>`}));
 await page.route('**/api/**',r=>{
  const url=new URL(r.request().url());
  const reply=body=>r.fulfill({contentType:'application/json',body:JSON.stringify(body)});
  if(url.pathname.endsWith('/users'))return reply({users:[{id:'test',name:'Tester',initials:'T'}]});
  if(url.pathname.includes('/orders/')){
   const selected=url.searchParams.get('fulfillment_order_id')||'';requests.push(selected);
   return reply({name:'#50221',delivery:{type:'shipping',packing_allowed:!!selected,message:'Choose a fulfillment group'},
    fulfillment_selection:{selected_id:selected,groups:['S50','Accessory'].map(s=>({id:s,status:'OPEN',eligible:true,location:'Warehouse',items:[{sku:s,quantity:1}]}))},
    physical_packages:selected?[{sku:selected,registry_id:selected,dimensions_in:[2,3,4],weight_kg:1,quantity:1,shipping_behavior:'standard',verification_status:'Verified — Warehouse'}]:[],
    packing_readiness:{physical_package_count:selected?1:0,unresolved_count:0,unresolved:[],provisional_skus:[]}});
  }
  return reply({});
 });
 await page.goto('http://127.0.0.1:3011/');await page.getByRole('button',{name:/T.*Tester/}).click();
 const input=page.getByPlaceholder('#51234 or 51234');await input.fill('50221');await page.getByRole('button',{name:'Load order',exact:true}).click();
 const selector=page.getByLabel('Fulfillment group to pack');await selector.waitFor();
 assert.equal(await page.getByRole('button',{name:'Build Packing Plan',exact:true}).count(),0);
 await selector.selectOption('S50');await page.getByRole('button',{name:'Open package database for S50',exact:true}).waitFor();
 assert.equal(await page.getByRole('button',{name:'Open package database for Accessory',exact:true}).count(),0);
 await page.getByRole('button',{name:'Load order',exact:true}).click();await page.waitForFunction(()=>!document.querySelector('select[aria-label="Fulfillment group to pack"]').disabled);
 assert.equal(requests.at(-1),'S50');
 await selector.selectOption('Accessory');await page.getByRole('button',{name:'Open package database for Accessory',exact:true}).waitFor();
 assert.equal(await page.getByRole('button',{name:'Open package database for S50',exact:true}).count(),0);
 await input.fill('99999');await page.getByRole('button',{name:'Load order',exact:true}).click();await page.waitForFunction(()=>!document.querySelector('select[aria-label="Fulfillment group to pack"]').disabled);
 assert.equal(requests.at(-1),'');assert.deepEqual(errors,[]);
 console.log('Split fulfillment selection, reload preservation, and order reset passed.');
} finally {await browser.close();}
