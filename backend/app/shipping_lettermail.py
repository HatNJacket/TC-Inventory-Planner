"""Selected-service handling only: never changes checkout, tags, or orders."""
import re
from decimal import Decimal, InvalidOperation


def is_lettermail(line):
    return any(re.search(r'\bletter[ -]?mail\b', str(line.get(k) or ''), re.I) for k in ('title','code'))


def assess(order, delivery):
    lines=[e.get('node') or {} for e in (order.get('shippingLines') or {}).get('edges',[])]
    lines=[line for line in lines if not line.get('isRemoved')]
    if not any(is_lettermail(line) for line in lines):
        return None
    warnings=[]
    exclusive=bool(lines) and all(is_lettermail(line) for line in lines)
    if any(re.search(r'\b(ups|fedex|purolator|dhl|canpar|usps|expedited|xpresspost)\b', str(line.get('title') or '')+' '+str(line.get('code') or ''),re.I) for line in lines):
        exclusive=False
    incomplete=((order.get('shippingLines') or {}).get('pageInfo') or {}).get('hasNextPage',False)
    selected=exclusive and not incomplete and delivery['type']=='shipping'
    if not selected:
        warnings.append('Lettermail appears alongside another service or delivery details are uncertain. Review Shopify shipping charges; do not assume the whole order is Lettermail.')
    if (order.get('shippingAddress') or {}).get('countryCodeV2')!='CA':
        warnings.append('Destination is not confirmed as Canada. Review before sending domestic Lettermail.')
    units=0;value=Decimal('0');value_known=True
    from .shipping_registry import lookup_sku
    for edge in (order.get('lineItems') or {}).get('edges',[]):
        item=edge.get('node') or {}
        if item.get('requiresShipping') is False:continue
        current=item.get('currentQuantity',item.get('quantity',0)) or 0
        units+=max(0,item.get('unfulfilledQuantity',current) or 0)
        if current<=0:continue
        money=(item.get('originalUnitPriceSet') or {}).get('shopMoney') or {}
        try:
            amount=Decimal(str(money.get('amount')))
            if money.get('currencyCode')!='CAD' or not amount.is_finite() or amount<0:raise ValueError()
            value+=amount*current
        except (InvalidOperation,ValueError):value_known=False
        records=lookup_sku(item.get('sku') or '')
        if not records:warnings.append(f"{item.get('sku') or 'Missing SKU'}: no package record; suitability needs a manual check.")
        for record in records:
            sources=lookup_sku(record['packaging_source_sku']) if record.get('packaging_source_sku') else []
            if any(r.get('lettermail_unsuitable') for r in [record,*sources]):
                warnings.append(f"{item.get('sku')}: marked unsuitable for Lettermail. Review the selected service.")
    if units>1:warnings.append(f'{units} physical units to pack. Check all items together in the final pouch; a single-item fit is not enough.')
    if units==0:warnings.append('No unfulfilled physical units remain. Do not dispatch this order again.')
    if ((order.get('lineItems') or {}).get('pageInfo') or {}).get('hasNextPage'):
        value_known=False;warnings.append('Order items are incomplete. Review the full order in Shopify.')
    if not value_known:warnings.append('Cannot confirm merchandise value in CAD. Check the $50 ceiling manually.')
    elif value>50:warnings.append(f'Merchandise value ${value:.2f} CAD exceeds the $50 Lettermail ceiling (before discounts).')
    return {'selected':selected,'warnings':list(dict.fromkeys(warnings)), 'physical_units':units,
            'merchandise_value_cad':str(value) if value_known else None, 'ceiling_cad':50}
