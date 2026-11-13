"""Editable linear schedules tied to approved Inventaris purchase lines."""
from calendar import monthrange
from datetime import date, timedelta
from decimal import Decimal
from fractions import Fraction

from .db import audit, client, insert, lock
from .domain import Problem, money, text_field

TABLE = 'assets.invoice_desk_depreciation_plans'
SOURCE = """
    FROM accounting.invoice_lines l
    JOIN accounting.invoices i ON i.id=l.invoice_id
    LEFT JOIN accounting.accounts a ON a.id=l.account_id
    LEFT JOIN accounting.staff_booking_categories k ON k.id=l.booking_category_id
    WHERE i.client_id=%s AND i.status IN ('approved','posted')
      AND i.invoice_type='purchase' AND i.invoice_kind<>'credit_note'
      AND NOT EXISTS (SELECT 1 FROM accounting.general_ledger_entries rev
                  WHERE rev.reverses_entry_id=i.general_ledger_entry_id AND rev.status='posted')
      AND l.net_amount>0
      AND (a.account_code='002' OR (l.account_id IS NULL AND
           (k.category_code='GNUCASH_002' OR lower(btrim(k.category_name)) IN ('inventaris','002 inventaris'))))
"""
COLUMNS = """SELECT l.id, l.description, l.net_amount, l.vat_amount,
                   i.invoice_number, i.invoice_date, i.currency, i.seller_name_snapshot"""


def inventory(c, client_id, summary=False, offset=0):
    client(c, client_id)
    if summary:
        return c.execute(f'SELECT EXISTS(SELECT 1 {SOURCE}) AS available', (client_id,)).fetchone()
    rows = c.execute(f"""
        SELECT src.*, p.asset_name, p.revision, p.loss_type, p.loss_date FROM (
            {COLUMNS} {SOURCE} ORDER BY i.invoice_date DESC, l.id DESC LIMIT 51 OFFSET %s
        ) src LEFT JOIN {TABLE} p ON p.invoice_line_id=src.id
        ORDER BY src.invoice_date DESC, src.id DESC
    """, (client_id, offset)).fetchall()
    return {'rows': rows[:50], 'more': len(rows) > 50}


def source_line(c, client_id, line_id, locked=False):
    row = c.execute(COLUMNS + SOURCE + ' AND l.id=%s' + (' FOR SHARE OF i,l' if locked else ''),
                    (client_id, line_id)).fetchone()
    if not row:
        raise Problem('Brak zatwierdzonego zakupu Inventaris u tego klienta.', 404)
    return row


def add_months(day, count):
    year, month = divmod(day.year * 12 + day.month - 1 + count, 12)
    return date(year, month+1, min(day.day, monthrange(year, month+1)[1]))


def schedule(plan):
    start = plan['in_use_date']
    end = add_months(start, plan['useful_life_months'])
    cost = int(plan['acquisition_cost'] * 100)
    base = cost - int(plan['residual_value'] * 100)
    loss_date = plan.get('loss_date')
    cutoff = min(end, loss_date) if loss_date else end
    periods = []
    total = Fraction()
    cursor = start
    while cursor < end:
        nxt = min(add_months(cursor.replace(day=1), 1), end)
        days = monthrange(cursor.year, cursor.month)[1]
        total += Fraction((nxt-cursor).days, days)
        used_until = min(nxt, cutoff)
        if used_until > cursor:
            periods.append((used_until-timedelta(days=1), Fraction((used_until-cursor).days, days)))
        cursor = nxt
    running, prior = Fraction(), 0
    months, years = [], {}
    def euros(cents):
        return Decimal(cents).scaleb(-2)
    for period_end, weight in periods:
        running += weight
        exact = base * running / total
        cents = (2 * exact.numerator + exact.denominator) // (2 * exact.denominator)
        movement, balance = euros(cents-prior), euros(cost-cents)
        months.append({'date': period_end, 'amount': movement, 'book_value': balance})
        year = years.setdefault(period_end.year, {'year': period_end.year, 'amount': Decimal('0.00')})
        year['amount'] += movement
        year['book_value'] = balance
        prior = cents
    return {'end_date': months[-1]['date'] if months else None,
            'stopped_by_loss': bool(loss_date and loss_date < end),
            'remaining_value': euros(cost-prior), 'months': months, 'years': list(years.values())}


def details(c, client_id, line_id):
    line = source_line(c, client_id, line_id)
    plan = c.execute(f'SELECT * FROM {TABLE} WHERE invoice_line_id=%s', (line_id,)).fetchone()
    return {'line': line, 'plan': plan, 'schedule': schedule(plan) if plan else None}


def save(c, client_id, line_id, data, staff_id):
    keys = {'asset_name', 'in_use_date', 'acquisition_cost', 'residual_value', 'useful_life_months', 'revision'}
    allowed = keys | {'notes', 'loss_type', 'loss_date'}
    if not isinstance(data, dict) or not keys <= set(data) <= allowed:
        raise Problem('Formularz amortyzacji jest niekompletny.')
    name = text_field(data, 'asset_name', 255, required=True)
    try:
        start = date.fromisoformat(data['in_use_date'])
        if not 1900 <= start.year <= 2300:
            raise ValueError
    except (TypeError, ValueError):
        raise Problem('Podaj poprawną datę przyjęcia do używania.')
    cost = money(data['acquisition_cost'], 'Wartość początkowa')
    residual = money(data['residual_value'], 'Wartość końcowa')
    if cost <= 0 or residual > cost:
        raise Problem('Wartość początkowa musi być dodatnia i nie mniejsza od końcowej.')
    months, revision = data['useful_life_months'], data['revision']
    if type(months) is not int or not 1 <= months <= 600:
        raise Problem('Podaj okres od 1 do 600 miesięcy.')
    if type(revision) is not int or revision < 0:
        raise Problem('Odśwież harmonogram przed zapisem.')

    lock(c, f'invoice-desk-inventaris:{line_id}')
    line = source_line(c, client_id, line_id, locked=True)
    before = c.execute(f'SELECT * FROM {TABLE} WHERE invoice_line_id=%s FOR UPDATE', (line_id,)).fetchone()
    if revision != (before['revision'] if before else 0):
        raise Problem('Harmonogram został zmieniony. Wczytaj go ponownie.', 409)
    notes = before['notes'] if before else None
    if 'notes' in data:
        raw_notes = data['notes']
        if raw_notes is not None and (not isinstance(raw_notes, str) or '\x00' in raw_notes):
            raise Problem('Notatka musi zawierać poprawny tekst.')
        notes = text_field(data, 'notes', 1000)
    loss_type = before['loss_type'] if before else None
    loss_date = before['loss_date'] if before else None
    if 'loss_type' in data or 'loss_date' in data:
        loss_type = data.get('loss_type')
        if loss_type not in (None, 'stolen', 'lost'):
            raise Problem('Wybierz poprawny rodzaj utraty środka trwałego.')
        raw_date = data.get('loss_date')
        if loss_type:
            try:
                loss_date = date.fromisoformat(raw_date)
                if not 1900 <= loss_date.year <= 2300:
                    raise ValueError
            except (TypeError, ValueError):
                raise Problem('Podaj poprawną datę utraty środka trwałego.')
        elif raw_date not in (None, ''):
            raise Problem('Wybierz rodzaj utraty albo usuń datę utraty.')
        else:
            loss_date = None
    if loss_date and loss_date < start:
        raise Problem('Data utraty nie może być wcześniejsza niż data przyjęcia do używania.')
    values = dict(asset_name=name, in_use_date=start, acquisition_cost=cost, residual_value=residual,
                  useful_life_months=months, revision=revision+1, updated_by=staff_id,
                  notes=notes, loss_type=loss_type, loss_date=loss_date)
    if before:
        plan = c.execute(f"""UPDATE {TABLE}
            SET asset_name=%(asset_name)s, in_use_date=%(in_use_date)s,
                acquisition_cost=%(acquisition_cost)s, residual_value=%(residual_value)s,
                useful_life_months=%(useful_life_months)s, revision=%(revision)s,
                notes=%(notes)s, loss_type=%(loss_type)s, loss_date=%(loss_date)s,
                updated_by=%(updated_by)s, updated_at=now()
            WHERE invoice_line_id=%(line_id)s RETURNING *
        """, {**values, 'line_id': line_id}).fetchone()
    else:
        plan = insert(c, TABLE, {**values, 'invoice_line_id': line_id})
    audit(c, TABLE, line_id, staff_id, plan, before)
    return {'line': line, 'plan': plan, 'schedule': schedule(plan)}
