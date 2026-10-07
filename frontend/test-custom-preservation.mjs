// Vite on port 3011; PLAYWRIGHT_MODULE can point to a shared Playwright entry.
import assert from 'node:assert/strict';
const {chromium}=await import(process.env.PLAYWRIGHT_MODULE||'playwright');
const browser=await chromium.launch({executablePath:process.env.CHROME_PATH||'C:/Program Files/Google/Chrome/Application/chrome.exe',headless:true});
try{
 const page=await browser.newPage();const errors=[];page.on('pageerror',e=>errors.push(e.message));
 let verified=false,fail=false,delayedPlan;const loads=[];
 const record=()=>({id:'A',sku:'A',product_name:'Product A',part:'',dimensions_in:[2,3,4],weight_kg:1,packages_per_unit:1,verification_status:verified?'Verified — Warehouse':'Shopify — Unverified',shipping_behavior:'standard',_revision:verified?'v2':'v1'});
 await page.route('http://127.0.0.1:3011/',r=>r.fulfill({contentType:'text/html',body:`<div id="root"></div><script type="module">
 import RefreshRuntime from '/@react-refresh';RefreshRuntime.injectIntoGlobalHook(window);window.$RefreshReg$=()=>{};window.$RefreshSig$=()=>t=>t;window.__vite_plugin_react_preamble_installed__=true;
 const React=(await import('/node_modules/.vite/deps/react.js')).default;
 const {createRoot}=(await import('/node_modules/.vite/deps/react-dom_client.js')).default;
 const {default:Shipping}=await import('/src/ShippingPage.jsx');
 createRoot(document.getElementById('root')).render(React.createElement(Shipping));</script>`}));
 await page.route('**/api/**',r=>{
  const path=new URL(r.request().url()).pathname;
  const reply=(body,status=200)=>r.fulfill({status,contentType:'application/json',body:JSON.stringify(body)});
  if(path.endsWith('/users'))return reply({users:[{id:'test',name:'Tester',initials:'T'}]});
  if(path.endsWith('/package-database'))return reply({count:1,records:[record()],bundles:[]});
  if(path.endsWith('/review')){verified=true;return reply({record:record()});}
  if(path.endsWith('/custom-shipment')){
   const body=r.request().postDataJSON();loads.push(body);
   if(fail)return reply({detail:'Test refresh failed'},500);
   return reply({name:body.reference,physical_packages:Array.from({length:body.items[0].quantity},()=>({...record(),registry_id:'A',registry_revision:record()._revision})),packing_readiness:{physical_package_count:body.items[0].quantity,unresolved_count:0}});
  }
  if(path.endsWith('/packing-plan')){delayedPlan=()=>reply({status:'ok',message:'STALE',shipping_summary:{complete:true,packages:[]}});return;}
  return reply({});
 });
 await page.goto('http://127.0.0.1:3011/');await page.getByRole('button',{name:/T.*Tester/}).click();
 await page.getByRole('button',{name:'Custom Shipment',exact:true}).click();
 await page.getByLabel('Search shipment SKU or product').fill('A');await page.getByRole('button',{name:'Search SKUs',exact:true}).click();
 await page.getByRole('button',{name:'Add A',exact:true}).click();
 await page.getByLabel('Quantity for A').fill('3');await page.getByPlaceholder('Test shipment').fill('My custom test');
 await page.getByRole('button',{name:'Load Custom Shipment',exact:true}).click();await page.getByText('My custom test',{exact:true}).waitFor();
 await page.getByRole('button',{name:'Build Packing Plan',exact:true}).click();
 await page.waitForFunction(()=>document.body.textContent.includes('Planning…'));
 await page.getByRole('button',{name:'Open package database for A',exact:true}).click();
 await page.getByRole('heading',{name:'Edit package record',exact:true}).waitFor();
 await page.getByRole('checkbox').check();await page.getByRole('button',{name:'Mark physically verified',exact:true}).click();
 await page.getByRole('button',{name:'Custom Shipment',exact:true}).click();await page.getByText('Verified — Warehouse',{exact:true}).first().waitFor();
 assert.equal(await page.getByLabel('Quantity for A').inputValue(),'3');assert.equal(await page.getByPlaceholder('Test shipment').inputValue(),'My custom test');
 assert.deepEqual(loads,[{items:[{sku:'A',quantity:3}],reference:'My custom test'},{items:[{sku:'A',quantity:3}],reference:'My custom test'}]);
 await delayedPlan();await page.waitForTimeout(150);assert.equal(await page.getByText('Final shipping summary',{exact:true}).count(),0);
 // Refresh failure preserves the loaded reference and draft, and cannot build a stale plan.
 fail=true;await page.getByRole('button',{name:'Package Database',exact:true}).click();
 await page.getByRole('checkbox').check();await page.getByRole('button',{name:'Mark physically verified',exact:true}).click();
 await page.getByRole('button',{name:'Custom Shipment',exact:true}).click();await page.getByText(/Could not refresh custom shipment/).waitFor();
 await page.getByText('My custom test',{exact:true}).waitFor();assert.equal(await page.getByLabel('Quantity for A').inputValue(),'3');
 assert.equal(await page.getByRole('button',{name:'Build Packing Plan',exact:true}).isDisabled(),true);
 fail=false;await page.getByRole('button',{name:'Load Custom Shipment',exact:true}).click();await page.getByText('Verified — Warehouse',{exact:true}).first().waitFor();
 // An edited, not-yet-loaded draft must survive package changes without reviving the old shipment.
 await page.getByLabel('Quantity for A').fill('4');
 await page.getByRole('button',{name:'Package Database',exact:true}).click();await page.getByRole('checkbox').check();await page.getByRole('button',{name:'Mark physically verified',exact:true}).click();
 await page.getByRole('button',{name:'Custom Shipment',exact:true}).click();await page.getByText('Plan a custom shipment',{exact:true}).waitFor();
 assert.equal(await page.getByLabel('Quantity for A').inputValue(),'4');assert.equal(loads.length,4);
 assert.deepEqual(errors,[]);console.log('PASS: custom SKUs, quantities, reference, and loaded shipment survive verification; stale plans ignored; refresh failure/retry and unloaded drafts preserved.');
}finally{await browser.close();}
