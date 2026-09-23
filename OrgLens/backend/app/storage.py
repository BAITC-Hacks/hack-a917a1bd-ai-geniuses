import json
import os
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4
from .schemas import Analysis

def now() -> str:
    return datetime.now(timezone.utc).isoformat()

def validate_id(value: str) -> str:
    if not re.fullmatch(r'[a-f0-9]{32}', value):
        raise ValueError('Некорректный идентификатор.')
    return value

def atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f'.{path.name}.{uuid4().hex}.tmp')
    try:
        with temporary.open('w', encoding='utf-8') as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)

class Storage:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self._lock = threading.RLock()
        self.root.mkdir(parents=True, exist_ok=True)

    def folder(self, analysis_id: str) -> Path:
        return self.root / validate_id(analysis_id)

    def save(self, analysis: Analysis) -> None:
        with self._lock:
            analysis.updated_at = now()
            atomic_json(self.folder(analysis.analysis_id) / 'analysis.json', analysis.model_dump(mode='json'))

    def load(self, analysis_id: str) -> Analysis:
        with self._lock:
            return Analysis.model_validate_json((self.folder(analysis_id) / 'analysis.json').read_text(encoding='utf-8'))

    def document_path(self, analysis_id: str, document_id: str) -> Path:
        return self.folder(analysis_id) / 'documents' / validate_id(document_id)

    def recover_interrupted(self) -> None:
        for path in self.root.glob('*/analysis.json'):
            try:
                analysis = Analysis.model_validate_json(path.read_text(encoding='utf-8'))
                if analysis.status in ('queued', 'running'):
                    analysis.status = 'partial' if analysis.result.functions else 'failed'
                    analysis.complete = False
                    analysis.error = 'Обработка прервана перезапуском сервера. Создайте новый анализ; сохранённые источники доступны.'
                    analysis.warnings.append(analysis.error)
                    analysis.progress.stage = 'Обработка прервана'
                    self.save(analysis)
            except (ValueError, OSError):
                continue
