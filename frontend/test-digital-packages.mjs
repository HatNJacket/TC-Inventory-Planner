// Isolated UI regression. Start Vite on port 3011; no live API writes.
import assert from 'node:assert/strict';
const {chromium}=await import(process.env.PLAYWRIGHT_MODULE||'playwright');
const browser=await chromium.launch({executablePath:process.env.CHROME_PATH||'C:/Program Files/Google/Chrome/Application/chrome.exe',headless:true});
try{
 const page=await browser.newPage();const errors=[];let saved=null;
 page.on('pageerror',e=>errors.push(e.message));
 await page.route('http://127.0.0.1:3011/',r=>r.fulfill({contentType:'text/html',body:`<div id="root"></div><script type="module">
 import RefreshRuntime from '/@react-refresh';RefreshRuntime.injectIntoGlobalHook(window);window.$RefreshReg$=()=>{};window.$RefreshSig$=()=>t=>t;window.__vite_plugin_react_preamble_installed__=true;
 const React=(await import('/node_modules/.vite/deps/react.js')).default;
 const {createRoot}=(await import('/node_modules/.vite/deps/react-dom_client.js')).default;
 const {default:Database}=await import('/src/PackageDatabase.jsx');
 createRoot(document.getElementById('root')).render(React.createElement(Database));</script>`}));
 await page.route('**/api/**',r=>{
  const reply=body=>r.fulfill({contentType:'application/json',body:JSON.stringify(body)});
  if(r.request().method()==='POST'){
   saved={...r.request().postDataJSON().record,id:'digital-test',_revision:'v1',dimensions_in:null,weight_kg:null};
   return reply({record:saved});
  }
  if(new URL(r.request().url()).searchParams.get('q')==='ORIGINAL')return reply({records:[{sku:'ORIGINAL',product_name:'Original physical product'}],count:1});
  return reply({records:saved?[saved]:[],count:saved?1:0,bundles:[],health:{total:0,registry_records:saved?1:0,digital:saved?1:0,linked:saved?.packaging_source_sku?1:0,verified:0,unverified:0,missing_dimensions:0,missing_weights:0,needs_review:0,ready:0,verified_percent:0,ready_percent:0,bundles:0,ready_bundles:0}});
 });
 await page.goto('http://127.0.0.1:3011/');
 await page.getByLabel('Package sku',{exact:true}).fill('DIGITAL-LICENSE');
 await page.getByLabel('Package product_name',{exact:true}).fill('Software license');
 await page.getByLabel('Shipping behavior').selectOption('digital');
 assert.equal(await page.getByLabel('Package length',{exact:true}).count(),0);
 await page.getByRole('button',{name:'Save Package',exact:true}).click();
 await page.getByRole('button',{name:'Edit package DIGITAL-LICENSE',exact:true}).waitFor();
 assert.equal(saved.shipping_behavior,'digital');
 assert.equal(await page.getByRole('button',{name:'Mark physically verified',exact:true}).count(),0);
 await page.getByRole('button',{name:/Digital \/ no shipping/}).click();
 await page.getByRole('button',{name:'Edit package DIGITAL-LICENSE',exact:true}).click();
 await page.getByLabel('Shipping behavior').selectOption('standard');
 assert.equal(await page.getByLabel('Package length',{exact:true}).inputValue(),'');
 assert.equal(await page.getByRole('button',{name:'Mark physically verified',exact:true}).isDisabled(),true);
 // Convert an existing listing to linked packaging without entering measurements.
 await page.getByLabel('Packaging data',{exact:true}).selectOption('linked');
 await page.getByLabel('Search original product').fill('ORIGINAL');
 await page.getByRole('button',{name:'Search packaging sources',exact:true}).click();
 await page.getByRole('button',{name:'Use ORIGINAL',exact:true}).click();
 await page.getByRole('button',{name:'Save Package',exact:true}).click();
 await page.getByText('Select an original SKU and confirm identical packaging before saving.',{exact:true}).waitFor();
 await page.getByLabel('I confirm this listing has the same physical packaging and contents as the original product.').check();
 await page.getByRole('button',{name:'Save Package',exact:true}).click();
 await page.getByText('Uses ORIGINAL',{exact:true}).waitFor();
 assert.equal(saved.packaging_source_sku,'ORIGINAL');
 assert.equal(saved.packaging_confirmed,true);
 assert.equal(await page.getByRole('button',{name:'Mark physically verified',exact:true}).count(),0);
 await page.getByLabel('Packaging data',{exact:true}).selectOption('own');
 assert.equal(await page.getByLabel('Package length',{exact:true}).inputValue(),'');
 assert.deepEqual(errors,[]);
 console.log('PASS: digital editor; original SKU search, packaging confirmation, link conversion, and return to separate measurements.');
}finally{await browser.close();}
