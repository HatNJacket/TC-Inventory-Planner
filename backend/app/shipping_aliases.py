"""Explicit, single-hop packaging links. Shopify SKU identity is never changed."""
from .shipping_bundles import sku_key


def link_revision(link, targets):
    from .shipping_package_health import revision
    return revision({'link': link, 'targets': sorted(targets, key=lambda r: str(r.get('id')))})


def resolve(sku, records, bundles):
    rows = sorted([r for r in records if sku_key(r.get('sku')) == sku_key(sku)], key=lambda r: (str(r.get('part') or '').casefold(), str(r.get('id') or '')))
    links = [r for r in rows if r.get('packaging_source_sku')]
    if not links:
        return rows, None
    if len(rows) != 1:
        raise ValueError('A linked SKU must have exactly one registry record.')
    link = links[0]
    source = link['packaging_source_sku']
    if sku_key(source) == sku_key(sku) or sku_key(source) in bundles:
        raise ValueError('Choose an original physical SKU, not itself or a bundle parent.')
    targets = sorted([r for r in records if sku_key(r.get('sku')) == sku_key(source)], key=lambda r: (str(r.get('part') or '').casefold(), str(r.get('id') or '')))
    if not targets or any(r.get('packaging_source_sku') or r.get('shipping_behavior') == 'digital' for r in targets):
        raise ValueError('Packaging source must exist and have its own physical package records (no linked or digital sources).')
    return targets, link


def validate(records, bundles):
    for record in records:
        if record.get('packaging_source_sku'):
            if sku_key(record['sku']) in bundles:
                raise ValueError('A bundle parent cannot also be a packaging link.')
            resolve(record['sku'], records, bundles)
