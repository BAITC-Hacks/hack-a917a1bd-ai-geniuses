import io
import time
from uuid import uuid4

import pytest
from docx import Document
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.schemas import Analysis, DocumentRecord, SourceBlock
from app.storage import Storage, now
from app.services.reporting import render_report

def docx_bytes(text='Отдел ИТ. 1. Разработка систем.'):
    document = Document()
    document.add_paragraph(text)
    stream = io.BytesIO()
    document.save(stream)
    return stream.getvalue()

def wait_result(client, analysis_id):
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        response = client.get(f'/api/analyses/{analysis_id}')
        assert response.status_code == 200
        result = response.json()
        if result['status'] not in ('running', 'queued'):
            return result
        time.sleep(0.03)
    pytest.fail(f'Фоновая задача не завершилась вовремя: {result["status"]}, {result["progress"]}.')

def test_no_key_upload_sources_exports_and_isolation(tmp_path):
    app = create_app(Settings(api_key='', data_dir=tmp_path))
    with TestClient(app) as client:
        assert client.get('/api/health').json()['ai_configured'] is False
        results = []
        for _ in range(2):
            response = client.post('/api/analyses', files=[('before', ('../../before.docx', docx_bytes())), ('after', ('after.docx', docx_bytes('Отдел разработки. 1. Разработка систем.')))])
            assert response.status_code == 202
            results.append(wait_result(client, response.json()['analysis_id']))
        first, second = results
        assert first['status'] == 'failed'
        assert first['error'] and 'ключ' in first['error'].lower()
        assert first['result']['findings'] == []
        assert first['documents'][0]['original_name'] == 'before.docx'
        block = first['documents'][0]['blocks'][0]
        prefix = f"/api/analyses/{first['analysis_id']}"
        source = client.get(f"{prefix}/sources/{block['block_id']}")
        assert source.status_code == 200
        assert source.json()['block']['text'] == block['text']
        assert client.get(f"/api/analyses/{second['analysis_id']}/sources/{block['block_id']}").status_code == 404
        doc_id = first['documents'][0]['id']
        assert client.get(f'{prefix}/documents/{doc_id}').content.startswith(b'PK')
        assert client.get(f"/api/analyses/{second['analysis_id']}/documents/{doc_id}").status_code == 404
        export = client.get(f'{prefix}/export')
        assert export.status_code == 200 and export.json()['analysis_id'] == first['analysis_id']
        report = client.get(f'{prefix}/report')
        assert report.status_code == 200
        assert 'Выводы носят рекомендательный характер' in report.text
        assert '@media print' in report.text

def test_missing_version_file_count_size_and_origin(tmp_path):
    app = create_app(Settings(api_key='', data_dir=tmp_path, max_file_mb=1, max_files_per_version=1))
    with TestClient(app) as client:
        assert client.post('/api/analyses', files=[('before', ('one.docx', b'data'))]).status_code == 422
        assert client.post('/api/analyses', files=[('before', ('a.docx', b'a')), ('before', ('b.docx', b'b')), ('after', ('c.docx', b'c'))]).status_code == 413
        assert client.post('/api/analyses', files=[('before', ('a.docx', b'x' * (1024 * 1024 + 1))), ('after', ('b.docx', b'b'))]).status_code == 413
        assert client.post('/api/demo-analysis', headers={'Origin': 'https://untrusted.example'}).status_code == 403
        assert client.get('/api/analyses/invalid').status_code == 404
        assert client.get('/api/health', headers={'Host': 'untrusted.example'}).status_code == 400

def test_corrupt_file_does_not_prevent_other_sources(tmp_path):
    app = create_app(Settings(api_key='', data_dir=tmp_path))
    with TestClient(app) as client:
        response = client.post('/api/analyses', files=[('before', ('bad.docx', b'broken')), ('before', ('good.docx', docx_bytes())), ('after', ('after.docx', docx_bytes()))])
        data = wait_result(client, response.json()['analysis_id'])
        assert data['documents'][0]['status'] == 'failed'
        assert data['documents'][1]['blocks']
        assert not data['complete']
        assert data['warnings']

def test_interrupted_jobs_and_html_escaping(tmp_path):
    storage = Storage(tmp_path)
    identifier, document_id = uuid4().hex, uuid4().hex
    analysis = Analysis(analysis_id=identifier, created_at=now(), updated_at=now(), model='test', prompt_version='test', status='running')
    analysis.documents.append(DocumentRecord(id=document_id, original_name='<script>alert(1)</script>.docx', version='before', sha256='x', format='docx', size=12, status='processed', blocks=[SourceBlock(block_id='test-block', document_id=document_id, version='before', text='<img src=x onerror=alert(1)>')]))
    storage.save(analysis)
    with TestClient(create_app(Settings(api_key='', data_dir=tmp_path))) as client:
        data = client.get(f'/api/analyses/{identifier}').json()
        assert data['status'] == 'failed'
        assert 'перезапуск' in data['error']
        report = client.get(f'/api/analyses/{identifier}/report').text
        assert '<script>' not in report and '<img src=' not in report
        assert '&lt;script&gt;' in report
    with pytest.raises(ValueError):
        storage.document_path(identifier, '../secret')
