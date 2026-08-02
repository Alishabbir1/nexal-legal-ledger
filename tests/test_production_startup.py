"""Production startup smoke tests."""
from __future__ import annotations

import importlib.util
from pathlib import Path


def test_wsgi_application_imports():
    from wsgi import application

    assert application is not None
    assert application.name == "app"


def test_gunicorn_conf_chdir_points_at_repo_root():
    root = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location("gunicorn_conf", root / "gunicorn.conf.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.chdir == module._base_dir
    assert (Path(module._base_dir) / "app.py").is_file()
