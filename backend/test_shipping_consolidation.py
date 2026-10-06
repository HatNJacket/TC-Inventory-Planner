import unittest
from unittest.mock import patch
from app import shipping_optimizer as so
from app import shipping_v57_service as svc
from app.shipping_consolidation import plan_shipments


def item(sku, behavior='carrier_ready', dims=(2,2,2), quantity=1, **extra):
    return dict(sku=sku, registry_id=sku, product_name=sku, dimensions_in=list(dims),
                verification_status='Verified — Warehouse', weight_kg=1,
                shipping_behavior=behavior, quantity=quantity, **extra)


class ConsolidationTests(unittest.TestCase):
    def plan(self, items, cartons=((2,2,4),), stocks=None):
        stocks=stocks if stocks is not None else [20]*len(cartons)
        inventory={'stock':{so._carton_key(c):s for c,s in zip(cartons,stocks) if s is not None}}
        with patch.object(so,'load_catalog',return_value=list(cartons)), patch.object(so,'_ensure_inventory',return_value=inventory), patch.object(svc,'load_registry',return_value=[]):
            return svc.plan_payload({'order_reference':'#TEST','items':items})

    def test_two_factory_boxes_consolidate(self):
        p=self.plan([item('A'),item('B')])
        self.assertEqual(p['total_shipping_packages'],1)
        box=p['shipping_summary']['packages'][0]
        self.assertEqual(box['package_type'],'warehouse_carton')
        self.assertEqual(box['calculated_weight_kg'],2)
        self.assertEqual(len(box['contents']),2)
        self.assertEqual(len(p['loose_result']['placements']),2)

    def test_single_factory_box_never_overboxed(self):
        p=self.plan([item('A')])
        self.assertEqual(p['shipping_summary']['packages'][0]['package_type'],'factory_carton')
        self.assertEqual(p['warehouse_results'],[])

    def test_must_ship_alone_is_separate(self):
        p=self.plan([item('A','must_ship_alone'),item('B'),item('C')])
        self.assertEqual(p['total_shipping_packages'],2)
        self.assertEqual(p['shipping_summary']['packages'][0]['contents'][0]['sku'],'A')

    def test_factory_and_standard_can_combine(self):
        p=self.plan([item('A'),item('B','standard')])
        self.assertEqual(p['total_shipping_packages'],1)

    def test_split_standard_order_across_two_cartons(self):
        p=self.plan([item('A','standard',quantity=4)])
        self.assertEqual(p['total_shipping_packages'],2)
        self.assertEqual(len(p['warehouse_results']),2)
        self.assertIsNone(p['loose_result'])
        self.assertEqual(sum(len(x['contents']) for x in p['shipping_summary']['packages']),4)

    def test_stock_consumed_across_plan_not_per_carton(self):
        p=self.plan([item('A',quantity=4)],stocks=[1])
        self.assertEqual(p['total_shipping_packages'],3)
        self.assertEqual(len(p['warehouse_results']),1)

    def test_standard_stock_shortage_blocks_complete_plan(self):
        p=self.plan([item('A','standard',quantity=4)],stocks=[1])
        self.assertIsNone(p['total_shipping_packages'])
        self.assertFalse(p['shipping_summary']['complete'])

    def test_uses_second_carton_size_when_smallest_stock_is_exhausted(self):
        p=self.plan([item('A','standard',quantity=4)],((2,2,4),(2,2,5)),[1,1])
        self.assertEqual(p['total_shipping_packages'],2)
        self.assertEqual(sorted(x['dimensions_in'] for x in p['shipping_summary']['packages']),[[2,2,4],[2,2,5]])

    def test_out_of_stock_and_no_fit_fall_back_to_own_boxes(self):
        for stocks,cartons in [([0],((2,2,4),)),([10],((1,1,1),))]:
            p=self.plan([item('A'),item('B')],cartons,stocks)
            self.assertEqual(p['total_shipping_packages'],2)
            self.assertFalse(p['warehouse_results'])

    def test_volume_tiebreak_and_count_priority(self):
        p=self.plan([item('A'),item('B')],((3,3,3),(2,2,4)))
        self.assertEqual(p['loose_result']['carton'],[2,2,4])
        p=self.plan([item('A',quantity=4)],((2,2,4),(4,4,4)))
        self.assertEqual(p['total_shipping_packages'],1)

    def test_packed_accessories_travel_with_host_once(self):
        p=self.plan([item('H','accessory_carrier'),item('A','standard',dims=(1,1,1),packed_inside_count=1,packed_into='H'),item('B')])
        box=p['shipping_summary']['packages'][0]
        self.assertEqual(p['total_shipping_packages'],1)
        self.assertEqual(box['calculated_weight_kg'],3)
        self.assertEqual(sorted(c['sku'] for c in box['contents']),['A','B','H'])
        self.assertEqual(len(p['loose_result']['placements']),2)

    def test_orphan_accessory_rejected(self):
        with self.assertRaises(ValueError):
            self.plan([item('A','standard',packed_inside_count=1,packed_into='missing')])

    def test_unknown_stock_and_missing_weight_are_explicit(self):
        a=item('A');a['weight_kg']=None
        p=self.plan([a,item('B')],stocks=[None])
        self.assertFalse(p['shipping_summary']['all_weights_complete'])
        self.assertIsNone(p['shipping_summary']['packages'][0]['calculated_weight_kg'])
        self.assertTrue(p['loose_result']['stock_warning'])

    def test_no_optimality_claim_on_timeout(self):
        with patch.object(so,'solve_packing',side_effect=lambda items,c,t: (None,[]) if len(items)>1 else (True,[((0,0,0),items[0])])):
            p=self.plan([item('A'),item('B')])
        self.assertEqual(p['total_shipping_packages'],2)
        self.assertFalse(p['search_complete'])

if __name__=='__main__':unittest.main()
