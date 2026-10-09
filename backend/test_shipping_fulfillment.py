import unittest
from unittest.mock import AsyncMock
from app.shipping_fulfillment import select_group
from app.shopify_client import ShopifyClient

def connection(*groups):return {'edges':[{'node':g} for g in groups],'pageInfo':{'hasNextPage':False}}
def group(identifier,items,status='OPEN',method='SHIPPING'):
    return {'id':identifier,'status':status,'assignedLocation':{'name':'Warehouse'},'deliveryMethod':{'methodType':method},'fulfillmentHolds':[],
            'lineItems':connection(*[{'remainingQuantity':q,'lineItem':{'id':i,'sku':i}} for i,q in items])}
def order():return {'id':'ORDER','name':'#50221','shippingLines':connection({'title':'Free Shipping'}),
                   'lineItems':connection(*[{'id':i,'sku':i,'quantity':3,'currentQuantity':3,'unfulfilledQuantity':3} for i in ['S50','Accessory']])}

class SelectionTests(unittest.TestCase):
    def test_cancelled_order_and_invalid_quantities_block(self):
        raw=order();raw['cancelledAt']='2026-10-01'
        self.assertTrue(select_group(raw,connection(group('A',[('S50',1)])))['blocked'])
        for qty in [None,True,-1]:
            self.assertTrue(select_group(order(),connection(group('A',[('S50',qty)])))['blocked'])
        raw=order();raw['lineItems']['edges'][0]['node']['unfulfilledQuantity']=None
        self.assertTrue(select_group(raw,connection(group('A',[('S50',1)])))['blocked'])

    def test_lettermail_counts_selected_group_only(self):
        from app.shipping_lettermail import assess
        from unittest.mock import patch
        raw=order();raw['shippingLines']=connection({'title':'Lettermail'})
        with patch('app.shipping_registry.lookup_sku',return_value=[]):
            result=assess(raw,{'type':'shipping'},{'S50':1})
        self.assertEqual(result['physical_units'],1)
        self.assertFalse(any('Accessory:' in w for w in result['warnings']))

    def test_split_requires_selection(self):
        state=select_group(order(),connection(group('A',[('Accessory',1)]),group('B',[('S50',1)])))
        self.assertTrue(state['blocked']);self.assertEqual(state['quantities'],{})
    def test_selected_remaining_quantity_not_whole_sku(self):
        c=connection(group('A',[('S50',2)]),group('B',[('S50',1)]))
        self.assertEqual(select_group(order(),c,'B')['quantities'],{'S50':1})
    def test_single_open_auto_selected_and_held_excluded(self):
        c=connection(group('A',[('Accessory',1)],'ON_HOLD'),group('B',[('S50',1)]),group('C',[('S50',1)],'CLOSED'))
        state=select_group(order(),c)
        self.assertEqual(state['selected_id'],'B');self.assertEqual(len(state['groups']),2)
        self.assertTrue(select_group(order(),c,'A')['blocked'])
    def test_stale_requested_selection_never_switches_silently(self):
        self.assertTrue(select_group(order(),connection(group('B',[('S50',1)])),'GONE')['blocked'])
    def test_missing_scope_and_incomplete_data_block(self):
        self.assertTrue(select_group(order(),None)['blocked'])
        c=connection(group('A',[('S50',1)]));c['pageInfo']['hasNextPage']=True
        self.assertTrue(select_group(order(),c)['blocked'])
        c['pageInfo']['hasNextPage']=False;c['edges'][0]['node']['lineItems']['pageInfo']['hasNextPage']=True
        self.assertTrue(select_group(order(),c)['blocked'])
    def test_unknown_line_and_excess_quantity_block(self):
        for items in [[('MISSING',1)],[('S50',4)]]:
            self.assertTrue(select_group(order(),connection(group('A',items)))['blocked'])
    def test_scheduled_cancelled_and_hold_objects_excluded(self):
        for status in ['SCHEDULED','CANCELLED','CLOSED','ON_HOLD']:
            self.assertTrue(select_group(order(),connection(group('A',[('S50',1)],status)))['blocked'])
        g=group('A',[('S50',1)]);g['fulfillmentHolds']=[{'reason':'OTHER'}]
        self.assertTrue(select_group(order(),connection(g))['blocked'])

class IntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_live_order_shape_excludes_accessories(self):
        c=ShopifyClient();groups=connection(group('A',[('Accessory',1)]),group('B',[('S50',1)]))
        for selected in [None,'B']:
            c._query=AsyncMock(side_effect=[{'orders':connection(order())},{'order':{'fulfillmentOrders':groups}}])
            loaded=await c.fetch_order_for_shipping('50221',selected)
            quantities={i['sku']:i['pack_quantity'] for i in loaded['line_items']}
            self.assertEqual(quantities,{'S50':1 if selected else 0,'Accessory':0})
            self.assertEqual(loaded['delivery']['packing_allowed'],bool(selected))
