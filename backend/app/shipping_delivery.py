"""Read-only delivery classification for Shipping Planner, never product flags."""
import re

PICKUP_MESSAGE = 'Pickup order — packing not needed. No shipping carton or label is required.'


def classify_delivery(order, fulfillment_orders=None):
    methods = []
    source = 'unavailable'
    if fulfillment_orders is not None:
        if (fulfillment_orders.get('pageInfo') or {}).get('hasNextPage'):
            return result('unknown', 'incomplete_fulfillment_orders')
        nodes = [e.get('node') or {} for e in fulfillment_orders.get('edges', [])]
        nodes = [n for n in nodes if n.get('status') != 'CANCELLED']
        active = [n for n in nodes if n.get('status') != 'CLOSED']
        nodes = active or nodes
        methods = [(n.get('deliveryMethod') or {}).get('methodType', 'UNKNOWN') for n in nodes]
        if methods:
            source = 'fulfillment_delivery_method'
    if not methods:
        source = 'shipping_line'
        if ((order.get('shippingLines') or {}).get('pageInfo') or {}).get('hasNextPage'):
            return result('unknown', 'incomplete_shipping_lines')
        lines = [e.get('node') or {} for e in (order.get('shippingLines') or {}).get('edges', [])]
        for line in lines:
            if line.get('isRemoved'): continue
            category = str(line.get('deliveryCategory') or '').upper().replace('-', '_')
            if category in {'PICK_UP', 'PICKUP', 'LOCAL_PICKUP'}:
                methods.append('PICK_UP'); continue
            if category in {'SHIPPING', 'PICKUP_POINT', 'LOCAL'}:
                methods.append(category); continue
            label = str(line.get('title') or '').strip().lower()
            code = str(line.get('code') or '').strip().lower()
            if re.search(r'\b(pickup point|pick.up point|access point|parcel locker)\b', label):
                methods.append('PICKUP_POINT')
            elif code in {'pickup', 'pick_up', 'local_pickup'} or re.search(r'^(?:local |in[- ]store |store |customer )?pick[ -]?up(?:$| in\b| at\b| -)', label):
                methods.append('PICK_UP')
            elif re.search(r'\b(shipping|canada post|ups|fedex|purolator|dhl|canpar|usps)\b', label):
                methods.append('SHIPPING')
            else:
                methods.append('UNKNOWN')
        if order.get('displayFulfillmentStatus') == 'READY_FOR_PICKUP':
            if not methods or set(methods) == {'UNKNOWN'}:
                methods = ['PICK_UP']; source = 'ready_for_pickup'
            elif 'PICK_UP' not in methods:
                methods.append('PICK_UP')
    kinds = set(methods)
    if kinds and kinds <= {'PICK_UP', 'RETAIL', 'NONE'}:
        return result('pickup' if 'PICK_UP' in kinds else 'not_required', source)
    if 'PICK_UP' in kinds and kinds & {'SHIPPING', 'PICKUP_POINT', 'LOCAL'}:
        return result('mixed', source)
    if kinds and kinds <= {'SHIPPING', 'PICKUP_POINT', 'LOCAL'}:
        return result('shipping', source)
    return result('unknown', source)


def result(kind, source):
    messages = {'pickup': PICKUP_MESSAGE, 'not_required': 'No shipping packing is needed for this order.',
                'mixed': 'This order mixes pickup and shipping. Review its fulfillment groups in Shopify before packing.',
                'unknown': 'Delivery type could not be confirmed. Check the order in Shopify and the app’s fulfillment-order access, then reload.',
                'shipping': ''}
    return {'type': kind, 'source': source, 'packing_allowed': kind == 'shipping', 'message': messages[kind]}


def ensure_packing_allowed(order):
    delivery = order.get('delivery') or {}
    if delivery.get('packing_allowed') is False:
        raise ValueError(delivery.get('message') or 'This order is not eligible for shipping packing.')
