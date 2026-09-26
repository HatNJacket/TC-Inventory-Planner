import csv
import io
import unittest
from app.shipping_pdf_import import uline_text_to_import

HEADER = 'uline.ca\nQTY BACK EXTENDED\nU/M ITEM NUMBER DESCRIPTION UNIT PRICE\nORDERED ORDERED PRICE\n'


class UlineTests(unittest.TestCase):
    def rows(self, text):
        return list(csv.DictReader(io.StringIO(uline_text_to_import(HEADER + text)['csv'])))

    def test_each_and_per_hundred_are_individual_quantities(self):
        rows = self.rows('60 EA S-100 40 X 18 X 8" CORRUGATED BOXES 6.27 376.20\n200 C S-200 6 X 6 X 4" WHITE MAILERS 163.00 326.00')
        self.assertEqual([r['quantity'] for r in rows], ['60', '200'])
        self.assertEqual([r['unit'] for r in rows], ['in', 'in'])

    def test_continuations_and_non_boxes(self):
        result = uline_text_to_import(HEADER + '50 EA S-100 16 X 12 X 12" 275 LB HEAVY DUTY 3.48 174.00\nCORRUGATED BOXES\n1 EA H-200 STEEL CARTON STAND - 42 X 18 X 23" .00 .00\n6 RL S-300 PAPER ROLL - 12" X 720\' 25.00 150.00')
        self.assertEqual(len(list(csv.DictReader(io.StringIO(result['csv'])))), 1)
        self.assertIn('H-200', result['notes'][1])
        self.assertIn('S-300', result['notes'][1])

    def test_backorder_subtracted_and_prices_checked(self):
        self.assertEqual(self.rows('60 EA 10 S-100 10 X 10 X 10" BOXES 2.00 100.00')[0]['quantity'], '50')
        with self.assertRaisesRegex(ValueError, 'do not agree'):
            self.rows('200 C S-100 10 X 10 X 10" BOXES 163.00 32600.00')

    def test_bad_units_and_malformed_items_fail_closed(self):
        with self.assertRaisesRegex(ValueError, 'unsupported'):
            self.rows('2 PK S-100 10 X 10 X 10" BOXES 10.00 20.00')
        with self.assertRaisesRegex(ValueError, 'could not be read'):
            self.rows('2 EA S-100 10 X 10 X 10" BOXES unreadable')

    def test_unrelated_format_is_not_assumed_uline(self):
        self.assertIsNone(uline_text_to_import('Description Qty\n10 x 10 x 10 5'))

    def test_box_with_missing_dimensions_cannot_be_silently_omitted(self):
        with self.assertRaises(ValueError):
            self.rows('2 EA S-100 10 X 10 X 10" BOXES 2.00 4.00\n2 EA S-200 CORRUGATED BOXES 2.00 4.00')


if __name__ == '__main__':
    unittest.main()
