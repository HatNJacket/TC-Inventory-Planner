"""
Operating Expenses Module

Tracks non-COGS overhead (payroll, lease, utilities, software, marketing, …)
and allocates a portion of it to a date range so per-order/per-SKU margin
reports can subtract overhead alongside FIFO COGS.

An expense can either be a single-day cost (``expense_date`` only) or cover
a period (``period_start``/``period_end`` — typical for monthly lease or
salary). The allocator computes the share of each expense that overlaps the
analysis window and returns the total overhead in CAD for that window.
"""
import calendar
import csv
import io
import logging
from datetime import date, datetime
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


CATEGORIES = [
    'payroll', 'lease', 'utilities', 'software', 'shipping',
    'marketing', 'insurance', 'professional_services', 'office', 'other',
]

SUPPORTED_CURRENCIES = ['CAD', 'USD', 'EUR']


def _f(val):
    if val is None:
        return 0.0
    return float(val)


def _resolve_fx_rate(cursor, currency):
    """Look up the stored ``{currency}CAD`` rate. Returns 1.0 for CAD."""
    if not currency:
        return 1.0
    currency = currency.upper()
    if currency == 'CAD':
        return 1.0
    pair = currency + 'CAD'
    cursor.execute("SELECT effective_rate FROM fx_rates WHERE currency_pair = ?", pair)
    row = cursor.fetchone()
    if not row or not row[0]:
        raise ValueError(
            "No FX rate stored for %s. Set it on the Vendors page first." % pair
        )
    return _f(row[0])


def _convert_to_cad(cursor, currency, amount_foreign, explicit_fx_rate=None):
    """Returns ``(amount_cad, fx_rate, currency_upper)``.

    If ``explicit_fx_rate`` is provided it wins (used for bulk imports that
    pre-locked a rate). Otherwise we look up the stored rate.
    """
    currency = (currency or 'CAD').upper()
    amount_foreign = _f(amount_foreign)
    if currency == 'CAD':
        return round(amount_foreign, 2), 1.0, 'CAD'
    fx_rate = _f(explicit_fx_rate) if explicit_fx_rate else _resolve_fx_rate(cursor, currency)
    return round(amount_foreign * fx_rate, 2), fx_rate, currency


def _parse_date(val):
    if val is None or val == '':
        return None
    if isinstance(val, (date, datetime)):
        return val if isinstance(val, date) and not isinstance(val, datetime) else val.date()
    return datetime.fromisoformat(str(val)[:10]).date()


def _serialize_row(d):
    """Format a DB row dict into JSON-friendly types."""
    out = dict(d)
    out['amount_cad'] = _f(out.get('amount_cad'))
    out['fx_rate'] = _f(out.get('fx_rate')) or 1.0
    out['currency'] = (out.get('currency') or 'CAD').upper()
    out['amount_foreign'] = (
        _f(out['amount_foreign']) if out.get('amount_foreign') is not None
        else out['amount_cad']  # back-fill for legacy rows entered pre-multi-currency
    )
    for k in ('expense_date', 'period_start', 'period_end'):
        v = out.get(k)
        out[k] = v.isoformat() if v else None
    if out.get('created_at') is not None:
        out['created_at'] = out['created_at'].isoformat()
    return out


# --- CRUD ------------------------------------------------------------------

def add_expense(db, expense_date, category, amount_foreign, currency='CAD',
                description=None, vendor=None, period_start=None,
                period_end=None, notes=None, fx_rate=None):
    """Insert one expense.

    Pass ``amount_foreign`` in ``currency``. The CAD equivalent is computed
    using the stored FX rate (or ``fx_rate`` if explicitly supplied for
    audit purposes — useful when a bill was paid at a locked rate).
    """
    conn = db._get_connection()
    try:
        cursor = conn.cursor()
        amount_cad, resolved_rate, curr = _convert_to_cad(
            cursor, currency, amount_foreign, fx_rate
        )
        cursor.execute("""
            INSERT INTO operating_expenses
            (expense_date, category, description, amount_cad, vendor,
             period_start, period_end, notes,
             currency, fx_rate, amount_foreign)
            OUTPUT INSERTED.id
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, expense_date, category, description, amount_cad,
            vendor, period_start, period_end, notes,
            curr, resolved_rate, round(_f(amount_foreign), 2))
        new_id = cursor.fetchone()[0]
        conn.commit()
        return {
            'status': 'ok', 'id': new_id,
            'amount_cad': amount_cad, 'fx_rate': resolved_rate, 'currency': curr,
        }
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def update_expense(db, expense_id, **fields):
    """Update an expense. If ``currency`` and/or ``amount_foreign`` change
    we also recompute ``amount_cad`` and ``fx_rate``.
    """
    simple_allowed = {
        'expense_date', 'category', 'description', 'vendor',
        'period_start', 'period_end', 'notes',
    }

    money_currency = fields.pop('currency', None)
    money_amount_foreign = fields.pop('amount_foreign', None)
    money_fx_rate = fields.pop('fx_rate', None)

    sets = []
    params = []
    conn = db._get_connection()
    try:
        cursor = conn.cursor()

        # Rebuild money columns when any of currency / amount / fx_rate changes.
        if money_currency is not None or money_amount_foreign is not None or money_fx_rate is not None:
            cursor.execute(
                "SELECT currency, amount_foreign, amount_cad FROM operating_expenses WHERE id = ?",
                expense_id,
            )
            row = cursor.fetchone()
            if not row:
                return {'status': 'error', 'message': 'Expense not found.'}
            cur_currency, cur_foreign, cur_cad = row
            new_currency = (money_currency or cur_currency or 'CAD').upper()
            new_foreign = _f(money_amount_foreign) if money_amount_foreign is not None else _f(cur_foreign or cur_cad)
            amount_cad, resolved_rate, curr = _convert_to_cad(
                cursor, new_currency, new_foreign, money_fx_rate
            )
            sets.extend(['currency = ?', 'fx_rate = ?', 'amount_foreign = ?', 'amount_cad = ?'])
            params.extend([curr, resolved_rate, round(new_foreign, 2), amount_cad])

        for k, v in fields.items():
            if k not in simple_allowed:
                continue
            sets.append(k + ' = ?')
            params.append(v)

        if not sets:
            return {'status': 'error', 'message': 'No fields to update.'}
        params.append(expense_id)

        cursor.execute(
            "UPDATE operating_expenses SET " + ', '.join(sets) + " WHERE id = ?",
            *params
        )
        conn.commit()
        return {'status': 'ok', 'updated': expense_id}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def delete_expense(db, expense_id):
    conn = db._get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM operating_expenses WHERE id = ?", expense_id)
        conn.commit()
        return {'status': 'ok', 'deleted': expense_id}
    finally:
        conn.close()


def list_expenses(db, start_date=None, end_date=None, category=None,
                  limit=500, offset=0, materialize=True):
    """List expenses, auto-materializing recurring templates first so the
    list always reflects 'what should be there as of today'. Set
    ``materialize=False`` to skip (e.g. when you're already inside a
    larger transaction)."""
    if materialize:
        try:
            materialize_recurring_expenses(db)
        except Exception as e:
            logger.warning("materialize_recurring_expenses failed: %s", e)

    conn = db._get_connection()
    try:
        cursor = conn.cursor()
        where = []
        params = []
        # Match if the expense_date OR the period [period_start, period_end]
        # overlaps the requested window.
        if start_date and end_date:
            where.append("""(
                (expense_date BETWEEN ? AND ?)
                OR (period_start IS NOT NULL AND period_end IS NOT NULL
                    AND period_start <= ? AND period_end >= ?)
            )""")
            params.extend([start_date, end_date, end_date, start_date])
        elif start_date:
            where.append("(expense_date >= ? OR period_end >= ?)")
            params.extend([start_date, start_date])
        elif end_date:
            where.append("(expense_date <= ? OR period_start <= ?)")
            params.extend([end_date, end_date])
        if category:
            where.append("category = ?")
            params.append(category)

        clause = (" WHERE " + " AND ".join(where)) if where else ""
        cursor.execute("""
            SELECT id, expense_date, category, description, amount_cad,
                   vendor, period_start, period_end, notes, created_at,
                   ISNULL(currency, 'CAD') AS currency,
                   ISNULL(fx_rate, 1.0) AS fx_rate,
                   amount_foreign, template_id
            FROM operating_expenses
            %s
            ORDER BY expense_date DESC, id DESC
            OFFSET ? ROWS FETCH NEXT ? ROWS ONLY
        """ % clause, *(params + [offset, limit]))
        cols = [d[0] for d in cursor.description]
        results = [_serialize_row(dict(zip(cols, row))) for row in cursor.fetchall()]

        cursor.execute("""
            SELECT COUNT(*), ISNULL(SUM(amount_cad), 0)
            FROM operating_expenses
            %s
        """ % clause, *params)
        count_row = cursor.fetchone()

        return {
            'expenses': results,
            'total': count_row[0] or 0,
            'total_amount_cad': _f(count_row[1]),
            'limit': limit,
            'offset': offset,
        }
    finally:
        conn.close()


def get_expenses_summary(db, start_date=None, end_date=None):
    """By-category totals + monthly totals for a date range."""
    conn = db._get_connection()
    try:
        cursor = conn.cursor()
        params = []
        date_filter = ""
        if start_date:
            date_filter += " AND expense_date >= ?"
            params.append(start_date)
        if end_date:
            date_filter += " AND expense_date <= ?"
            params.append(end_date)

        cursor.execute("""
            SELECT category, COUNT(*), SUM(amount_cad)
            FROM operating_expenses
            WHERE 1=1 %s
            GROUP BY category
            ORDER BY SUM(amount_cad) DESC
        """ % date_filter, *params)
        by_category = [
            {'category': r[0], 'count': r[1], 'amount_cad': _f(r[2])}
            for r in cursor.fetchall()
        ]

        cursor.execute("""
            SELECT FORMAT(expense_date, 'yyyy-MM') AS month,
                   COUNT(*), SUM(amount_cad)
            FROM operating_expenses
            WHERE 1=1 %s
            GROUP BY FORMAT(expense_date, 'yyyy-MM')
            ORDER BY month
        """ % date_filter, *params)
        monthly = [
            {'month': r[0], 'count': r[1], 'amount_cad': _f(r[2])}
            for r in cursor.fetchall()
        ]

        cursor.execute("""
            SELECT COUNT(*), ISNULL(SUM(amount_cad), 0)
            FROM operating_expenses
            WHERE 1=1 %s
        """ % date_filter, *params)
        tot = cursor.fetchone()

        return {
            'total_count': tot[0] or 0,
            'total_amount_cad': _f(tot[1]),
            'by_category': by_category,
            'monthly': monthly,
        }
    finally:
        conn.close()


# --- ALLOCATION ------------------------------------------------------------

def compute_allocated_overhead(db, start_date, end_date):
    """Total CAD overhead allocated to the window [start_date, end_date].

    For each expense:
      - If period_start AND period_end are set, overlap = days_overlap / period_length.
      - Otherwise it's a point-in-time cost; counts in full if expense_date
        falls inside the window, else 0.

    Returns ``{'total_cad': float, 'by_category': [{category, amount_cad}]}``.
    """
    if isinstance(start_date, str):
        start_date = _parse_date(start_date)
    if isinstance(end_date, str):
        end_date = _parse_date(end_date)
    if start_date is None or end_date is None:
        raise ValueError("compute_allocated_overhead needs both start_date and end_date")
    if end_date < start_date:
        return {'total_cad': 0.0, 'by_category': []}

    conn = db._get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id, expense_date, category, amount_cad, period_start, period_end
            FROM operating_expenses
            WHERE
                (period_start IS NULL AND expense_date BETWEEN ? AND ?)
                OR (period_start IS NOT NULL AND period_end IS NOT NULL
                    AND period_start <= ? AND period_end >= ?)
        """, start_date, end_date, end_date, start_date)

        by_category: Dict[str, float] = {}
        total = 0.0
        for row in cursor.fetchall():
            _id, exp_date, category, amount, p_start, p_end = row
            amount = _f(amount)
            if p_start is None or p_end is None:
                allocated = amount
            else:
                period_days = (p_end - p_start).days + 1
                if period_days <= 0:
                    continue
                overlap_start = max(p_start, start_date)
                overlap_end = min(p_end, end_date)
                overlap_days = (overlap_end - overlap_start).days + 1
                if overlap_days <= 0:
                    continue
                allocated = round(amount * overlap_days / period_days, 2)
            by_category[category] = by_category.get(category, 0.0) + allocated
            total += allocated

        return {
            'total_cad': round(total, 2),
            'by_category': [
                {'category': k, 'amount_cad': round(v, 2)}
                for k, v in sorted(by_category.items(), key=lambda x: -x[1])
            ],
            'start_date': start_date.isoformat(),
            'end_date': end_date.isoformat(),
        }
    finally:
        conn.close()


# --- CSV UPLOAD ------------------------------------------------------------

def upload_expenses_csv(db, csv_content, date_col, category_col, amount_col,
                       description_col=None, vendor_col=None,
                       period_start_col=None, period_end_col=None,
                       currency_col=None, default_currency='CAD'):
    """Bulk-insert expenses from a CSV. Each row's currency comes from
    ``currency_col`` if present, else falls back to ``default_currency``.
    The CAD equivalent is computed using the FX rate stored on the Vendors
    page (one rate per non-CAD currency)."""
    reader = csv.DictReader(io.StringIO(csv_content))
    parsed = []
    errors = []
    for i, row in enumerate(reader, start=2):
        try:
            d = _parse_date(row.get(date_col))
            cat = (row.get(category_col) or 'other').strip().lower()
            amt = float(str(row.get(amount_col, '0')).replace('$', '').replace(',', '').strip() or 0)
            if d is None or amt <= 0:
                errors.append({'row': i, 'reason': 'missing date or amount'})
                continue
            cur = (row.get(currency_col) if currency_col else None) or default_currency
            cur = cur.strip().upper() or 'CAD'
            if cur not in SUPPORTED_CURRENCIES:
                errors.append({'row': i, 'reason': 'unsupported currency: ' + cur})
                continue
            parsed.append({
                'expense_date': d,
                'category': cat,
                'amount_foreign': round(amt, 2),
                'currency': cur,
                'description': (row.get(description_col) or '').strip() if description_col else None,
                'vendor': (row.get(vendor_col) or '').strip() if vendor_col else None,
                'period_start': _parse_date(row.get(period_start_col)) if period_start_col else None,
                'period_end': _parse_date(row.get(period_end_col)) if period_end_col else None,
            })
        except Exception as e:
            errors.append({'row': i, 'reason': str(e)})

    if not parsed:
        return {'status': 'error', 'message': 'No valid rows found.', 'errors': errors}

    conn = db._get_connection()
    try:
        cursor = conn.cursor()

        # Resolve and cache one FX rate per distinct non-CAD currency before
        # the insert loop so a missing rate fails the whole upload cleanly.
        fx_cache = {'CAD': 1.0}
        for currency in {r['currency'] for r in parsed if r['currency'] != 'CAD'}:
            fx_cache[currency] = _resolve_fx_rate(cursor, currency)

        total_cad = 0.0
        for r in parsed:
            rate = fx_cache[r['currency']]
            amount_cad = round(r['amount_foreign'] * rate, 2)
            total_cad += amount_cad
            cursor.execute("""
                INSERT INTO operating_expenses
                (expense_date, category, description, amount_cad, vendor,
                 period_start, period_end, currency, fx_rate, amount_foreign)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, r['expense_date'], r['category'], r['description'],
                amount_cad, r['vendor'], r['period_start'], r['period_end'],
                r['currency'], rate, r['amount_foreign'])
        conn.commit()
        return {
            'status': 'ok',
            'inserted': len(parsed),
            'skipped': len(errors),
            'errors': errors[:25],
            'total_amount_cad': round(total_cad, 2),
            'fx_rates_used': {k: v for k, v in fx_cache.items() if k != 'CAD'},
        }
    except ValueError as e:
        conn.rollback()
        return {'status': 'error', 'message': str(e), 'errors': errors[:25]}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# --- RECURRING EXPENSE TEMPLATES -------------------------------------------

def _last_day_of_month(year, month):
    return calendar.monthrange(year, month)[1]


def _clamp_to_month(year, month, day_of_month):
    """Clamp a day-of-month to the actual length of that month (so a Day-31
    template lands on the last day of February, etc.)."""
    return date(year, month, min(day_of_month, _last_day_of_month(year, month)))


def _iter_template_dates(start_date, end_date, today, day_of_month):
    """Yield every materialization date for a template that is on or before
    ``today``. Limits to ``end_date`` if set. Iterates by calendar month."""
    if end_date is None or end_date > today:
        cap = today
    else:
        cap = end_date

    year, month = start_date.year, start_date.month
    while True:
        candidate = _clamp_to_month(year, month, day_of_month)
        if candidate > cap:
            break
        if candidate >= start_date:
            yield candidate
        # Next month
        if month == 12:
            year, month = year + 1, 1
        else:
            month += 1


def materialize_recurring_expenses(db, today=None):
    """Idempotently generate ``operating_expenses`` rows for active templates
    whose materialization dates have already passed.

    Each (template_id, expense_date) pair is created at most once — re-running
    is safe. Edits to a template only affect rows generated *after* the edit
    (already-generated rows stand on their own and stay editable individually).

    Note: if a generated row is deleted, the materializer will re-create it
    on the next run. Deactivate the template (or set ``end_date``) to stop
    that.
    """
    if today is None:
        today = date.today()

    conn = db._get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id, name, category, amount_foreign, currency, day_of_month,
                   vendor, notes, start_date, end_date
            FROM recurring_expense_templates
            WHERE active = 1 AND start_date <= ?
        """, today)
        templates = cursor.fetchall()

        # Pre-load existing (template_id, expense_date) pairs so we don't
        # round-trip per template.
        cursor.execute("""
            SELECT template_id, expense_date FROM operating_expenses
            WHERE template_id IS NOT NULL
        """)
        existing = set()
        for tid, ed in cursor.fetchall():
            existing.add((tid, ed))

        # Cache one FX rate per non-CAD currency seen.
        fx_cache = {'CAD': 1.0}
        created = 0

        for tpl in templates:
            (tid, name, category, amount_foreign, currency, day_of_month,
             vendor, notes, start_date, end_date) = tpl
            currency = (currency or 'CAD').upper()
            amount_foreign = _f(amount_foreign)

            if currency not in fx_cache:
                try:
                    fx_cache[currency] = _resolve_fx_rate(cursor, currency)
                except ValueError as e:
                    # Skip this template if its FX rate isn't set; surface in result.
                    fx_cache[currency] = None
                    logger.warning("Recurring template %s skipped: %s", name, e)
                    continue
            if fx_cache[currency] is None:
                continue
            rate = fx_cache[currency]

            for ed in _iter_template_dates(start_date, end_date, today, day_of_month):
                if (tid, ed) in existing:
                    continue
                amount_cad = round(amount_foreign * rate, 2)
                period_start = date(ed.year, ed.month, 1)
                period_end = date(ed.year, ed.month, _last_day_of_month(ed.year, ed.month))
                description = name + ' (auto)'
                cursor.execute("""
                    INSERT INTO operating_expenses
                    (expense_date, category, description, amount_cad, vendor,
                     period_start, period_end, notes,
                     currency, fx_rate, amount_foreign, template_id)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, ed, category, description, amount_cad, vendor,
                    period_start, period_end, notes,
                    currency, rate, round(amount_foreign, 2), tid)
                created += 1

        conn.commit()
        return {'created': created, 'templates_checked': len(templates)}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def list_recurring_templates(db, include_inactive=False):
    conn = db._get_connection()
    try:
        cursor = conn.cursor()
        where = "" if include_inactive else " WHERE active = 1"
        cursor.execute("""
            SELECT id, name, category, amount_foreign, currency, day_of_month,
                   vendor, notes, start_date, end_date, active, created_at, updated_at
            FROM recurring_expense_templates
            %s
            ORDER BY active DESC, name
        """ % where)
        cols = [d[0] for d in cursor.description]
        results = []
        for row in cursor.fetchall():
            d = dict(zip(cols, row))
            d['amount_foreign'] = _f(d['amount_foreign'])
            d['active'] = bool(d['active'])
            for k in ('start_date', 'end_date'):
                d[k] = d[k].isoformat() if d[k] else None
            for k in ('created_at', 'updated_at'):
                d[k] = d[k].isoformat() if d[k] else None
            results.append(d)
        return results
    finally:
        conn.close()


def add_recurring_template(db, name, category, amount_foreign, currency='CAD',
                           day_of_month=1, vendor=None, notes=None,
                           start_date=None, end_date=None):
    if not start_date:
        raise ValueError("start_date is required")
    if not (1 <= int(day_of_month) <= 31):
        raise ValueError("day_of_month must be 1-31")

    conn = db._get_connection()
    try:
        cursor = conn.cursor()
        # Validate the currency is resolvable now to fail fast (not at materialize).
        if (currency or 'CAD').upper() != 'CAD':
            _resolve_fx_rate(cursor, currency)
        cursor.execute("""
            INSERT INTO recurring_expense_templates
            (name, category, amount_foreign, currency, day_of_month,
             vendor, notes, start_date, end_date)
            OUTPUT INSERTED.id
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, name, category, round(_f(amount_foreign), 2),
            (currency or 'CAD').upper(), int(day_of_month),
            vendor, notes, start_date, end_date)
        new_id = cursor.fetchone()[0]
        conn.commit()
        return {'status': 'ok', 'id': new_id}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def update_recurring_template(db, template_id, **fields):
    """Edits apply to *future* materialization only — already-generated rows
    are not retroactively modified."""
    allowed = {
        'name', 'category', 'amount_foreign', 'currency', 'day_of_month',
        'vendor', 'notes', 'start_date', 'end_date', 'active',
    }
    sets = []
    params = []
    for k, v in fields.items():
        if k not in allowed:
            continue
        if k == 'amount_foreign':
            v = round(_f(v), 2)
        elif k == 'currency':
            v = (v or 'CAD').upper()
        elif k == 'day_of_month':
            v = int(v)
            if not (1 <= v <= 31):
                raise ValueError("day_of_month must be 1-31")
        elif k == 'active':
            v = 1 if v else 0
        sets.append(k + ' = ?')
        params.append(v)
    if not sets:
        return {'status': 'error', 'message': 'No fields to update.'}
    sets.append("updated_at = GETUTCDATE()")
    params.append(template_id)

    conn = db._get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE recurring_expense_templates SET " + ', '.join(sets) + " WHERE id = ?",
            *params,
        )
        conn.commit()
        return {'status': 'ok', 'updated': template_id}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def delete_recurring_template(db, template_id, cascade=False):
    """Hard-delete a template. Generated ``operating_expenses`` rows survive
    (their ``template_id`` is set NULL) unless ``cascade`` is True."""
    conn = db._get_connection()
    try:
        cursor = conn.cursor()
        if cascade:
            cursor.execute("DELETE FROM operating_expenses WHERE template_id = ?", template_id)
        else:
            cursor.execute("UPDATE operating_expenses SET template_id = NULL WHERE template_id = ?", template_id)
        cursor.execute("DELETE FROM recurring_expense_templates WHERE id = ?", template_id)
        conn.commit()
        return {'status': 'ok', 'deleted': template_id, 'cascaded': cascade}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
