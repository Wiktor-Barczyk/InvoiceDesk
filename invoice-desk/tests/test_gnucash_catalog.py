from pathlib import Path

import pytest
from psycopg import Error

from test_flow import rig, upload, payload


SQL = (Path(__file__).resolve().parents[1] / 'database/gnucash_invoice_accounts.sql').read_text()


def test_gnucash_catalog_and_invoice_approval(rig):
    http = rig['http']
    client_id = rig['client']['id']
    archive = upload(rig, True)['id']
    assert http.post(f'/api/documents/{archive}/approve', json=payload(rig, archive)).status_code == 200
    archived_lines = http.get(f'/api/documents/{archive}').json()['invoice']['lines']
    with rig['db'].connect() as c:
        c.execute(SQL)
    try:
        def refs():
            return http.get('/api/references', params={'client_id': client_id}).json()

        before = refs()
        with rig['db'].connect() as c:
            c.execute(SQL)
        after = refs()
        assert after['booking_choices'] == before['booking_choices']
        options = {x['account_code']: x for x in after['booking_choices']}
        assert len(options) == 16 and '130' not in options
        assert options['002']['label'] == '002 Inventaris'
        assert options['430']['invoice_type'] == 'purchase'
        assert options['860']['invoice_type'] == 'sale'
        vat = {x['vat_code']: x for x in after['vat_codes']}
        assert vat['PORTAL_EXEMPT']['vat_rate_percent'] is None
        assert float(vat['PORTAL_0']['vat_rate_percent']) == 0
        assert http.get(f'/api/documents/{archive}').json()['invoice']['lines'] == archived_lines

        for direction, good, bad in [('purchase', '430', '830'), ('sale', '830', '430')]:
            doc = upload(rig, True)['id']
            form = payload(rig, doc)
            form['invoice_type'] = direction
            form['invoice_number'] = 'GC-' + direction
            url = f'/api/documents/{doc}/approve'
            assert http.post(url, json=form).status_code == 422  # old category is no longer selectable
            for line in form['lines']:
                line.update(booking_category_id=None, account_id=options[bad]['account_id'], vat_code_id=vat['PORTAL_21']['id'])
            assert http.post(url, json=form).status_code == 422
            for line in form['lines']:
                line['account_id'] = options[good]['account_id']
            result = http.post(url, json=form)
            assert result.status_code == 200, result.text
            invoice = http.get(f'/api/documents/{doc}').json()['invoice']
            assert all(x['account_code'] == good and x['booking_category_id'] is None
                       and x['vat_display_name'] == 'VAT 21%' for x in invoice['lines'])
    finally:
        with rig['db'].connect() as c:
            c.execute('DROP TABLE accounting.invoice_desk_account_catalog')


def test_conflicting_account_is_not_overwritten(rig):
    with rig['db'].connect() as c:
        c.execute("UPDATE accounting.accounts SET account_name='Inne konto' WHERE account_code='430'")
        c.execute("""INSERT INTO accounting.accounts (account_code,account_name,account_type)
                     VALUES ('430','Inne konto','expense') ON CONFLICT DO NOTHING""")
    try:
        with pytest.raises(Error, match='Konflikt istniejących kont GnuCash: 430'):
            with rig['db'].connect() as c:
                c.execute(SQL)
        with rig['db'].connect() as c:
            assert c.execute("SELECT account_name FROM accounting.accounts WHERE account_code='430'").fetchone()['account_name'] == 'Inne konto'
            assert not c.execute("SELECT to_regclass('accounting.invoice_desk_account_catalog') AS t").fetchone()['t']
    finally:
        with rig['db'].connect() as c:
            c.execute("UPDATE accounting.accounts SET account_name='Transportkosten' WHERE account_code='430'")
