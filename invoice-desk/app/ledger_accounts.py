"""Read-only account balances from the client's complete posted journal."""
from decimal import Decimal

from .db import client
from .domain import Problem

ZERO = Decimal('0.00')
CREDIT_NORMAL = {'liability', 'equity', 'income'}


def account_ids(c, account_id):
    if type(account_id) is not int or account_id <= 0:
        raise Problem('Nieprawidłowe konto.')
    ids = [r['id'] for r in c.execute("""
        WITH RECURSIVE branch(id) AS (
            SELECT id FROM accounting.accounts WHERE id=%s
            UNION
            SELECT a.id FROM accounting.accounts a JOIN branch b ON a.parent_account_id=b.id
        ) SELECT id FROM branch
    """, (account_id,)).fetchall()]
    if not ids:
        raise Problem('Nie znaleziono konta.', 404)
    return ids


def balances(c, client_id, date_from, date_to):
    client(c, client_id)
    if date_from > date_to:
        raise Problem('Sprawdź zakres dat.')
    # Do not suppress reversal pairs here: the complete journal is the source
    # of balances, including openings, payments and dated corrections.
    rows = c.execute("""
        WITH movement AS (
            SELECT l.account_id,
                COALESCE(SUM(l.debit_amount-l.credit_amount)
                    FILTER (WHERE e.entry_date<%s),0) AS opening_net,
                COALESCE(SUM(l.debit_amount)
                    FILTER (WHERE e.entry_date>=%s),0) AS period_debit,
                COALESCE(SUM(l.credit_amount)
                    FILTER (WHERE e.entry_date>=%s),0) AS period_credit,
                COUNT(DISTINCT e.id) AS history_count,
                COUNT(DISTINCT e.id) FILTER (WHERE e.entry_date>=%s) AS period_count
            FROM accounting.general_ledger_entries e
            JOIN accounting.general_ledger_entry_lines l ON l.general_ledger_entry_id=e.id
            WHERE e.client_id=%s AND e.entry_date<=%s AND e.status IN ('posted','reversed')
            GROUP BY l.account_id
        )
        SELECT a.id,a.account_code,a.account_name,a.account_type,a.parent_account_id,
               a.is_active,a.allow_direct_posting,
               COALESCE(m.opening_net,0) AS opening_net,
               COALESCE(m.period_debit,0) AS period_debit,
               COALESCE(m.period_credit,0) AS period_credit,
               COALESCE(m.history_count,0) AS history_count,
               COALESCE(m.period_count,0) AS period_count
        FROM accounting.accounts a LEFT JOIN movement m ON m.account_id=a.id
        ORDER BY a.account_code,a.id
    """, (date_from,date_from,date_from,date_from,client_id,date_to)).fetchall()
    all_rows = {r['id']:r for r in rows}
    included = {r['id'] for r in rows if r['is_active'] or r['history_count']}
    # Keep ancestors even when inactive; do not lose children or old balances.
    for account_id in list(included):
        seen=set()
        while account_id in all_rows and account_id not in seen:
            seen.add(account_id);included.add(account_id)
            account_id=all_rows[account_id]['parent_account_id']
    nodes={r['id']:dict(r) for r in rows if r['id'] in included}
    warning=False
    for identifier,node in nodes.items():
        if node['parent_account_id'] not in nodes:
            node['parent_account_id']=None
        current=identifier;seen=set()
        while current in nodes:
            if current in seen:
                node['parent_account_id']=None
                warning=True
                break
            seen.add(current)
            current=nodes[current]['parent_account_id']
    children={identifier:[] for identifier in nodes}
    roots=[]
    for identifier,node in nodes.items():
        parent=node['parent_account_id']
        (children[parent] if parent is not None else roots).append(identifier)
        direct=node['opening_net']+node['period_debit']-node['period_credit']
        sign=-1 if node['account_type'] in CREDIT_NORMAL else 1
        node.update(direct_opening_balance=node['opening_net']*sign,
                    direct_period_change=(node['period_debit']-node['period_credit'])*sign,
                    direct_closing_balance=direct*sign,
                    _opening=node['opening_net'],_debit=node['period_debit'],_credit=node['period_credit'])
    order=[];stack=[(identifier,0) for identifier in reversed(roots)]
    while stack:
        identifier,depth=stack.pop();node=nodes[identifier]
        node.update(depth=depth,has_children=bool(children[identifier]))
        order.append(identifier)
        stack.extend((child,depth+1) for child in reversed(children[identifier]))
    for identifier in reversed(order):
        node=nodes[identifier];parent=node['parent_account_id']
        if parent is not None:
            for field in ('_opening','_debit','_credit'):
                nodes[parent][field]+=node[field]
    result=[]
    for identifier in order:
        node=nodes[identifier];sign=-1 if node['account_type'] in CREDIT_NORMAL else 1
        opening=node.pop('_opening');debit=node.pop('_debit');credit=node.pop('_credit')
        node.update(opening_balance=opening*sign,period_change=(debit-credit)*sign,
                    closing_balance=(opening+debit-credit)*sign,
                    total_period_debit=debit,total_period_credit=credit)
        node.pop('opening_net')
        result.append(node)
    return {'accounts':result,'currency':'EUR','date_from':date_from,'date_to':date_to,
            'hierarchy_warning':warning,
            'counts':{'accounts':len(result),'with_activity':sum(bool(r['period_count']) for r in result)}}
