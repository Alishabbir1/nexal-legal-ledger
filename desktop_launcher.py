"""
Nexal Legal - Desktop Application Launcher
Runs Flask app in embedded browser window via pywebview.
"""

import os
import sys
import threading

# Ensure we're in the app directory (critical when running from PyInstaller bundle)
if getattr(sys, 'frozen', False):
    os.chdir(os.path.dirname(sys.executable))
else:
    os.chdir(os.path.dirname(os.path.abspath(__file__)))

# Flask runs in production mode when bundled
DEBUG_MODE = not getattr(sys, 'frozen', False)
HOST = '127.0.0.1'
PORT = 5000
APP_URL = f'http://{HOST}:{PORT}'

APP_NAME = 'Nexal Legal'
APP_NAME_REGISTRY = 'Nexal Legal'
REGISTRY_RUN_PATH = r'Software\Microsoft\Windows\CurrentVersion\Run'


def is_autostart_enabled():
    """Check if auto-start is enabled (Windows Run registry key)"""
    try:
        import winreg
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            REGISTRY_RUN_PATH,
            0,
            winreg.KEY_READ
        )
        winreg.QueryValueEx(key, APP_NAME_REGISTRY)
        winreg.CloseKey(key)
        return True
    except (FileNotFoundError, OSError):
        return False


def set_autostart(enabled: bool):
    """Enable or disable Windows auto-start on login via Registry"""
    import winreg
    if getattr(sys, 'frozen', False):
        exe_path = sys.executable
    else:
        exe_path = f'"{sys.executable}" "{os.path.abspath(__file__)}"'

    try:
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            REGISTRY_RUN_PATH,
            0,
            winreg.KEY_SET_VALUE
        )
        if enabled:
            winreg.SetValueEx(key, APP_NAME_REGISTRY, 0, winreg.REG_SZ, exe_path)
        else:
            try:
                winreg.DeleteValue(key, APP_NAME_REGISTRY)
            except FileNotFoundError:
                pass
        winreg.CloseKey(key)
        return True
    except Exception as e:
        print(f"Autostart config error: {e}")
        return False


def run_flask_server():
    """Run Flask app in background thread"""
    from app import app
    app.run(host=HOST, port=PORT, debug=False, use_reloader=False, threaded=True)


def wait_for_server(timeout=15):
    """Wait until Flask server is responding"""
    import time
    import urllib.request
    import urllib.error
    start = time.time()
    while time.time() - start < timeout:
        try:
            urllib.request.urlopen(APP_URL, timeout=1)
            return True
        except (urllib.error.URLError, OSError):
            time.sleep(0.3)
    return False


def main():
    # Ensure templates/static exist
    os.makedirs('templates', exist_ok=True)
    os.makedirs('static', exist_ok=True)

    import webview

    # Start Flask server in background thread
    server_thread = threading.Thread(target=run_flask_server, daemon=True)
    server_thread.start()

    # Wait for server to be ready before showing window
    if not wait_for_server():
        raise RuntimeError("Flask server failed to start. Check if port 5000 is in use.")

    # Create embedded browser window that loads the Flask app
    webview.create_window(
        APP_NAME,
        APP_URL,
        width=1200,
        height=800,
        resizable=True,
        min_size=(800, 600)
    )

    webview.start(debug=DEBUG_MODE)


if __name__ == '__main__':
    main()
