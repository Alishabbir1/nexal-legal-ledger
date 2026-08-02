"""WSGI entry point for production gunicorn."""
from app import app

application = app
