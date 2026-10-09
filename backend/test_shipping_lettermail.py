import unittest
from unittest.mock import patch, AsyncMock
from app.shipping_lettermail import assess
from app.shipping_delivery import classify_delivery
from app.shopify_client import ShopifyClient
from app.shipping_registry import expand_order
from app.shipping_v57_service import plan_payload
from app.shipping_optimizer import build_packing_plan

def fixture(price='18',quantity=1):
    return {'id':'gid://shopify/Order/1','name':'#1','shippingAddress':{'countryCodeV2':'CA'},
        'shippingLines':{'edges':[{'node':{'title':'Flat Rate Lettermail (No Tracking)','code':'Flat Rate Lettermail (No Tracking)'}}]},
        'lineItems':{'edges':[{'node':{'sku':'TEST','quantity':quantity,'currentQuantity':quantity,'unfulfilledQuantity':quantity,'requiresShipping':True,
            'originalUnitPriceSet':{'shopMoney':{'amount':price,'currencyCode':'CAD'}}}}]}}

class LettermailTests(unittest.TestCase):
    def setUp(self):
        p=patch('app.shipping_registry.lookup_sku',return_value=[{'sku':'TEST'}]);p.start();self.addCleanup(p.stop)
    def assess(self,order):return assess(order,classify_delivery(order))
    def test_exact_order_service_and_code_fallback(self):
        order=fixture();self.assertTrue(self.assess(order)['selected'])
        order['shippingLines']['edges'][0]['node']['title']='Economy'
        self.assertTrue(self.assess(order)['selected'])
    def test_tags_never_override_ups(self):
        order=fixture();order['tags']=['Low Cost Shipping']
        order['shippingLines']['edges']=[{'node':{'title':'UPS Standard','code':'UPS'}}]
        self.assertIsNone(self.assess(order))
    def test_mixed_removed_and_incomplete_services(self):
        order=fixture();order['shippingLines']['edges'].append({'node':{'title':'UPS Standard'}})
        self.assertFalse(self.assess(order)['selected'])
        combined=fixture();combined['shippingLines']['edges'][0]['node']['title']='UPS Standard + Lettermail'
        self.assertFalse(self.assess(combined)['selected'])
        order['shippingLines']['edges'][1]['node']['isRemoved']=True
        self.assertTrue(self.assess(order)['selected'])
        order['shippingLines']['pageInfo']={'hasNextPage':True}
        self.assertFalse(self.assess(order)['selected'])
    def test_ceiling_uses_whole_order_before_discounts(self):
        self.assertFalse(any('exceeds' in w for w in self.assess(fixture('50'))['warnings']))
        self.assertTrue(any('exceeds' in w for w in self.assess(fixture('25.01',2))['warnings']))
        self.assertEqual(self.assess(fixture('2',9))['merchandise_value_cad'],'18')
        self.assertTrue(any('9 physical' in w for w in self.assess(fixture('2',9))['warnings']))
    def test_unknown_value_destination_and_no_remaining_units(self):
        order=fixture();order['shippingAddress']={}
        item=order['lineItems']['edges'][0]['node'];item.pop('originalUnitPriceSet');item['unfulfilledQuantity']=0
        result=self.assess(order)
        self.assertIsNone(result['merchandise_value_cad'])
        self.assertEqual(result['physical_units'],0)
        self.assertEqual(len(result['warnings']),3)
    def test_unsuitable_alias_source(self):
        with patch('app.shipping_registry.lookup_sku',side_effect=[[{'packaging_source_sku':'SOURCE'}],[{'lettermail_unsuitable':True}]]):
            self.assertTrue(any('unsuitable' in w for w in self.assess(fixture())['warnings']))
    def test_pickup_not_overridden(self):
        self.assertFalse(assess(fixture(),{'type':'pickup'})['selected'])

class IntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_order_lookup_blocks_optimizers_and_preserves_order_lines(self):
        client=ShopifyClient()
        client._query=AsyncMock(side_effect=[{'orders':{'edges':[{'node':fixture()}]}},Exception('No scope')])
        with patch('app.shipping_registry.lookup_sku',return_value=[{'sku':'TEST'}]):
            order=await client.fetch_order_for_shipping('1')
        self.assertEqual(order['delivery']['type'],'lettermail')
        expanded=expand_order(order)
        self.assertEqual(len(expanded['line_items']),1)
        self.assertEqual(expanded['physical_packages'],[])
        for fn in (plan_payload,build_packing_plan):
            with self.assertRaisesRegex(ValueError,'Lettermail selected'):fn(expanded)
