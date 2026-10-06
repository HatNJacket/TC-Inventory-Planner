// Run with PLAYWRIGHT_MODULE pointing to a Playwright ESM entry and Vite on port 3011.
import assert from 'node:assert/strict';
const {chromium}=await import(process.env.PLAYWRIGHT_MODULE||'playwright');
const browser=await chromium.launch({executablePath:process.env.CHROME_PATH||'C:/Program Files/Google/Chrome/Application/chrome.exe',headless:true});
try {
 const page=await browser.newPage();const errors=[];page.on('pageerror',e=>errors.push(e.message));
 let verified=false,loads=0,fail=false,holdPlan=null;
 const record=()=>({id:'r1',sku:'TEST',product_name:'Test product',part:'',dimensions_in:[2,3,4],weight_kg:1,packages_per_unit:1,verification_status:verified?'Verified — Warehouse':'Shopify — Unverified',shipping_behavior:'standard',_revision:verified?'v2':'v1'});
 await page.route('http://127.0.0.1:3011/',r=>r.fulfill({contentType:'text/html',body:`<div id="root"></div><script type="module">
 import RefreshRuntime from '/@react-refresh';RefreshRuntime.injectIntoGlobalHook(window);window.$RefreshReg$=()=>{};window.$RefreshSig$=()=>t=>t;window.__vite_plugin_react_preamble_installed__=true;
 const React=(await import('/node_modules/.vite/deps/react.js')).default;
 const {createRoot}=(await import('/node_modules/.vite/deps/react-dom_client.js')).default;
 const {default:Shipping}=await import('/src/ShippingPage.jsx');
 createRoot(document.getElementById('root')).render(React.createElement(Shipping));</script>`}));
 await page.route('**/api/**',async r=>{
  const path=new URL(r.request().url()).pathname;
  const reply=(body,status=200)=>r.fulfill({status,contentType:'application/json',body:JSON.stringify(body)});
  if(path.endsWith('/users'))return reply({users:[{id:'test',name:'Tester',initials:'T'}]});
  if(path.includes('/orders/')){loads++;if(fail)return reply({detail:'Test refresh unavailable'},500);return reply({name:'#123',physical_packages:[{...record(),registry_id:'r1',registry_revision:record()._revision,quantity:1}],packing_readiness:{physical_package_count:1,unresolved_count:0,unresolved:[],provisional_skus:verified?[]:['TEST']}});}
  if(path.endsWith('/review')){verified=true;return reply({record:record()});}
  if(path.endsWith('/package-database'))return reply({records:[record()],count:1,bundles:[],health:{registry_records:1,total:1,verified:verified?1:0,unverified:verified?0:1,missing_dimensions:0,missing_weights:0,needs_review:0,ready:verified?1:0,bundles:0,ready_bundles:0}});
  if(path.endsWith('/packing-plan')){holdPlan=()=>reply({message:'STALE PLAN',loose_result:{},shipping_summary:{packages:[]}});return;}
  return reply({});
 });
 await page.goto('http://127.0.0.1:3011/');await page.getByRole('button',{name:/T.*Tester/}).click();
 const input=page.getByPlaceholder('#51234 or 51234');await input.fill('123');await page.getByRole('button',{name:'Load order',exact:true}).click();
 await page.getByText('#123',{exact:true}).waitFor();
 await page.getByRole('button',{name:'Build Packing Plan',exact:true}).click();
 await page.waitForFunction(()=>document.body.textContent.includes('Planning…'));
 await page.getByRole('button',{name:'Open package database for TEST',exact:true}).click();
 await page.getByRole('heading',{name:'Edit package record',exact:true}).waitFor();
 assert.equal(await page.getByLabel('Search package database').inputValue(),'TEST');
 assert.equal(await page.getByLabel('Package sku',{exact:true}).inputValue(),'TEST');
 await page.getByRole('checkbox').check();await page.getByRole('button',{name:'Mark physically verified',exact:true}).click();
 await page.getByText('Physical verification recorded',{exact:true}).count();
 await page.getByRole('button',{name:'Pack an Order',exact:true}).click();
 await page.getByText('Verified — Warehouse',{exact:true}).first().waitFor();
 assert.equal(await input.inputValue(),'123');assert.equal(loads,2);
 await holdPlan();await page.waitForTimeout(150);
 assert.equal(await page.getByRole('button',{name:'Build Packing Plan',exact:true}).isEnabled(),true);
 assert.equal(await page.getByText('STALE PLAN',{exact:true}).count(),0);
 fail=true;
 await page.getByRole('button',{name:'Package Database',exact:true}).click();
 await page.getByRole('checkbox').check();await page.getByRole('button',{name:'Mark physically verified',exact:true}).click();
 await page.getByRole('button',{name:'Pack an Order',exact:true}).click();
 await page.getByText(/Could not refresh package details/).waitFor();
 assert.equal(await input.inputValue(),'123');await page.getByText('#123',{exact:true}).waitFor();
 assert.equal(await page.getByRole('button',{name:'Build Packing Plan',exact:true}).isDisabled(),true);
 fail=false;await page.getByRole('button',{name:'Load order',exact:true}).click();
 await page.getByText('Verified — Warehouse',{exact:true}).first().waitFor();
 // Repeated clicks on the same SKU reopen its editor, but never discard drafts silently.
 await page.getByRole('button',{name:'Open package database for TEST',exact:true}).click();
 await page.getByRole('heading',{name:'Edit package record',exact:true}).waitFor();
 await page.getByLabel('Package product_name').fill('Unsaved change');
 await page.getByRole('button',{name:'Pack an Order',exact:true}).click();
 page.once('dialog',d=>d.dismiss());
 await page.getByRole('button',{name:'Open package database for TEST',exact:true}).click();
 assert.equal(await page.getByLabel('Package product_name').inputValue(),'Unsaved change');
 await page.getByRole('button',{name:'Pack an Order',exact:true}).click();
 page.once('dialog',d=>d.accept());
 await page.getByRole('button',{name:'Open package database for TEST',exact:true}).click();
 await page.getByRole('heading',{name:'Edit package record',exact:true}).waitFor();
 assert.equal(await page.getByLabel('Package product_name').inputValue(),'Test product');
 assert.deepEqual(errors,[]);
 console.log('PASS: SKU link opens matching editor; repeated navigation protects drafts; order preserved after verification; stale plan ignored; failed refresh can be retried.');
} finally {await browser.close();}
