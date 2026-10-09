"""Choose one Shopify fulfillment group; never merge unrelated open shipments."""
def select_group(order, connection, requested=None):
    groups=[];problem=''
    if connection is None:
        problem='Fulfillment groups could not be loaded. Check fulfillment-order access and reload before packing.'
    elif (connection.get('pageInfo') or {}).get('hasNextPage'):
        problem='More fulfillment groups exist than were loaded. Review in Shopify before packing.'
    for edge in (connection or {}).get('edges',[]):
        node=edge.get('node') or {}
        if node.get('status') in ('CLOSED','CANCELLED'):continue
        items=node.get('lineItems')
        if items is None or (items.get('pageInfo') or {}).get('hasNextPage'):
            problem='Fulfillment group items are incomplete. Review in Shopify before packing.'
        quantities={}
        display=[]
        for entry in (items or {}).get('edges',[]):
            item=entry.get('node') or {};line=item.get('lineItem') or {}
            qty=item.get('remainingQuantity')
            if not line.get('id') or type(qty) is not int or qty<0:
                problem='Fulfillment quantities could not be confirmed. Reload before packing.';continue
            quantities[line['id']]=quantities.get(line['id'],0)+qty
            if qty:display.append({'sku':line.get('sku') or 'Missing SKU','quantity':qty})
        eligible=bool(node.get('id')) and node.get('status') in ('OPEN','IN_PROGRESS') and not node.get('fulfillmentHolds') and bool(display)
        groups.append({'id':node.get('id'),'status':node.get('status'),'location':(node.get('assignedLocation') or {}).get('name',''),
                       'eligible':eligible,'items':display,'quantities':quantities,'delivery':node})
    available=[g for g in groups if g['eligible']]
    chosen=next((g for g in available if g['id']==requested),None) if requested else (available[0] if len(available)==1 else None)
    if requested and not chosen:problem='The selected fulfillment group is no longer available to pack. Choose an open group and reload.'
    if (order.get('lineItems',{}).get('pageInfo') or {}).get('hasNextPage'):
        problem='Order items are incomplete. Review the full order before packing.'
    known={e.get('node',{}).get('id'):e.get('node',{}) for e in order.get('lineItems',{}).get('edges',[])}
    if chosen:
        for line_id,qty in chosen['quantities'].items():
            line=known.get(line_id)
            remaining=(line or {}).get('unfulfilledQuantity')
            if line is None or type(remaining) is not int or qty>remaining:
                problem='Order and fulfillment quantities disagree. Reload before packing.'
    if order.get('cancelledAt'):
        problem='This order is cancelled. Do not pack it.'
    if problem:chosen=None
    message=problem or ('' if chosen else 'Choose the fulfillment group to pack.' if available else 'No open, unheld fulfillment group is available to pack.')
    return {'groups':[{k:v for k,v in g.items() if k not in ('quantities','delivery')} for g in groups],
            'selected_id':chosen['id'] if chosen else None,'message':message,
            'quantities':chosen['quantities'] if chosen else {},'selected_node':chosen['delivery'] if chosen else None,
            'blocked':not bool(chosen)}
