"""New carton creation uses temporary inventory and never touches live state."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from fastapi.testclient import TestClient
from app import main, shipping_optimizer as stock
from app.shipping_stock_import import preview_import


class AddCartonTests(unittest.TestCase):
    def setUp(self):
        folder=tempfile.TemporaryDirectory();self.addCleanup(folder.cleanup)
        root=Path(folder.name)
        for name,path in [('APP_DATA_DIR',root),('CARTON_INVENTORY_PATH',root/'inventory.json'),('CARTON_CATALOG_PATH',root/'catalog.json')]:
            p=patch.object(stock,name,path);p.start();self.addCleanup(p.stop)
        stock.CARTON_CATALOG_PATH.write_text(json.dumps({'unit':'inches','cartons':[[10,10,10]]}))
        main.app.dependency_overrides[main.verify_token]=lambda:'test'
        main.app.dependency_overrides[main.current_shipping_user]=lambda:'Tester'
        self.addCleanup(main.app.dependency_overrides.clear)
        self.client=TestClient(main.app);self.addCleanup(self.client.close)

    def add(self, **changes):
        payload={'dimensions':[25.4,12.7,7.62],'unit':'cm','quantity':10,'minimum':12,'target':40,
                 'revision':stock.get_carton_catalog()['revision'],**changes}
        return self.client.post('/api/shipping/cartons',json=payload)

    def test_creation_conversion_persistence_audit_and_reorder(self):
        seed=stock.CARTON_CATALOG_PATH.read_bytes()
        response=self.add();self.assertEqual(response.status_code,200)
        row=next(r for r in response.json()['inventory'] if r['key']=='3x5x10')
        self.assertEqual(row['quantity'],10)
        self.assertEqual(row['order_quantity'],30)
        self.assertIn((3,5,10),stock.load_catalog())
        self.assertEqual(stock.CARTON_CATALOG_PATH.read_bytes(),seed)
        self.assertEqual(stock.get_carton_catalog()['count'],2)
        audit=stock._ensure_inventory()['adjustments'][-1]
        self.assertEqual(audit['change_type'],'carton_created')
        self.assertEqual(audit['user_name'],'Tester')
        preview=preview_import('length,width,height,unit,quantity\n3,5,10,in,5\n','receive')
        self.assertTrue(preview['can_apply'])
        self.assertEqual(preview['rows'][0]['new_quantity'],15)

    def test_rotated_and_converted_duplicates_do_not_overwrite_stock(self):
        self.assertEqual(self.add().status_code,200)
        before=stock.CARTON_INVENTORY_PATH.read_bytes()
        self.assertEqual(self.add(unit='in',dimensions=[10,3,5],quantity=99).status_code,400)
        self.assertEqual(self.add(dimensions=[25.4,25.4,25.4]).status_code,400)
        self.assertEqual(stock.CARTON_INVENTORY_PATH.read_bytes(),before)

    def test_invalid_inputs_atomic(self):
        stock.get_carton_catalog()
        before=stock.CARTON_INVENTORY_PATH.read_bytes()
        for change in ({'unit':'feet'},{'dimensions':[1,2]},{'dimensions':[True,2,3]},
                       {'dimensions':[0,2,3]},{'dimensions':['NaN',2,3]},
                       {'quantity':-1},{'quantity':1.5},{'quantity':True},
                       {'minimum':4,'target':4},{'minimum':None,'target':10}):
            self.assertEqual(self.add(**change).status_code,400,change)
            self.assertEqual(stock.CARTON_INVENTORY_PATH.read_bytes(),before)

    def test_blank_stock_differs_from_zero(self):
        data=self.add(quantity=None,minimum=None,target=None).json()
        self.assertIsNone(next(r for r in data['inventory'] if r['key']=='3x5x10')['quantity'])
        data=self.add(dimensions=[4,5,6],unit='in',quantity=0).json()
        self.assertEqual(next(r for r in data['inventory'] if r['key']=='4x5x6')['quantity'],0)

    def test_conflict_and_identity(self):
        self.assertEqual(self.add(revision='stale').status_code,409)
        self.assertEqual(self.add(revision=None).status_code,409)
        del main.app.dependency_overrides[main.current_shipping_user]
        self.assertEqual(self.add().status_code,400)
        self.assertEqual(stock.get_carton_catalog()['count'],1)

    def test_new_carton_is_used_by_optimizer_and_stocktake(self):
        self.assertEqual(self.add().status_code,200)
        result=stock._optimize_stock_aware([stock.Package((9,4,2),verification_status='Verified — Warehouse')])
        self.assertEqual(sorted(result['carton']),[3,5,10])
        data=stock.set_carton_stock_bulk([{'dimensions':[3,5,10],'quantity':0}],user_name='Tester',
            mode='stocktake',expected_revision=stock.get_carton_catalog()['revision'])
        self.assertEqual(next(r for r in data['inventory'] if r['key']=='3x5x10')['quantity'],0)
        result=stock._optimize_stock_aware([stock.Package((9,4,2),verification_status='Verified — Warehouse')])
        self.assertEqual(sorted(result['carton']),[10,10,10])


if __name__=='__main__':unittest.main()
