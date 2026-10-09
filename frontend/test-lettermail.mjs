// Vite on 3011, mocked API only; no Shopify writes.
import assert from 'node:assert/strict';
const {chromium}=await import(process.env.PLAYWRIGHT_MODULE||'playwright');
const browser=await chromium.launch({executablePath:'C:/Program Files/Google/Chrome/Application/chrome.exe',headless:true});
try {
 const page=await browser.newPage();const errors=[];page.on('pageerror',e=>errors.push(e.message));
 await page.route('http://127.0.0.1:3011/',r=>r.fulfill({contentType:'text/html',body:`<div id="root"></div><script type="module">
 import RefreshRuntime from '/@react-refresh';RefreshRuntime.injectIntoGlobalHook(window);window.$RefreshReg$=()=>{};window.$RefreshSig$=()=>t=>t;window.__vite_plugin_react_preamble_installed__=true;
 const React=(await import('/node_modules/.vite/deps/react.js')).default;
 const {createRoot}=(await import('/node_modules/.vite/deps/react-dom_client.js')).default;
 const {default:Shipping}=await import('/src/ShippingPage.jsx');
 createRoot(document.getElementById('root')).render(React.createElement(Shipping));</script>`}));
 await page.route('**/api/**',r=>{
  const path=new URL(r.request().url()).pathname;
  const reply=body=>r.fulfill({contentType:'application/json',body:JSON.stringify(body)});
  if(path.endsWith('/users'))return reply({users:[{id:'test',name:'Tester',initials:'T'}]});
  if(path.includes('/orders/'))return reply({name:'#51183',shipping_method:'Flat Rate Lettermail (No Tracking)',delivery:{type:'lettermail',packing_allowed:false},lettermail:{selected:true,merchandise_value_cad:'18',warnings:['9 physical units to pack. Check the combined pouch.']},line_items:[{sku:'M4ThumbScrew',title:'Screws',pack_quantity:9}],physical_packages:[]});
  return reply({});
 });
 await page.goto('http://127.0.0.1:3011/');await page.getByRole('button',{name:/T.*Tester/}).click();
 await page.getByPlaceholder('#51234 or 51234').fill('51183');await page.getByRole('button',{name:'Load order',exact:true}).click();
 await page.getByRole('heading',{name:'#51183 · LETTERMAIL — NO TRACKING',exact:true}).waitFor();
 assert.equal(await page.getByRole('button',{name:'Build Packing Plan',exact:true}).count(),0);
 await page.getByLabel('Correct pouch and protection; nothing fragile or unsuitable included.').check();
 await page.getByLabel('Sealed mailpiece length and width checked: 14–38 cm long and 9–27 cm wide.').check();
 await page.getByLabel('I reviewed every warning above and confirmed this order is suitable to send as selected.').check();
 await page.getByLabel('Final sealed pouch weight (g)').fill('25');await page.getByLabel('Maximum sealed pouch thickness (cm)').fill('2.1');
 await page.getByText('Final thickness must be between 0.018 and 2 cm, including protection.',{exact:true}).waitFor();
 await page.getByLabel('Maximum sealed pouch thickness (cm)').fill('1.5');
 await page.getByText('Checklist checked for this session — confirm postage before dispatch.',{exact:true}).waitFor();
 await page.getByRole('button',{name:'Load order',exact:true}).click();
 await page.waitForFunction(()=>document.querySelector('input[type="number"]')?.value==='');
 assert.deepEqual(errors,[]);console.log('PASS: selected Lettermail hides carton workflow, validates pouch checks, and resets on reload.');
} finally {await browser.close();}
