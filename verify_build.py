"""
Build Verification Test for Nexal Legal.

Runs in-process (no compiled exe needed) to verify:
  1. All critical imports succeed
  2. Database initializes correctly
  3. Test client/transactions can be created
  4. All report exports (PDF, CSV, Excel) produce valid files
  5. File paths resolve correctly

Exit code 0 = all tests passed, non-zero = failure.
"""

import sys
import os
import json
import tempfile
import shutil

PASS = 0
FAIL = 0


def check(name, condition, detail=''):
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f'  [PASS] {name}')
    else:
        FAIL += 1
        print(f'  [FAIL] {name}  {detail}')


def main():
    global PASS, FAIL
    print('\n  Nexal Legal — Build Verification')
    print('  ' + '='*48 + '\n')

    # --- 1. Import checks ---
    print('  1. Critical imports')
    for mod_name in ['flask', 'reportlab', 'reportlab.platypus', 'openpyxl',
                     'cryptography', 'webview', 'werkzeug', 'jinja2']:
        try:
            __import__(mod_name)
            check(f'import {mod_name}', True)
        except ImportError as e:
            check(f'import {mod_name}', False, str(e))

    # --- 2. App initialization ---
    print('\n  2. Application initialization')
    try:
        # Use a temporary database so we don't touch the real one
        test_dir = tempfile.mkdtemp(prefix='solicitor_verify_')
        test_db = os.path.join(test_dir, 'test.db')
        os.environ['_VERIFY_BUILD'] = '1'

        from database import Database
        db = Database(db_path=test_db)
        check('Database initializes', os.path.isfile(test_db))

        from app import app
        app.config['TESTING'] = True
        check('Flask app creates', app is not None)
    except Exception as e:
        check('App initialization', False, str(e))
        print(f'\n  FATAL: Cannot continue without app initialization.\n')
        return 1

    client = app.test_client()

    # --- 3. Create test data ---
    print('\n  3. Test data creation')
    # Default admin is seeded automatically during init_database()
    admin = db.get_user_by_username('admin')
    check('Default admin exists', admin is not None)

    with client.session_transaction() as sess:
        sess['user_id'] = admin['user_id'] if admin else 1
        sess['username'] = 'admin'
        sess['role'] = 'admin'

    try:
        cid = db.create_client('VERIFY01', 'Build Verification Client',
                               'Test Matter', 'Verification test client')
        check(f'Test client created (id={cid})', cid is not None and cid > 0)
    except Exception as e:
        check('Test client created', False, str(e))
        cid = None

    if cid:
        try:
            from decimal import Decimal
            tid = db.create_ledger_transaction(
                cid, '2026-01-15', Decimal('1000.00'), 'Receipt',
                'VER-001', 'Bank Transfer', created_by='admin')
            check(f'Test receipt created (id={tid})', tid is not None and tid > 0)
            tid2 = db.create_ledger_transaction(
                cid, '2026-01-20', Decimal('250.00'), 'Payment',
                'VER-002', 'Bank Transfer', created_by='admin')
            check(f'Test payment created (id={tid2})', tid2 is not None and tid2 > 0)
        except Exception as e:
            check('Test transactions created', False, str(e))

    # --- 4. Report exports ---
    print('\n  4. Report exports')
    export_dir = os.path.join(test_dir, 'exports')
    os.makedirs(export_dir, exist_ok=True)

    # Temporarily redirect exports to our test directory
    import app as app_module
    original_get_exports = app_module._get_exports_dir
    app_module._get_exports_dir = lambda: export_dir

    pdf_routes = [
        ('/reports/export/ledger-pdf', 'ledger PDF'),
        ('/reports/export/cashbook-pdf', 'cashbook PDF'),
        ('/reports/export/office-income-pdf', 'office income PDF'),
        ('/reports/export/office-expenses-pdf', 'office expenses PDF'),
        ('/reports/export/office-profit-pdf', 'office profit PDF'),
    ]
    csv_routes = [
        ('/reports/export/ledger-csv', 'ledger CSV'),
        ('/reports/export/cashbook-csv', 'cashbook CSV'),
        ('/reports/export/office-income-csv', 'office income CSV'),
        ('/reports/export/office-expenses-csv', 'office expenses CSV'),
        ('/reports/export/office-profit-csv', 'office profit CSV'),
    ]
    xlsx_routes = [
        ('/reports/export/ledger-xlsx', 'ledger Excel'),
        ('/reports/export/cashbook-xlsx', 'cashbook Excel'),
        ('/reports/export/office-income-xlsx', 'office income Excel'),
        ('/reports/export/office-expenses-xlsx', 'office expenses Excel'),
        ('/reports/export/office-profit-xlsx', 'office profit Excel'),
    ]

    for routes, fmt in [(pdf_routes, 'PDF'), (csv_routes, 'CSV'), (xlsx_routes, 'Excel')]:
        for route, name in routes:
            try:
                with client.session_transaction() as sess:
                    sess['user_id'] = 1
                    sess['username'] = 'admin'
                    sess['role'] = 'admin'
                resp = client.get(f'{route}?save_local=1')
                if resp.status_code == 200:
                    data = json.loads(resp.data)
                    if data.get('success'):
                        filepath = data.get('filepath', '')
                        exists = os.path.isfile(filepath)
                        size = os.path.getsize(filepath) if exists else 0
                        check(f'Export {name}', exists and size > 0,
                              f'file={filepath} size={size}' if not (exists and size > 0) else '')
                    else:
                        check(f'Export {name}', False, data.get('error', 'unknown'))
                else:
                    check(f'Export {name}', False, f'HTTP {resp.status_code}')
            except Exception as e:
                check(f'Export {name}', False, str(e))

    # Restore
    app_module._get_exports_dir = original_get_exports

    # --- 5. File count verification ---
    print('\n  5. Export file verification')
    exported_files = os.listdir(export_dir)
    pdf_count = len([f for f in exported_files if f.endswith('.pdf')])
    csv_count = len([f for f in exported_files if f.endswith('.csv')])
    xlsx_count = len([f for f in exported_files if f.endswith('.xlsx')])
    check(f'PDF files generated: {pdf_count}/5', pdf_count == 5)
    check(f'CSV files generated: {csv_count}/5', csv_count == 5)
    check(f'Excel files generated: {xlsx_count}/5', xlsx_count == 5)

    # --- Cleanup ---
    try:
        shutil.rmtree(test_dir, ignore_errors=True)
    except Exception:
        pass

    # --- Summary ---
    total = PASS + FAIL
    print(f'\n  Results: {PASS}/{total} passed, {FAIL} failed')
    if FAIL > 0:
        print('  BUILD VERIFICATION FAILED\n')
        return 1
    else:
        print('  BUILD VERIFICATION PASSED\n')
        return 0


if __name__ == '__main__':
    sys.exit(main())
