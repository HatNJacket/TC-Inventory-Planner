# Separately packaged shipping bundles

`app/data/shipping_bundle_registry.json` maps an ordered parent SKU to component
SKUs and quantities per bundle. It is maintained separately from package records,
so saving product measurements does not remove mappings. The Package Database
shows matching mappings; this first version does not include a mapping editor.

Only use a mapping when components are picked as separate physical packages.
Prepacked retail sets should continue using their own package record.

The initial mapping is ALP-T-3NM/3.5NM-SET to one ALP-T-3NM-Ha/OIII and one
ALP-T-3.5NM-SII/Hb. Packing ignores the parent's measurement/weight record and
uses the component records, including each component's package parts and copies.
Remaining order quantity multiplies each component quantity. Shopify is read-only
and the original parent order line is retained in the response.

Missing component records, invalid dimensions, or missing/nonpositive weights
produce unresolved warnings and block the order's packing-plan button. The
order-number packing endpoint also rejects incomplete bundles. Provisional
component verification remains provisional. Reload an order/custom shipment
after updating component measurements or mappings.

Mappings use normalized, case-insensitive SKU matching. Duplicate parents,
duplicate components, invalid quantities, and nested/circular bundles fail closed.
Flatten nested bundles to physical component SKUs when configuring them.

Test: `python -m unittest test_shipping_bundles` from backend. Tests use temporary
registries and do not read Shopify or write the working measurement registry.
