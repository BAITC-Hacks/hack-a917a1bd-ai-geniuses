"""Проверки локальной раздачи frontend с вымышленными файлами вне dist."""

from pathlib import Path
from uuid import uuid4

import dotenv
import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def workspace_path() -> Path:
    # Обычное создание внутри workspace сохраняет наследуемые разрешения Windows.
    # Маленькие синтетические файлы остаются в игнорируемом каталоге work.
    root = Path(__file__).resolve().parents[2] / 'work' / 'test-frontend-serving' / uuid4().hex
    root.mkdir(parents=True)
    return root


def create_test_app(monkeypatch, root: Path):
    # При отдельном запуске не читаем настоящий backend/.env при импорте config.
    monkeypatch.setattr(dotenv, 'load_dotenv', lambda *args, **kwargs: False)
    from app import main

    monkeypatch.setattr(main, 'ROOT', root)
    return main.create_app(main.Settings(api_key='', data_dir=root / 'data'))


def write_build(root: Path) -> None:
    dist = root / 'frontend' / 'dist'
    (dist / 'assets').mkdir(parents=True)
    (dist / 'index.html').write_text('<!doctype html><html lang="ru"><body>Учебный интерфейс OrgLens</body></html>', encoding='utf-8')
    (dist / 'assets' / 'app.css').write_text('body { color: #123456; }', encoding='utf-8')


def test_static_build_is_available_without_shadowing_api(workspace_path, monkeypatch):
    write_build(workspace_path)
    with TestClient(create_test_app(monkeypatch, workspace_path)) as client:
        index = client.get('/')
        assert index.status_code == 200
        assert index.headers['content-type'].startswith('text/html')
        assert 'Учебный интерфейс OrgLens' in index.text
        css = client.get('/assets/app.css')
        assert css.status_code == 200
        assert css.headers['content-type'].startswith('text/css')
        assert '#123456' in css.text
        health = client.get('/api/health')
        assert health.status_code == 200
        assert health.headers['content-type'].startswith('application/json')
        assert health.json()['status'] == 'ok'
        assert health.json()['ai_configured'] is False
        assert client.get('/api/unknown-route').status_code == 404


def test_files_outside_dist_are_not_served_even_with_traversal(workspace_path, monkeypatch):
    write_build(workspace_path)
    markers = {
        '.env': 'FAKE_ROOT_ENV_MARKER',
        'backend/.env': 'FAKE_BACKEND_ENV_MARKER',
        'backend/private.py': 'FAKE_BACKEND_SOURCE_MARKER',
        'data/secret.txt': 'FAKE_DATA_SECRET_MARKER',
        'frontend/private.txt': 'FAKE_FRONTEND_PARENT_MARKER',
    }
    for relative_path, marker in markers.items():
        path = workspace_path / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(marker, encoding='utf-8')

    paths = [
        '/.env', '/backend/.env', '/backend/private.py', '/data/secret.txt',
        '/frontend/private.txt', '/../.env', '/assets/../../backend/private.py',
        '/%2e%2e/%2e%2e/.env', '/%2e%2e/%2e%2e/backend/.env',
        '/assets/%2e%2e/%2e%2e/%2e%2e/data/secret.txt',
        '/%2e%2e%5c%2e%2e%5cbackend%5cprivate.py',
    ]
    with TestClient(create_test_app(monkeypatch, workspace_path)) as client:
        for path in paths:
            response = client.get(path)
            assert response.status_code == 404, path
            assert all(marker not in response.text for marker in markers.values()), path


def test_missing_frontend_build_does_not_disable_api(workspace_path, monkeypatch):
    with TestClient(create_test_app(monkeypatch, workspace_path)) as client:
        health = client.get('/api/health')
        assert health.status_code == 200 and health.json()['status'] == 'ok'
        assert client.get('/').status_code == 404
        assert client.get('/assets/app.css').status_code == 404
