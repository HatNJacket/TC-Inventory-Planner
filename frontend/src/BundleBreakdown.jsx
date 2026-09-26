export default function BundleBreakdown({ bundles = [], registry = false }) {
  if (!bundles.length) return null;
  return <section aria-label="Bundle components" style={{background:'#f0fbf4',border:'1px solid #b7e4c7',borderRadius:12,padding:16,marginBottom:18}}>
    <strong>{registry ? 'Separately packaged bundle mappings' : 'Bundle expanded into separate packages'}</strong>
    <p style={{fontSize:12,margin:'6px 0 12px'}}>Packing uses each component’s package dimensions and weight. The parent bundle is not counted again. Shopify’s order is unchanged.</p>
    {bundles.map((bundle,index) => <div key={`${bundle.sku}-${index}`} style={{marginTop:8,fontSize:13}}>
      <strong>{bundle.sku} × {bundle.quantity ?? 1}</strong>
      <ul style={{margin:'6px 0',paddingLeft:22}}>{bundle.components.map(component =>
        <li key={component.sku}>{component.quantity} × {component.sku}</li>
      )}</ul>
    </div>)}
    {registry && <p style={{fontSize:12,marginBottom:0}}>Edit measurements and weights on the component SKU records below. Use Manage Bundles to add or change mappings.</p>}
  </section>;
}
