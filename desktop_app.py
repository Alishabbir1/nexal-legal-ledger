"""
Nexal Legal — Desktop Application Launcher

Starts the Flask server in a background thread and displays the application
in a native desktop window using pywebview.

Launch order:
  1. Configure logging
  2. Start Flask server (background thread)
  3. Wait for server to be ready
  4. Launch PyWebView window
"""

import sys
import os
import threading
import time
import logging
import logging.handlers
import multiprocessing


def _get_data_dir() -> str:
    """Return the writable user-data directory."""
    if getattr(sys, 'frozen', False):
        d = os.path.join(
            os.environ.get('LOCALAPPDATA', os.path.expanduser('~')),
            'SolicitorLedger'
        )
    else:
        d = os.path.dirname(os.path.abspath(__file__))
    os.makedirs(d, exist_ok=True)
    return d


def setup_logging():
    """Configure application-wide logging to file and console."""
    data_dir = _get_data_dir()
    log_dir = os.path.join(data_dir, 'logs')
    os.makedirs(log_dir, exist_ok=True)
    log_file = os.path.join(log_dir, 'solicitor_ledger.log')

    root = logging.getLogger()
    root.setLevel(logging.INFO)

    if not root.handlers:
        file_handler = logging.handlers.RotatingFileHandler(
            log_file, maxBytes=5 * 1024 * 1024, backupCount=3, encoding='utf-8'
        )
        file_handler.setFormatter(logging.Formatter(
            '%(asctime)s %(levelname)-8s [%(name)s] %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        ))
        root.addHandler(file_handler)

        console_handler = logging.StreamHandler()
        console_handler.setLevel(logging.WARNING)
        console_handler.setFormatter(logging.Formatter('%(levelname)s: %(message)s'))
        root.addHandler(console_handler)

    return logging.getLogger('desktop_app')


if getattr(sys, 'frozen', False):
    os.chdir(os.path.dirname(sys.executable))

logger = setup_logging()
logger.info('='*60)
logger.info('Nexal Legal starting')
logger.info('Frozen: %s', getattr(sys, 'frozen', False))
logger.info('Data dir: %s', _get_data_dir())
logger.info('Working dir: %s', os.getcwd())
if getattr(sys, 'frozen', False):
    logger.info('MEIPASS: %s', getattr(sys, '_MEIPASS', 'N/A'))


def run_flask():
    """Run Flask server in a daemon thread."""
    try:
        from app import app
        logger.info('Flask server starting on 127.0.0.1:5000')
        app.run(host='127.0.0.1', port=5000, debug=False, use_reloader=False, threaded=True)
    except Exception:
        logger.exception('Flask server failed to start')


def wait_for_server(timeout=20):
    """Wait until Flask server is accepting connections."""
    import urllib.request
    url = 'http://127.0.0.1:5000/login'
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            urllib.request.urlopen(url, timeout=2)
            logger.info('Flask server is ready')
            return True
        except OSError:
            time.sleep(0.3)
    logger.error('Flask server did not respond within %s seconds', timeout)
    return False


def main():
    try:
        server_thread = threading.Thread(target=run_flask, daemon=True)
        server_thread.start()
        logger.info('Flask thread started, waiting for server...')

        if not wait_for_server():
            logger.error('Server failed to start — exiting')
            try:
                import ctypes
                ctypes.windll.user32.MessageBoxW(
                    0,
                    'The application server failed to start.\n\n'
                    'Check the log file at:\n'
                    f'{os.path.join(_get_data_dir(), "logs", "solicitor_ledger.log")}',
                    'Nexal Legal — Startup Error',
                    0x10
                )
            except Exception:
                pass
            return 1

        import webview
        logger.info('Creating PyWebView window')

        api = None
        try:
            from desktop_api import DesktopAPI
            api = DesktopAPI()
            logger.info('DesktopAPI loaded')
        except Exception as e:
            logger.warning('DesktopAPI not available: %s', e)

        window = webview.create_window(
            'Nexal Legal',
            'http://127.0.0.1:5000',
            width=1200,
            height=800,
            resizable=True,
            min_size=(800, 600),
            js_api=api,
        )
        webview.start()
        logger.info('Application closed normally')
        return 0

    except Exception:
        logger.exception('Application failed to start')
        return 1


if __name__ == '__main__':
    multiprocessing.freeze_support()
    sys.exit(main())
