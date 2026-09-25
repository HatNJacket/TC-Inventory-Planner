"""Extract tabular carton receipts from text PDFs; ambiguous layouts fail closed."""
import csv
import io
import re


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


def pdf_to_csv(content, default_unit):
    import pdfplumber
    try:
        with pdfplumber.open(io.BytesIO(content)) as document:
            if len(document.pages) > 20:
                raise ValueError('Import PDFs of 20 pages or fewer.')
            tables = []
            for page in document.pages:
                found = page.extract_tables()
                if not found:
                    found = page.extract_tables({'vertical_strategy':'text','horizontal_strategy':'text'})
                tables.extend(found)
            return tables_to_csv(tables, default_unit)
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError('Could not read this PDF. Use an unlocked PDF containing selectable text.') from exc
