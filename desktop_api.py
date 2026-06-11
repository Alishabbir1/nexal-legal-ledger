"""
Desktop export API for PyWebView.
Exposes export_report() for native Save File dialog.
"""


class DesktopAPI:
    """API class exposed to PyWebView for report exports."""

    def export_report(self, report_type, format):
        """
        Generate a report and save via native Save File dialog.
        report_type: client_ledger|cashbook|income_summary|expense_summary|net_profit
        format: 'pdf' or 'csv'
        """
        try:
            from app import app, generate_report_bytes
            from tkinter import filedialog, Tk
        except ImportError as e:
            return {'success': False, 'error': str(e)}

        try:
            report_type = (report_type or '').strip().lower()
            fmt = (format or 'pdf').strip().lower()
            internal = {'client_ledger': 'ledger', 'cashbook': 'cashbook',
                        'income_summary': 'office_income', 'expense_summary': 'office_expenses',
                        'net_profit': 'office_profit'}.get(report_type)
            if not internal or fmt not in ('pdf', 'csv'):
                return {'success': False, 'error': 'Invalid report_type or format'}

            with app.app_context():
                data, name = generate_report_bytes(internal, fmt, client_id=None,
                    date_from=None, date_to=None, created_by=None)

            root = Tk()
            root.withdraw()
            root.attributes('-topmost', True)
            ext = 'pdf' if fmt == 'pdf' else 'csv'
            path = filedialog.asksaveasfilename(
                defaultextension=f'.{ext}',
                filetypes=[('PDF files', '*.pdf') if fmt == 'pdf' else ('CSV files', '*.csv'),
                          ('All files', '*.*')],
                initialfile=name, title='Save report')
            root.destroy()

            if not path:
                return {'success': False, 'error': 'Save cancelled'}

            with open(path, 'wb') as f:
                f.write(data)
            return {'success': True, 'message': 'Report downloaded successfully.'}
        except Exception as e:
            return {'success': False, 'error': str(e)}
