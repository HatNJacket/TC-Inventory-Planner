"""Stock import and replenishment tests with isolated inventory files."""
import csv
import io
import json
import tempfile
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch
from fastapi.testclient import TestClient
from app import shipping_optimizer as stock, main
from app.shipping_stock_import import preview_import, apply_import
from app.shipping_pdf_import import tables_to_csv, pdf_to_csv


def sample_pdf():
    # Small, real text PDF containing a ruled stock table (no extra test dependency).
    commands = ['0.5 w']
    for x in (50,150,250,350,450): commands.append(f'{x} 640 m {x} 700 l S')
    for y in (640,660,680,700): commands.append(f'50 {y} m 450 {y} l S')
    for y, row in [(686,['Length','Width','Height','Qty']),(666,['10','10','10','12']),(646,['20','10','5','8'])]:
        for x, value in zip((55,155,255,355),row): commands.append(f'BT /F1 10 Tf {x} {y} Td ({value}) Tj ET')
    stream='\n'.join(commands).encode()
    objects=[b'<< /Type /Catalog /Pages 2 0 R >>',b'<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
             b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>',
             b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>',b'<< /Length '+str(len(stream)).encode()+b' >>\nstream\n'+stream+b'\nendstream']
    data=b'%PDF-1.4\n'; offsets=[0]
    for index,obj in enumerate(objects,1):
        offsets.append(len(data)); data+=f'{index} 0 obj\n'.encode()+obj+b'\nendobj\n'
    start=len(data)
    data+=b'xref\n0 6\n0000000000 65535 f \n'+b''.join(f'{offset:010} 00000 n \n'.encode() for offset in offsets[1:])
    return data+f'trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n{start}\n%%EOF'.encode()


class StockTests(unittest.TestCase):
    def setUp(self):
        folder=tempfile.TemporaryDirectory();self.addCleanup(folder.cleanup)
        root=Path(folder.name)
        for name,path in [('APP_DATA_DIR',root),('CARTON_INVENTORY_PATH',root/'inventory.json'),('CARTON_CATALOG_PATH',root/'catalog.json')]:
            p=patch.object(stock,name,path);p.start();self.addCleanup(p.stop)
        stock.CARTON_CATALOG_PATH.write_text(json.dumps({'unit':'inches','cartons':[[10,10,10],[20,10,5]]}))
        self.dim=[10,10,10]
        self.csv='length,width,height,unit,quantity\n25.4,25.4,25.4,cm,5\n'

    def save(self,quantity,**kwargs):
        return stock.set_carton_stock_bulk([{'dimensions':self.dim,'quantity':quantity,**kwargs}], 'Tester')

    def test_reorder_boundary_target_and_uncounted(self):
        result=self.save(8,minimum=10,target=40)
        self.assertEqual(result['shopping_list'][0]['order_quantity'],32)
        self.assertEqual(self.save(10)['shopping_list'][0]['order_quantity'],30)
        self.assertEqual(self.save(11)['shopping_list'],[])
        self.assertEqual(self.save(0)['shopping_list'][0]['order_quantity'],40)
        result=self.save(None)
        self.assertEqual(result['shopping_list'],[])
        self.assertEqual(result['needs_count_count'],1)
        self.assertIsNone(self.save(2,minimum=None,target=None)['inventory'][0]['minimum'])

    def test_bad_batch_does_not_partially_write(self):
        self.save(8)
        before=stock.CARTON_INVENTORY_PATH.read_bytes()
        with self.assertRaises(ValueError):
            stock.set_carton_stock_bulk([{'dimensions':self.dim,'quantity':20},{'dimensions':[20,10,5],'quantity':2,'minimum':5,'target':5}])
        self.assertEqual(stock.CARTON_INVENTORY_PATH.read_bytes(),before)

    def test_preview_cm_receipt_and_idempotent_apply(self):
        self.save(8,minimum=10,target=40)
        before=stock.CARTON_INVENTORY_PATH.read_bytes()
        preview=preview_import(self.csv,'receive')
        self.assertTrue(preview['can_apply']);self.assertEqual(preview['rows'][0]['new_quantity'],13)
        self.assertEqual(stock.CARTON_INVENTORY_PATH.read_bytes(),before)
        request={'csv':self.csv,'mode':'receive','revision':preview['revision'],'request_id':str(uuid.uuid4())}
        for _ in range(2):
            result=apply_import(request,'Packer')
            self.assertEqual(result['inventory'][0]['quantity'],13)
            self.assertEqual(result['shopping_list'],[])
        history=stock._ensure_inventory()['adjustments']
        self.assertEqual(history[-1]['change_type'],'delivery_import')
        self.assertEqual(history[-1]['user_name'],'Packer')

    def test_stale_preview_conflicts(self):
        self.save(8)
        preview=preview_import(self.csv,'count')
        self.save(9)
        with self.assertRaises(stock.InventoryConflict):
            apply_import({'csv':self.csv,'mode':'count','revision':preview['revision'],'request_id':str(uuid.uuid4())},'Packer')
        self.assertEqual(stock.get_carton_catalog()['inventory'][0]['quantity'],9)

    def test_duplicates_unknown_negative_and_unknown_delivery(self):
        self.assertFalse(preview_import(self.csv,'receive')['can_apply'])
        for text in (self.csv+'25.4,25.4,25.4,cm,2\n',self.csv+'99,99,99,in,2\n',self.csv.replace(',cm,5',',cm,-1')):
            preview=preview_import(text,'count')
            self.assertFalse(preview['can_apply']);self.assertTrue(preview['errors'])

    def test_count_and_policy_import(self):
        self.save(8,minimum=10,target=40)
        text='length,width,height,unit,quantity,minimum,target\n10,10,10,in,4,6,20\n'
        preview=preview_import(text,'count')
        result=apply_import({'csv':text,'mode':'count','revision':preview['revision'],'request_id':str(uuid.uuid4())},'Tester')
        self.assertEqual(result['shopping_list'][0]['order_quantity'],16)

    def test_concurrent_receipts_do_not_lose_counts(self):
        self.save(0)
        def receive(_): return stock.set_carton_stock_bulk([{'dimensions':self.dim,'quantity':1}],mode='receive')
        with ThreadPoolExecutor(max_workers=4) as pool:list(pool.map(receive,range(8)))
        self.assertEqual(stock.get_carton_catalog()['inventory'][0]['quantity'],8)

    def test_pdf_description_and_explicit_cm_headers(self):
        text=tables_to_csv([[['Description','Qty shipped'],['Carton 10 x 10 x 10 in','12']]],'cm')
        self.assertEqual(preview_import(text,'count')['rows'][0]['new_quantity'],12)
        text=tables_to_csv([[['Length cm','Width cm','Height cm','Qty'],['25.4','25.4','25.4','3']]],'in')
        self.assertTrue(preview_import(text,'count')['can_apply'])
        with self.assertRaises(ValueError):tables_to_csv([], 'in')
        with self.assertRaises(ValueError):
            tables_to_csv([[['Description','Qty'],['Box 10 x 10 x 1/2','5']]],'in')

    def test_real_pdf_and_authenticated_file_endpoint(self):
        text=pdf_to_csv(sample_pdf(),'in')
        preview=preview_import(text,'count')
        self.assertEqual(len(preview['rows']),2)
        self.assertTrue(preview['can_apply'])
        main.app.dependency_overrides[main.verify_token]=lambda:'test'
        main.app.dependency_overrides[main.current_shipping_user]=lambda:'Packer'
        self.addCleanup(main.app.dependency_overrides.clear)
        client=TestClient(main.app);self.addCleanup(client.close)
        response=client.post('/api/shipping/cartons/import/file',files={'file':('invoice.pdf',sample_pdf(),'application/pdf')},data={'mode':'count','unit':'in'})
        self.assertEqual(response.status_code,200)
        payload=response.json()
        self.assertTrue(payload['can_apply'])
        self.assertEqual(client.post('/api/shipping/cartons/import/apply',json={**payload,'request_id':str(uuid.uuid4())}).status_code,200)
        self.assertEqual(stock.get_carton_catalog()['inventory'][0]['quantity'],12)


if __name__=='__main__':unittest.main()
