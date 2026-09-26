# Separately packaged shipping bundles

`app/data/shipping_bundle_registry.json` maps an ordered parent SKU to component
SKUs and quantities per bundle. It is maintained separately from package records,
so saving product measurements does not remove mappings. Shipping → Manage Bundles
provides searchable, paginated mappings and an editor for component SKUs and
quantities per bundle. Component SKUs can be searched in the Package Database or
entered directly. Missing package data produces warnings; measurements are not
invented or marked verified by saving a mapping.

User edits are atomic overrides in `app/data/shipping_bundle_overrides.json`,
ignored by Git. Back up this file with other shipping runtime data. It persists
across app restarts and normal source updates on this backend, but is not a cloud
database or automatically shared with separate installations. Default mapping
deletions persist as tombstones. Saves include a revision check and user/time;
both save and removal require the shipping identity and Planner authorization.
The existing single-process local-runtime assumption applies to writes.

Changing a mapping clears loaded packing drafts in the current Shipping screen
to prevent reuse of an old plan. Other open screens must reload their shipments.
The parent SKU is immutable during edits; remove/recreate to correct it. Removal
does not delete measurements: subsequent packing reverts to the parent package
record, if present. Nested bundles are rejected with instructions to flatten them.

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
