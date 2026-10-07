import unittest
from unittest.mock import AsyncMock, patch
from app.shipping_delivery import classify_delivery
from app.shipping_registry import expand_order
from app.shipping_v57_service import plan_payload
from app.shipping_optimizer import build_packing_plan
from app.shopify_client import ShopifyClient


def connection(*methods):
    return {'edges':[{'node':{'status':'OPEN','deliveryMethod':{'methodType':m}}} for m in methods], 'pageInfo':{'hasNextPage':False}}


def order_line(title, **extra):
    return {'shippingLines':{'edges':[{'node':{'title':title,**extra}}]}}


class DeliveryTests(unittest.TestCase):
    def test_official_pickup_and_carrier_pickup_point_are_different(self):
        self.assertEqual(classify_delivery({},connection('PICK_UP'))['type'],'pickup')
        for kind in ['SHIPPING','PICKUP_POINT','LOCAL']:
            self.assertTrue(classify_delivery({},connection(kind))['packing_allowed'])

    def test_mixed_and_incomplete_do_not_hide_shipping_items_as_pickup(self):
        self.assertEqual(classify_delivery({},connection('PICK_UP','SHIPPING'))['type'],'mixed')
        self.assertEqual(classify_delivery({},connection('PICK_UP','UNKNOWN'))['type'],'unknown')
        c=connection('PICK_UP');c['pageInfo']['hasNextPage']=True
        self.assertEqual(classify_delivery({},c)['type'],'unknown')

    def test_active_delivery_overrides_historic_pickup(self):
        c=connection('SHIPPING','PICK_UP');c['edges'][1]['node']['status']='CLOSED'
        self.assertEqual(classify_delivery(order_line('Local pickup'),c)['type'],'shipping')

    def test_fallback_labels_and_status(self):
        for title in ['Pickup','Local pickup','Pick up at Telescopes Canada','In-store pickup']:
            self.assertEqual(classify_delivery(order_line(title))['type'],'pickup',title)
        for title in ['Free Shipping','UPS Standard','Delivery to pickup point']:
            self.assertEqual(classify_delivery(order_line(title))['type'],'shipping',title)
        self.assertEqual(classify_delivery({'displayFulfillmentStatus':'READY_FOR_PICKUP'})['type'],'pickup')
        self.assertEqual(classify_delivery(order_line('Local pickup',isRemoved=True))['type'],'unknown')

    def test_no_address_or_physical_product_flag_is_not_pickup_evidence(self):
        self.assertEqual(classify_delivery({'shippingAddress':None,'lineItems':{'edges':[{'node':{'requiresShipping':True}}]}})['type'],'unknown')

    def test_truncated_shipping_lines_and_missing_method_require_review(self):
        order=order_line('Local pickup')
        order['shippingLines']['pageInfo']={'hasNextPage':True}
        self.assertEqual(classify_delivery(order)['type'],'unknown')
        self.assertEqual(classify_delivery(order,connection('SHIPPING'))['type'],'shipping')
        self.assertEqual(classify_delivery({},connection(None))['type'],'unknown')

    def test_pickup_does_not_expand_packages_or_reach_either_optimizer(self):
        order={'delivery':classify_delivery({},connection('PICK_UP')),'line_items':[{'sku':'MISSING','quantity':2,'requires_shipping':True}]}
        with patch('app.shipping_registry.lookup_sku',side_effect=AssertionError('Must not load package data')):
            expanded=expand_order(order)
        self.assertEqual(expanded['physical_packages'],[])
        for fn in [plan_payload,build_packing_plan]:
            with self.assertRaisesRegex(ValueError,'Pickup order'):
                fn(expanded)


class ShopifyDeliveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_delivery_query_and_permission_fallback(self):
        raw={'id':'gid://shopify/Order/1','name':'#1',**order_line('Local pickup')}
        for delivery_response in [{'order':{'fulfillmentOrders':connection('PICK_UP')}},Exception('Access denied')]:
            client=ShopifyClient()
            client._query=AsyncMock(side_effect=[{'orders':{'edges':[{'node':raw}]}},delivery_response])
            result=await client.fetch_order_for_shipping('1')
            self.assertEqual(result['delivery']['type'],'pickup')
            self.assertEqual(client._query.await_count,2)
            self.assertIn('deliveryMethod',client._query.call_args_list[1].args[0])

if __name__=='__main__':unittest.main()
