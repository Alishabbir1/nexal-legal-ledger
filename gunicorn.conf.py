"""Gunicorn configuration for Nexal Ledger production.

Use an absolute path so a stale systemd WorkingDirectory drop-in cannot break imports:
  /opt/nexal-ledger/venv/bin/gunicorn -c /opt/nexal-ledger/gunicorn.conf.py wsgi:application
"""
import os

_base_dir = os.path.dirname(os.path.abspath(__file__))
os.chdir(_base_dir)

bind = os.environ.get("GUNICORN_BIND", "127.0.0.1:5001")
workers = int(os.environ.get("GUNICORN_WORKERS", "2"))
timeout = int(os.environ.get("GUNICORN_TIMEOUT", "120"))
wsgi_app = "wsgi:application"
chdir = _base_dir
