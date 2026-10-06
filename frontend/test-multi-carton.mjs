// Start Vite on port 3011; set PLAYWRIGHT_MODULE if Playwright is not installed locally.
import assert from 'node:assert/strict';
const {chromium}=await import(process.env.PLAYWRIGHT_MODULE||'playwright');
const browser=await chromium.launch({executablePath:process.env.CHROME_PATH||'C:/Program Files/Google/Chrome/Application/chrome.exe',headless:true});
try{
 const page=await browser.newPage();const errors=[];page.on('pageerror',e=>errors.push(e.message));
 const layout={status:'ok',carton:[2,2,4],empty_space_percent:0,confidence:'verified',placements:[
  {sku:'A',dimensions:[2,2,2],position:[0,0,0]}, {sku:'A',dimensions:[2,2,2],position:[0,0,2]}]};
 const packages=[1,2].map(n=>({package_number:n,package_type:'warehouse_carton',dimensions_in:[2,2,4],calculated_weight_kg:2,weight_complete:true,contents:[{sku:'A',quantity:2}],packing_layout:layout}));
 await page.route('http://127.0.0.1:3011/',r=>r.fulfill({contentType:'text/html',body:`<div id="root"></div><script type="module">
 import RefreshRuntime from '/@react-refresh';RefreshRuntime.injectIntoGlobalHook(window);window.$RefreshReg$=()=>{};window.$RefreshSig$=()=>t=>t;window.__vite_plugin_react_preamble_installed__=true;
 const React=(await import('/node_modules/.vite/deps/react.js')).default;
 const {createRoot}=(await import('/node_modules/.vite/deps/react-dom_client.js')).default;
 const {default:Shipping}=await import('/src/ShippingPage.jsx');
 createRoot(document.getElementById('root')).render(React.createElement(Shipping));</script>`}));
 let confirmation;
 await page.route('**/api/**',r=>{
  const path=new URL(r.request().url()).pathname;let body={};
  if(path.endsWith('/users'))body={users:[{id:'test',name:'Tester',initials:'T'}]};
  else if(path.includes('/orders/'))body={name:'#TEST',physical_packages:Array.from({length:4},()=>({sku:'A',registry_id:'A',dimensions_in:[2,2,2],weight_kg:1,verification_status:'Verified — Warehouse',shipping_behavior:'standard'})),packing_readiness:{physical_package_count:4,unresolved_count:0}};
  else if(path.endsWith('/packing-plan'))body={status:'ok',message:'Fewest packages among proven fits.',loose_result:null,warehouse_results:[layout,layout],shipping_summary:{complete:true,packages}};
  else if(path.endsWith('/cartons'))body={revision:'v1',inventory:[{key:'2x2x4',quantity:5}]};
  else if(r.request().method()==='POST')confirmation=r.request().postDataJSON();
  return r.fulfill({contentType:'application/json',body:JSON.stringify(body)});
 });
 await page.goto('http://127.0.0.1:3011/');await page.getByRole('button',{name:/T.*Tester/}).click();
 await page.getByPlaceholder('#51234 or 51234').fill('TEST');await page.getByRole('button',{name:'Load order',exact:true}).click();
 await page.getByText('#TEST',{exact:true}).waitFor();await page.getByRole('button',{name:'Build Packing Plan',exact:true}).click();
 await page.getByText('2 warehouse cartons',{exact:true}).waitFor();
 assert.equal(await page.locator('canvas').count(),2);
 await page.getByRole('button',{name:'Review boxes used',exact:true}).click();
 const row=page.getByRole('region',{name:'Shipment box usage'}).getByRole('row').nth(1);
 assert.equal(await row.getByRole('cell').nth(2).innerText(),'2');
 assert.equal(await row.getByRole('cell').nth(3).innerText(),'3');
 await page.getByRole('button',{name:'Confirm boxes used',exact:true}).click();
 await page.getByRole('status').waitFor();assert.equal(confirmation.packages.length,2);
 assert.equal(confirmation.packing_result,null);
 assert.deepEqual(errors,[]);console.log('PASS: multi-carton heading, two layouts, complete summary, and grouped stock usage of two boxes.');
}finally{await browser.close();}
