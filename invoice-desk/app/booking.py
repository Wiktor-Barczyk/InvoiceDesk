"""One operational booking catalog; legacy ledger links are optional."""
import copy
from .domain import Problem, positive_id


def choices(refs):
    accounts = {a['id']: a for a in refs['accounts']}
    if 'booking_catalog' in refs:
        result = []
        for scope in refs['booking_catalog']:
            account = accounts.get(scope['account_id'])
            if not account:
                continue
            code = account['account_code']
            name = account['account_name'].strip().removeprefix(code+' ')
            result.append({**account, 'key': 'account:'+str(account['id']),
                'label': code+' '+name,
                'booking_category_id': None, 'account_id': account['id'],
                'invoice_type': scope['invoice_type'], 'default_vat_code_id': None})
        return sorted(result, key=lambda x: (x['account_code'], x['invoice_type']))
    mappings = {m['booking_category_id']: m for m in refs['mappings']}
    result = []
    seen = {}
    for category in refs['categories']:
        mapping = mappings.get(category['id'], {})
        account = accounts.get(mapping.get('account_id'))
        label_key = (category['category_name'].strip().casefold(), account['id'] if account else None)
        if label_key in seen:
            seen[label_key]['category_ids'].append(category['id'])
            continue
        result.append({'key': 'category:'+str(category['id']), 'label': category['category_name'],
            'booking_category_id': category['id'], 'category_ids': [category['id']], 'account_id': account['id'] if account else None,
            'account_code': account['account_code'] if account else None,
            'account_name': account['account_name'] if account else None,
            'default_vat_code_id': mapping.get('default_vat_code_id') if account else None})
        seen[label_key] = result[-1]
    linked = {m['account_id'] for m in refs['mappings']}
    result.extend({'key': 'account:'+str(a['id']), 'label': a['account_name'],
        'booking_category_id': None, 'account_id': a['id'], 'account_code': a['account_code'],
        'account_name': a['account_name'], 'default_vat_code_id': None} for a in refs['accounts'] if a['id'] not in linked)
    return result


def normalize(payload, refs, strict=True):
    """Accept old separate IDs too, but never silently approve conflicting IDs."""
    result = copy.deepcopy(payload)
    if not isinstance(result.get('lines'), list):
        return result, []
    if 'booking_catalog' in refs:
        active = {a['id'] for a in refs['accounts']}
        allowed = {x['account_id'] for x in refs['booking_catalog']
                   if x['invoice_type'] == result.get('invoice_type')} & active
        warnings = []
        for number, line in enumerate(result['lines'], 1):
            if not isinstance(line, dict):
                continue
            account = positive_id(line.get('account_id'), 'Konto', True)
            has_category = line.get('booking_category_id') not in (None, '', 0, '0')
            if account not in allowed or has_category:
                message = f'Pozycja {number}: wybierz konto GnuCash dla zakupu albo sprzedaży.'
                if strict:
                    raise Problem(message)
                warnings.append(message)
                line.update(account_id=None, booking_category_id=None)
        return result, warnings
    mappings = {m['booking_category_id']: m for m in refs['mappings']}
    accounts = {a['id'] for a in refs['accounts']}
    categories = {x['id'] for x in refs['categories']}
    warnings = []
    for number, line in enumerate(result['lines'], 1):
        if not isinstance(line, dict) or line.get('booking_category_id') in (None, '', 0, '0'):
            continue
        category = positive_id(line['booking_category_id'], 'Kategoria')
        if category not in categories:
            message = f'Pozycja {number}: wybierz aktywną kategorię / konto.'
            if strict:
                raise Problem(message)
            warnings.append(message)
            continue
        mapping = mappings.get(category)
        if not mapping or mapping['account_id'] not in accounts:
            # The category is the booking account. No additional ledger ID is required.
            line['account_id'] = None
            continue
        expected = mapping['account_id']
        if line.get('account_id') not in (None, '', expected, str(expected)):
            message = f'Pozycja {number}: konto nie odpowiada wybranej kategorii.'
            if strict:
                raise Problem(message)
            warnings.append(message+' Propozycję dopasowano do ustawionego powiązania.')
        line['account_id'] = expected
    return result, warnings


def set_mapping(c, client_id, category_id, account_id, expected_account_id, staff_id):
    # Imports are local to avoid a db <-> booking module cycle.
    from .db import references, audit, insert, lock
    lock(c, f'booking-map:{client_id}:{category_id}')
    refs = references(c, client_id)
    if not any(x['id'] == category_id for x in refs['categories']) or not any(x['id'] == account_id for x in refs['accounts']):
        raise Problem('Wybierz aktywną kategorię i konto dostępne do księgowania.')
    effective = next((m for m in refs['mappings'] if m['booking_category_id'] == category_id), None)
    current_account = effective['account_id'] if effective else None
    if current_account != expected_account_id:
        raise Problem('Powiązanie zostało zmienione. Otwórz ustawienie ponownie.', 409)
    old = c.execute('SELECT * FROM accounting.booking_category_account_mappings WHERE client_id=%s AND booking_category_id=%s FOR UPDATE', (client_id, category_id)).fetchone()
    if old:
        row = c.execute('UPDATE accounting.booking_category_account_mappings SET account_id=%s,is_active=true WHERE id=%s RETURNING *', (account_id, old['id'])).fetchone()
    else:
        row = insert(c, 'accounting.booking_category_account_mappings', {'client_id': client_id,
            'booking_category_id': category_id, 'account_id': account_id,
            'default_vat_code_id': effective['default_vat_code_id'] if effective else None})
    audit(c, 'accounting.booking_category_account_mappings', row['id'], staff_id, row, old)
    return references(c, client_id)
