import hashlib
import os
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .config import ROOT, Settings
from .schemas import Analysis, DocumentRecord, Progress
from .storage import Storage, now
from .services.reporting import build_conclusion, render_report, source_address

LOCAL_ORIGINS = ['http://localhost:5173', 'http://127.0.0.1:5173', 'http://localhost:8000', 'http://127.0.0.1:8000']

class BodyTooLarge(Exception):
    pass

class BodyLimitMiddleware:
    def __init__(self, app, maximum: int):
        self.app, self.maximum = app, maximum

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http':
            return await self.app(scope, receive, send)
        received = 0

        async def limited_receive():
            nonlocal received
            message = await receive()
            received += len(message.get('body', b''))
            if received > self.maximum:
                raise BodyTooLarge()
            return message

        try:
            await self.app(scope, limited_receive, send)
        except BodyTooLarge:
            await JSONResponse({'detail': 'Превышен общий лимит размера загрузки.'}, status_code=413)(scope, receive, send)

def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    storage = Storage(settings.data_dir)

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        storage.recover_interrupted()
        application.state.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='orglens')
        yield
        application.state.executor.shutdown(wait=True, cancel_futures=True)

    app = FastAPI(title='OrgLens — анализ организационных изменений', lifespan=lifespan)
    app.state.settings = settings
    app.state.storage = storage
    app.add_middleware(BodyLimitMiddleware, maximum=settings.max_file_mb * 1024 * 1024 * settings.max_files_per_version * 2 + 1024 * 1024)
    app.add_middleware(CORSMiddleware, allow_origins=LOCAL_ORIGINS, allow_methods=['GET', 'POST'], allow_headers=['Content-Type'])
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=['127.0.0.1', 'localhost', 'testserver'])

    @app.middleware('http')
    async def local_only(request: Request, call_next):
        origin = request.headers.get('origin')
        if request.method == 'POST' and origin and origin not in LOCAL_ORIGINS:
            return JSONResponse({'detail': 'Запросы разрешены только с локального интерфейса OrgLens.'}, status_code=403)
        response = await call_next(request)
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'no-referrer'
        response.headers['Cache-Control'] = 'no-store'
        return response

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        return JSONResponse({'detail': 'Проверьте параметры запроса. Необходимы документы обоих комплектов: ДО и ПОСЛЕ.'}, status_code=422)

    def load_analysis(analysis_id: str) -> Analysis:
        try:
            return storage.load(analysis_id)
        except (ValueError, FileNotFoundError):
            raise HTTPException(404, 'Анализ не найден.') from None

    def run_job(analysis_id: str):
        # Один рабочий поток: исходные документы и результаты разных анализов изолированы.
        analysis = storage.load(analysis_id)
        try:
            from .parsers import parse_document
            from .services.pipeline import run_pipeline
            analysis.status = 'running'
            analysis.progress = Progress(stage='Чтение документов', total=len(analysis.documents))
            storage.save(analysis)
            remaining = settings.max_analysis_chars
            for index, document in enumerate(analysis.documents):
                if remaining <= 0:
                    document.status = 'partial'
                    document.warnings.append('Превышен общий лимит извлечённого текста. Документ не прочитан.')
                else:
                    document = parse_document(storage.document_path(analysis_id, document.id), document, max_chars=min(remaining, settings.max_document_chars))
                    analysis.documents[index] = document
                    remaining -= sum(len(block.text) for block in document.blocks)
                analysis.warnings.extend(f'{document.original_name}: {warning}' for warning in document.warnings)
                analysis.progress.processed = index + 1
                storage.save(analysis)
            analysis.complete = all(document.status == 'processed' for document in analysis.documents)
            if not all(any(document.blocks and document.version == version for document in analysis.documents) for version in ('before', 'after')):
                analysis.status = 'failed'
                analysis.complete = False
                analysis.error = 'Недостаточно читаемых данных: нужны фрагменты документов ДО и ПОСЛЕ. Проверьте предупреждения файлов.'
            else:
                analysis = run_pipeline(analysis, settings, storage.save)
            analysis.progress.stage = 'Формирование заключения'
            analysis.result.conclusion = build_conclusion(analysis)
            analysis.progress.stage = 'Завершено' if analysis.status == 'completed' else 'Обработка завершена с ограничениями'
            storage.save(analysis)
        except Exception:
            # Не возвращаем исключения SDK/содержимое запросов: в них могут быть секреты и документы.
            analysis.status = 'partial' if analysis.result.functions else 'failed'
            analysis.complete = False
            analysis.error = 'Не удалось завершить обработку. Сохранённые источники доступны; проверьте конфигурацию и повторите анализ.'
            analysis.progress.stage = 'Обработка остановлена'
            analysis.result.conclusion = build_conclusion(analysis)
            storage.save(analysis)

    def new_analysis() -> Analysis:
        return Analysis(analysis_id=uuid4().hex, created_at=now(), updated_at=now(), model=settings.model, prompt_version=settings.prompt_version)

    def save_original(analysis: Analysis, name: str, version: str, content: bytes) -> None:
        identifier = uuid4().hex
        clean_name = name.replace('\\', '/').split('/')[-1][:240] or 'Без имени'
        suffix = Path(clean_name).suffix.lower().lstrip('.')
        record = DocumentRecord(id=identifier, original_name=clean_name, version=version, sha256=hashlib.sha256(content).hexdigest(), format=suffix, size=len(content))
        path = storage.document_path(analysis.analysis_id, identifier)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix('.tmp')
        temporary.write_bytes(content)
        os.replace(temporary, path)
        analysis.documents.append(record)

    def enqueue(analysis: Analysis) -> dict:
        storage.save(analysis)
        app.state.executor.submit(run_job, analysis.analysis_id)
        return {'analysis_id': analysis.analysis_id}

    @app.get('/api/health')
    def health():
        return {'status': 'ok', 'ai_configured': bool(settings.api_key.strip()), 'model': settings.model, 'limits': {'max_file_mb': settings.max_file_mb, 'max_files_per_version': settings.max_files_per_version, 'max_analysis_chars': settings.max_analysis_chars}}

    @app.post('/api/analyses', status_code=202)
    async def create_analysis(before: list[UploadFile] = File(...), after: list[UploadFile] = File(...)):
        if not before or not after:
            raise HTTPException(422, 'Загрузите документы ДО и ПОСЛЕ.')
        if max(len(before), len(after)) > settings.max_files_per_version:
            raise HTTPException(413, f'Не более {settings.max_files_per_version} файлов в каждом комплекте.')
        analysis = new_analysis()
        # Сначала проверяем весь комплект, затем атомарно публикуем задачу.
        incoming = []
        try:
            for version, files in [('before', before), ('after', after)]:
                for file in files:
                    content = await file.read(settings.max_file_mb * 1024 * 1024 + 1)
                    if len(content) > settings.max_file_mb * 1024 * 1024:
                        raise HTTPException(413, f'Файл превышает лимит {settings.max_file_mb} МБ.')
                    incoming.append((file.filename or 'Без имени', version, content))
        finally:
            for file in before + after:
                await file.close()
        for name, version, content in incoming:
            save_original(analysis, name, version, content)
        return enqueue(analysis)

    @app.post('/api/demo-analysis', status_code=202)
    def demo_analysis():
        analysis = new_analysis()
        for version in ('before', 'after'):
            files = sorted((ROOT / 'demo' / version).iterdir())
            for path in files:
                if path.is_file() and path.suffix.lower() in ('.docx', '.pdf', '.xlsx'):
                    save_original(analysis, path.name, version, path.read_bytes())
        if not all(any(document.version == version for document in analysis.documents) for version in ('before', 'after')):
            raise HTTPException(503, 'Демонстрационные документы отсутствуют. Запустите scripts/generate_demo.py.')
        return enqueue(analysis)

    @app.get('/api/analyses/{analysis_id}', response_model=Analysis)
    def get_analysis(analysis_id: str):
        return load_analysis(analysis_id)

    @app.get('/api/analyses/{analysis_id}/sources/{block_id}')
    def get_source(analysis_id: str, block_id: str):
        analysis = load_analysis(analysis_id)
        for document in analysis.documents:
            for block in document.blocks:
                if block.block_id == block_id:
                    return {'block': block, 'document': document.model_dump(exclude={'blocks'}), 'address': source_address(block)}
        raise HTTPException(404, 'Источник не найден в этом анализе.')

    @app.get('/api/analyses/{analysis_id}/documents/{document_id}')
    def get_document(analysis_id: str, document_id: str):
        analysis = load_analysis(analysis_id)
        document = next((doc for doc in analysis.documents if doc.id == document_id), None)
        if document is None:
            raise HTTPException(404, 'Документ не найден в этом анализе.')
        path = storage.document_path(analysis_id, document_id)
        if not path.is_file():
            raise HTTPException(404, 'Оригинал документа недоступен.')
        media = {'pdf': 'application/pdf', 'docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document', 'xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'}.get(document.format, 'application/octet-stream')
        return FileResponse(path, media_type=media, filename=document.original_name, content_disposition_type='inline' if document.format == 'pdf' else 'attachment')

    @app.get('/api/analyses/{analysis_id}/report', response_class=HTMLResponse)
    def get_report(analysis_id: str):
        response = HTMLResponse(render_report(load_analysis(analysis_id)))
        response.headers['Content-Disposition'] = f'attachment; filename="orglens-{analysis_id}.html"'
        response.headers['Content-Security-Policy'] = "default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'"
        return response

    @app.get('/api/analyses/{analysis_id}/export')
    def export_result(analysis_id: str):
        analysis = load_analysis(analysis_id)
        return JSONResponse(analysis.model_dump(mode='json'), headers={'Content-Disposition': f'attachment; filename="orglens-{analysis_id}.json"'})

    # Сборка интерфейса доступна на том же локальном адресе, что и API.
    # Отдаём только dist, не каталог проекта с ключами и документами.
    frontend_dist = ROOT / 'frontend' / 'dist'
    if (frontend_dist / 'index.html').is_file():
        app.mount('/', StaticFiles(directory=frontend_dist, html=True), name='frontend')

    return app

app = create_app()
