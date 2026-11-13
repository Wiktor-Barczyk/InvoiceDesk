"""Invoice Desk posting for ordinary domestic EUR invoices and open-period corrections."""
from decimal import Decimal

from .db import SPLIT_INVOICE_OWNER, approve, audit, client, document, insert, jsonb
from .domain import Problem, money, text_field

GL = 'accounting.general_ledger_entries'
GLL = 'accounting.general_ledger_entry_lines'
VAT = 'accounting.vat_transactions'
CONTROL = {'130': ('Debiteuren','asset'), '140': ('Crediteuren','liability'),
           '150': ('Te vorderen BTW','asset'), '151': ('Te betalen BTW','liability')}
RATES = {'PORTAL_0': Decimal(0), 'PORTAL_9': Decimal(9), 'PORTAL_21': Decimal(21), 'PORTAL_EXEMPT': None}
SPECIAL = {'460','490','840','860'}
CORRECTORS = {'admin','chief_accountant'}
JOURNAL = 'IDESK'
ZERO = Decimal('0.00')


def period(c, client_id, posting_date, vat_date):
    locked = c.execute("""
        SELECT lock_type FROM tax.accounting_period_locks
        WHERE client_id=%s AND
          ((lock_type IN ('VAT','FULL') AND locked_through_date >= %s)
            OR (lock_type IN ('YEAR','FULL') AND locked_through_date >= %s)) LIMIT 1
    """, (client_id, vat_date, posting_date)).fetchone()
    if locked:
        raise Problem('Okres księgowy lub VAT jest zamknięty.',409)
    fiscal = c.execute("""SELECT id,status FROM core.client_fiscal_years WHERE client_id=%s
                         AND %s BETWEEN date_from AND date_to FOR SHARE""", (client_id,posting_date)).fetchall()
    if len(fiscal)>1 or (fiscal and fiscal[0]['status']!='open'):
        raise Problem('Rok obrotowy jest zamknięty albo jego daty nakładają się.',409)
    return fiscal[0]['id'] if fiscal else None


def control(c, code):
    row = c.execute("""SELECT * FROM accounting.accounts WHERE account_code=%s
                       AND is_active AND allow_direct_posting FOR SHARE""", (code,)).fetchone()
    name, kind = CONTROL[code]
    if not row or row['account_type']!=kind or row['account_name'].strip().removeprefix(code+' ').strip().casefold()!=name.casefold():
        raise Problem(f'Brak aktywnego konta {code} {name} z właściwym typem.')
    return row['id']


def journal(c, staff_id, client_id, fiscal, day, kind, title, reference, rows, reverses=None):
    if len(rows)<2 or sum(x['debit_amount'] for x in rows)!=sum(x['credit_amount'] for x in rows):
        raise Problem('Zapis Wn/Ma nie jest zbilansowany.')
    entry = insert(c, GL, dict(client_id=client_id,fiscal_year_id=fiscal,entry_date=day,
                   entry_type=kind,journal_code=JOURNAL,description=title,reference=reference,
                   created_by_staff_id=staff_id,reverses_entry_id=reverses))
    for idx, row in enumerate(rows, 1):
        insert(c, GLL, dict(row, general_ledger_entry_id=entry['id'], line_number=idx))
    final = c.execute(f"""UPDATE {GL} SET status='posted',posted_by_staff_id=%s
                         WHERE id=%s RETURNING *""", (staff_id,entry['id'])).fetchone()
    audit(c, GL, final['id'],staff_id,final,entry)
    return final['id']


def post(c, doc_id, invoice_id, data, staff_id):
    invoice = c.execute("SELECT * FROM accounting.invoices WHERE id=%s FOR UPDATE",(invoice_id,)).fetchone()
    cid = invoice['client_id']
    if not c.execute(f"""SELECT 1 FROM accounting.invoice_source_documents s
        JOIN accounting.invoices inv ON inv.id=s.invoice_id
        JOIN documents.accounting_documents d ON d.id=s.accounting_document_id
        WHERE d.id=%s AND inv.id=%s AND s.document_role='source' AND inv.client_id=d.client_id
          AND {SPLIT_INVOICE_OWNER}""", (doc_id,invoice_id)).fetchone():
        raise Problem('Faktura nie należy do tego dokumentu.',409)
    if invoice['status']!='approved' or invoice['general_ledger_entry_id'] is not None:
        raise Problem('Faktura nie jest gotowa do księgowania.',409)
    if invoice['currency']!='EUR' or invoice['invoice_kind']!='invoice' or invoice['invoice_type'] not in ('purchase','sale'):
        raise Problem('Księgowanie obsługuje zwykłe faktury krajowe w EUR. Korekty handlowe, paragony i inne waluty wymagają osobnej obsługi.')
    if invoice['gross_total']<=0:
        raise Problem('Kwota brutto musi być dodatnia.')
    for field in ('seller_vat_number_snapshot','buyer_vat_number_snapshot'):
        vat_number = (invoice[field] or '').strip().upper()
        if vat_number and vat_number[:2].isalpha() and not vat_number.startswith('NL'):
            raise Problem('Faktura z zagranicznym numerem VAT wymaga osobnej obsługi.')
    fiscal = period(c,cid,invoice['invoice_date'],invoice['vat_date'])
    buy = invoice['invoice_type']=='purchase'
    lines = c.execute("SELECT * FROM accounting.invoice_lines WHERE invoice_id=%s ORDER BY line_number FOR SHARE", (invoice_id,)).fetchall()
    if (not lines or sum(x['net_amount'] for x in lines)!=invoice['net_total']
            or sum(x['vat_amount'] for x in lines)!=invoice['vat_total']
            or invoice['net_total']+invoice['vat_total']!=invoice['gross_total']):
        raise Problem('Kwoty faktury i pozycji się nie zgadzają.')
    deductions=data.get('deductions')
    if buy and (not isinstance(deductions,list) or len(deductions)!=len(lines)):
        raise Problem('Uzupełnij VAT do odliczenia dla każdej pozycji zakupu.')
    rows, taxes = [], []
    def line(account_id, amount, debit, label, invoice_line_id=None):
        if amount:
            rows.append(dict(account_id=account_id,description=label,
                debit_amount=amount if debit else ZERO,credit_amount=ZERO if debit else amount,
                counterparty_id=invoice['counterparty_id'],invoice_line_id=invoice_line_id))

    for idx, item in enumerate(lines):
        n, vat = item['net_amount'], item['vat_amount']
        acct = c.execute("""SELECT * FROM accounting.accounts WHERE id=%s AND is_active AND allow_direct_posting FOR SHARE""",
                         (item['account_id'],)).fetchone()
        code = c.execute("SELECT * FROM accounting.vat_codes WHERE id=%s AND is_active FOR SHARE", (item['vat_code_id'],)).fetchone()
        if not acct:
            raise Problem(f'Pozycja {idx+1}: wybierz konto księgowe GnuCash, nie samą kategorię.')
        if acct['account_code'] in SPECIAL or not code or code['vat_code'] not in RATES:
            raise Problem(f'Pozycja {idx+1}: Verlegd, ICP, zakupy zagraniczne i inne kody VAT wymagają osobnej obsługi.')
        if acct['account_code'] in CONTROL or acct['account_type'] not in (('expense','asset','liability') if buy else ('income',)):
            raise Problem(f'Pozycja {idx+1}: konto nie pasuje do typu faktury.')
        if (code['vat_direction'] not in (('input' if buy else 'output'),'both','none')
                or (code['valid_from'] and invoice['vat_date']<code['valid_from'])
                or (code['valid_to'] and invoice['vat_date']>code['valid_to'])):
            raise Problem(f'Pozycja {idx+1}: kod VAT nie pasuje do rodzaju lub daty faktury.')
        rate = RATES[code['vat_code']]
        if rate != code['vat_rate_percent'] or ((rate is None or rate==0) and vat!=0):
            raise Problem(f'Pozycja {idx+1}: kod VAT nie zgadza się z zapisanymi kwotami.')
        if not buy and acct['account_code'] in ('820','830','850'):
            if rate != {'820': Decimal(9), '830': Decimal(21), '850': None}[acct['account_code']]:
                raise Problem(f'Pozycja {idx+1}: VAT nie odpowiada wybranemu kontu sprzedaży.')
        if buy:
            deductible = money(deductions[idx], f'Pozycja {idx+1}: VAT do odliczenia')
            if deductible>vat:
                raise Problem(f'Pozycja {idx+1}: VAT do odliczenia przekracza VAT z faktury.')
            line(acct['id'],n+vat-deductible,True,item['description'],item['id'])
            if deductible:
                line(control(c,'150'),deductible,True,'VAT do odliczenia',item['id'])
        else:
            deductible=ZERO
            line(acct['id'],n,False,item['description'],item['id'])
            if vat:
                line(control(c,'151'),vat,False,'VAT należny',item['id'])
        taxes.append(dict(client_id=cid,invoice_line_id=item['id'],vat_code_id=code['id'],
                          vat_date=invoice['vat_date'],taxable_amount=n,
                          input_vat_amount=deductible,output_vat_amount=ZERO if buy else vat))
    line(control(c,'140' if buy else '130'),invoice['gross_total'],not buy,'Rozrachunek z kontrahentem')
    ref = invoice['invoice_number']
    entry_id=journal(c,staff_id,cid,fiscal,invoice['invoice_date'],invoice['invoice_type'],
                     'Faktura '+str(ref or invoice_id),ref,rows)
    for tax in taxes:
        saved=insert(c,VAT,dict(tax,general_ledger_entry_id=entry_id))
        audit(c,VAT,saved['id'],staff_id,saved)
    final=c.execute("""UPDATE accounting.invoices SET status='posted',general_ledger_entry_id=%s
                      WHERE id=%s RETURNING *""",(entry_id,invoice_id)).fetchone()
    audit(c,'accounting.invoices',invoice_id,staff_id,final,invoice)
    before=c.execute("SELECT * FROM documents.accounting_documents WHERE id=%s",(doc_id,)).fetchone()
    doc=c.execute("""UPDATE documents.accounting_documents SET status='booked'
                     WHERE id=%s RETURNING *""",(doc_id,)).fetchone()
    audit(c,'documents.accounting_documents',doc_id,staff_id,doc,before)
    return entry_id


def reverse(c, previous, doc_id, staff_id, reason):
    old_id, cid, entry_id = previous['id'], previous['client_id'], previous['general_ledger_entry_id']
    entry = c.execute(f'SELECT * FROM {GL} WHERE id=%s FOR UPDATE',(entry_id,)).fetchone()
    if not entry or entry['client_id']!=cid or entry['journal_code']!=JOURNAL or entry['status']!='posted':
        raise Problem('Poprawki są dostępne dla zapisów zaksięgowanych w tym portalu.',409)
    if c.execute(f"SELECT 1 FROM {GL} WHERE reverses_entry_id=%s",(entry_id,)).fetchone():
        raise Problem('Zapis został już skorygowany.',409)
    fiscal = period(c,cid,entry['entry_date'],previous['vat_date'])
    if previous['payment_status'] in ('partial','paid') or c.execute("""
        SELECT 1 FROM banking.bank_invoice_match_suggestions
        WHERE invoice_id=%s AND status='approved' LIMIT 1
    """,(old_id,)).fetchone():
        raise Problem('Faktura ma rozliczoną płatność. Najpierw rozwiąż rozrachunek.',409)
    if c.execute("""SELECT 1 FROM assets.fixed_assets a
        JOIN accounting.invoice_lines l ON l.id=a.source_invoice_line_id
        WHERE l.invoice_id=%s LIMIT 1""",(old_id,)).fetchone():
        raise Problem('Faktura jest powiązana z kartoteką środków trwałych. Wymagana osobna korekta.',409)

    vat_rows=c.execute(f"SELECT * FROM {VAT} WHERE general_ledger_entry_id=%s ORDER BY id FOR UPDATE",(entry_id,)).fetchall()
    if sorted(row['invoice_line_id'] or 0 for row in vat_rows)!=sorted(line['id'] for line in previous['lines']):
        raise Problem('Rejestr VAT różni się od zapisu faktury. Wymagana osobna korekta.',409)
    if c.execute("""SELECT 1 FROM tax.tax_declaration_vat_transactions
                  WHERE vat_transaction_id=ANY(%s) LIMIT 1""",([row['id'] for row in vat_rows],)).fetchone():
        raise Problem('Faktura jest powiązana z deklaracją VAT. Wymagana osobna korekta.',409)
    original=c.execute(f"SELECT * FROM {GLL} WHERE general_ledger_entry_id=%s ORDER BY line_number",(entry_id,)).fetchall()
    rows=[dict(account_id=x['account_id'],debit_amount=x['credit_amount'],credit_amount=x['debit_amount'],
               description=x['description'],counterparty_id=x['counterparty_id'],invoice_line_id=x['invoice_line_id']) for x in original]
    reversal=journal(c,staff_id,cid,fiscal,entry['entry_date'],'correction',
                     f'Storno faktury #{old_id}: {reason}',previous['invoice_number'],rows,entry_id)
    # The V5 register stores unsigned amounts. Keep the replaced entries in
    # append-only history and replace the current open-period register atomically.
    for row in vat_rows:
        insert(c,'audit.change_log',dict(schema_name='accounting',table_name='vat_transactions',
               record_id=row['id'],action='DELETE',staff_user_id=staff_id,before_data=jsonb(row),
               after_data=jsonb({'reversal_entry_id': reversal})))
        c.execute(f'DELETE FROM {VAT} WHERE id=%s',(row['id'],))
    c.execute("""INSERT INTO accounting.invoice_source_documents
        (invoice_id,accounting_document_id,document_role) VALUES (%s,%s,'supporting')
        ON CONFLICT DO NOTHING""",(old_id,doc_id))
    c.execute("""DELETE FROM accounting.invoice_source_documents
               WHERE invoice_id=%s AND accounting_document_id=%s AND document_role='source'""",(old_id,doc_id))
    return reversal


def copy_asset_plans(c, previous, replacement_id, submitted, staff_id):
    table='assets.invoice_desk_depreciation_plans'
    if not c.execute('SELECT to_regclass(%s) AS t',(table,)).fetchone()['t']:
        return 0
    old={str(line['id']):line for line in previous['lines']}
    new=c.execute("""SELECT l.*,i.currency,COALESCE(v.input_vat_amount,0) AS deductible_vat
                     FROM accounting.invoice_lines l JOIN accounting.invoices i ON i.id=l.invoice_id
                     LEFT JOIN accounting.vat_transactions v ON v.invoice_line_id=l.id
                       AND v.general_ledger_entry_id=i.general_ledger_entry_id
                     WHERE l.invoice_id=%s ORDER BY l.line_number""",(replacement_id,)).fetchall()
    planned=c.execute(f"SELECT * FROM {table} WHERE invoice_line_id=ANY(%s)",([int(x) for x in old],)).fetchall()
    plans={str(x['invoice_line_id']):x for x in planned}
    carried=set()
    for incoming, line in zip(submitted,new):
        key=str(incoming.get('id',''))
        if key not in plans or key in carried or previous['currency']!=line['currency']:
            continue
        if any(old[key].get(k)!=line[k] for k in ('account_id','description','net_amount','vat_amount','quantity','deductible_vat')):
            continue
        fields={k:plans[key][k] for k in ('asset_name','in_use_date','acquisition_cost','residual_value','useful_life_months')}
        # INVENTARIS_EVENT_FIELDS: keep the asset's operational history on invoice replacement.
        fields.update({k:plans[key][k] for k in ('notes','loss_type','loss_date') if k in plans[key]})
        saved=insert(c,table,{**fields,'invoice_line_id':line['id'],'updated_by':staff_id})
        audit(c,table,line['id'],staff_id,saved)
        carried.add(key)
    return len(plans)-len(carried)


def book(c, doc_id, payload, staff):
    if not isinstance(payload,dict) or payload.get('reviewed') is not True:
        raise Problem('Potwierdź zgodność z dokumentem, konta i krajowe rozliczenie VAT.')
    staff_id=staff['id']
    state=document(c,doc_id,for_update=True)
    doc, before=state['document'],state['invoice']
    client(c,doc['client_id'])
    if str(payload.get('client_id'))!=str(doc['client_id']):
        raise Problem('Dokument należy do innego klienta.',409)
    if payload.get('version')!=doc['updated_at'].isoformat():
        raise Problem('Dokument się zmienił. Otwórz go ponownie.',409)
    if before:
        current=c.execute('SELECT * FROM accounting.invoices WHERE id=%s FOR UPDATE',(before['id'],)).fetchone()
        if current['updated_at'].isoformat()!=payload.get('invoice_version'):
            raise Problem('Faktura się zmieniła. Otwórz ją ponownie.',409)
        if before['client_id']!=doc['client_id']:
            raise Problem('Faktura należy do innego klienta.',409)
    if before and before['status']=='posted':
        if staff['role'] not in CORRECTORS:
            raise Problem('Zaksięgowaną fakturę może poprawić główny księgowy lub administrator.',403)
        reason=text_field(payload,'correction_reason',500,required=True)
        if doc['status']!='booked':
            raise Problem('Status dokumentu nie pozwala na poprawkę.',409)
        reversal=reverse(c,before,doc_id,staff_id,reason)
        changed=c.execute("""UPDATE documents.accounting_documents SET status='to_book'
                            WHERE id=%s RETURNING updated_at""",(doc_id,)).fetchone()
        new=approve(c,doc_id,{**payload,'version':changed['updated_at'].isoformat()},staff_id, correction_of=before['id'])
        posted=post(c,doc_id,new['id'],payload,staff_id)
        insert(c,'accounting.invoice_desk_corrections',dict(previous_invoice_id=before['id'],
               replacement_invoice_id=new['id'],reversal_entry_id=reversal,reason=reason,staff_id=staff_id))
        pending=copy_asset_plans(c,before,new['id'],payload['lines'],staff_id)
        return {'id':new['id'],'entry_id':posted,'corrected':True,'plans_to_review':pending}
    if before and before['status']=='approved':
        if doc['status']!='to_book':
            raise Problem('Status dokumentu nie pozwala na księgowanie.',409)
        invoice_id=before['id']
    else:
        invoice_id=approve(c,doc_id,payload,staff_id)['id']
    return {'id':invoice_id,'entry_id':post(c,doc_id,invoice_id,payload,staff_id),'corrected':False}
