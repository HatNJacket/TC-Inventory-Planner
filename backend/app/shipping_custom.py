"""Build a shipment from registry SKUs without reading or creating a Shopify order."""
from .shipping_registry import expand_order, lookup_sku


def custom_shipment(payload):
    items = payload.get('items')
    if not isinstance(items, list) or not items or len(items) > 50:
        raise ValueError('Add between 1 and 50 SKUs to the custom shipment.')
    reference = str(payload.get('reference') or 'Custom shipment').strip()
    if not reference or len(reference) > 80:
        raise ValueError('Shipment reference must be between 1 and 80 characters.')
    lines = []
    total = 0
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            raise ValueError('Each shipment item needs a SKU and quantity.')
        sku = str(item.get('sku') or '').strip()
        quantity = item.get('quantity')
        if not sku or len(sku) > 100:
            raise ValueError('Enter a valid SKU for every item.')
        if isinstance(quantity, bool) or not isinstance(quantity, int) or not 1 <= quantity <= 100:
            raise ValueError(f'{sku}: quantity must be a whole number between 1 and 100.')
        total += quantity
        if total > 100:
            raise ValueError('Custom shipments can contain up to 100 product units.')
        records = lookup_sku(sku)
        if not records:
            raise ValueError(f'{sku} is not in the Package Database. Add its package data first.')
        lines.append({'id': f'custom-{index}', 'sku': sku, 'title': records[0].get('product_name') or sku,
                      'quantity': quantity, 'requires_shipping': True})
    return expand_order({'name': reference, 'source': 'custom', 'shipping_method': 'Custom shipment', 'line_items': lines})
