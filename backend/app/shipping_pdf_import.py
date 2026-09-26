"""Extract tabular carton receipts from text PDFs; ambiguous layouts fail closed."""
import csv
import io
import re
from decimal import Decimal, ROUND_HALF_UP


def uline_text_to_import(text):
    """Recognize the invoice layout, validate billed each-counts, ignore supplies."""
    if not re.search(r'uline\.(?:ca|com)', text, re.I) or not all(word in text for word in ('QTY', 'ORDERED', 'U/M', 'ITEM NUMBER')):
        return None
    items = []
    for line in text.splitlines():
        match = re.match(r'^([\d,]+)\s+([A-Z]+)\s+(?:([\d,]+)\s+)?([SH]-[\w-]+)\s+(.+?)\s+([\d,]*\.\d{2})\s+([\d,]*\.\d{2})\s*$', line.strip())
        if match:
            qty, unit, back, sku, description, price, total = match.groups()
            items.append({'quantity':int(qty.replace(',', '')), 'back':int((back or '0').replace(',', '')), 'unit':unit,
                          'sku':sku, 'description':description, 'price':Decimal(price.replace(',', '')), 'total':Decimal(total.replace(',', ''))})
        elif items and re.match(r'^(?:CORRUGATED BOXES|MAILERS)\b', line.strip(), re.I):
            items[-1]['description'] += ' ' + line.strip()
        elif re.search(r'\b[SH]-\d+\b', line):
            raise ValueError('A Uline item line could not be read safely. Use the CSV template for this invoice.')
    tables = [['Description', 'Quantity']]
    skipped = []
    for item in items:
        if not re.search(r'\b(?:BOXES|MAILERS)\b', item['description'], re.I):
            skipped.append(item['sku'])
            continue
        qty = item['quantity'] - item['back']
        if qty < 0 or item['unit'] not in {'EA', 'C'}:
            raise ValueError(f"Uline {item['sku']}: unsupported quantity/unit. Verify individual box totals using CSV.")
        divisor = 100 if item['unit'] == 'C' else 1
        expected = (Decimal(qty) * item['price'] / divisor).quantize(Decimal('.01'), rounding=ROUND_HALF_UP)
        if expected != item['total']:
            raise ValueError(f"Uline {item['sku']}: quantity and line price do not agree. Verify the quantity using CSV.")
        # Validate every box row individually so a missing size cannot be silently skipped.
        tables_to_csv([[['Description', 'Quantity'], [item['description'], str(qty)]]], 'in')
        tables.append([item['description'], str(qty)])
    notes = ['Uline quantities are ordered less backordered. Check them against the boxes actually delivered before applying. C is a per-100 price unit; listed quantities remain individual boxes.']
    if skipped:
        notes.append('Excluded non-box items: ' + ', '.join(skipped) + '.')
    return {'csv':tables_to_csv([tables], 'in'), 'notes':notes}


def _header(value):
    return re.sub(r'[^a-z0-9]+', ' ', str(value or '').lower()).strip()


def tables_to_csv(tables, default_unit):
    if default_unit not in {'cm', 'in'}:
        raise ValueError('Select the units used for box dimensions on the PDF (cm or in).')
    output = []
    for table in tables:
        mapping = None
        for raw in table:
            cells = [str(value or '').strip() for value in raw]
            headings = [_header(value) for value in cells]
            qty = next((i for label in ('qty shipped','quantity shipped','shipped','qty received','quantity received','quantity','qty','on hand') for i,h in enumerate(headings) if h == label), None)
            axes = [next((i for i,h in enumerate(headings) if h in (axis, f'{axis} cm', f'{axis} in')), None) for axis in ('length','width','height')]
            description = next((i for i,h in enumerate(headings) if h in ('description','item description','product description','size','dimensions','box size')), None)
            if qty is not None and (all(i is not None for i in axes) or description is not None):
                header_units = {headings[i].split()[-1] for i in axes if i is not None and headings[i].split()[-1] in {'cm','in'}}
                if len(header_units) > 1:
                    raise ValueError('PDF dimension columns use mixed units. Correct them in the CSV template.')
                mapping = (qty, axes, description, next(iter(header_units), default_unit))
                continue
            if mapping is None or not any(cells):
                continue
            qty, axes, description, header_unit = mapping
            def cell(index):
                return cells[index] if index is not None and index < len(cells) else ''
            if all(i is not None for i in axes):
                dims = [cell(i) for i in axes]
                dimension_text = ' '.join(dims)
                if not any(dims):
                    continue
            else:
                dimension_text = cell(description)
                match = re.search(r'(?<![\d.,/])(\d+(?:\.\d+)?)\s*["″]?\s*[xX×]\s*(\d+(?:\.\d+)?)\s*["″]?\s*[xX×]\s*(\d+(?:\.\d+)?)(?![\d./])', dimension_text)
                if not match:
                    if re.search(r'\d.*[xX×].*\d', dimension_text):
                        raise ValueError('A PDF row has ambiguous dimensions. Use decimal measurements in the CSV template (for example 10.5 instead of 10 1/2).')
                    continue
                dims = list(match.groups())
            unit = 'cm' if re.search(r'\bcm\b', dimension_text, re.I) else ('in' if re.search(r'\bin(?:ch(?:es)?)?\b|["″]', dimension_text, re.I) else header_unit)
            dims = [re.sub(r'\s*(?:cm|in(?:ch(?:es)?)?|["″])\s*$', '',value,flags=re.I) for value in dims]
            # Numbers only. Do not infer bundle/case quantities or silently multiply.
            quantity = cell(qty).replace(',', '').replace('\n', ' ').strip()
            output.append([*dims, unit, quantity])
    if not output:
        raise ValueError('No readable box-size/quantity table found. Use a text PDF with Description/Dimensions and Qty/Shipped columns, or the CSV template. Scanned PDFs are not supported yet.')
    stream = io.StringIO()
    writer = csv.writer(stream)
    writer.writerow(['length','width','height','unit','quantity'])
    writer.writerows(output)
    return stream.getvalue()


def extract_pdf_import(content, default_unit):
    import pdfplumber
    try:
        with pdfplumber.open(io.BytesIO(content)) as document:
            if len(document.pages) > 20:
                raise ValueError('Import PDFs of 20 pages or fewer.')
            uline = uline_text_to_import('\n'.join(page.extract_text() or '' for page in document.pages))
            if uline is not None:
                return uline
            tables = []
            for page in document.pages:
                found = page.extract_tables()
                if not found:
                    found = page.extract_tables({'vertical_strategy':'text','horizontal_strategy':'text'})
                tables.extend(found)
            return {'csv':tables_to_csv(tables, default_unit), 'notes':[]}
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError('Could not read this PDF. Use an unlocked PDF containing selectable text.') from exc


def pdf_to_csv(content, default_unit):
    return extract_pdf_import(content, default_unit)['csv']
