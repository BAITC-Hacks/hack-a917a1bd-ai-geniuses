"""Безопасное чтение поддерживаемых документов без OCR и исполнения формул."""
from __future__ import annotations

from pathlib import Path
from app.schemas import DocumentRecord
from .common import Collector, ParseLimitError
from .docx_parser import read_docx
from .pdf_parser import read_pdf
from .xlsx_parser import read_xlsx


def parse_document(path: Path, document: DocumentRecord, max_chars: int = 200000) -> DocumentRecord:
    result = document.model_copy(deep=True)
    result.blocks = []
    result.warnings = []
    collector = Collector(result, max_chars)
    format_name = result.format.lower().lstrip('.')
    readers = {'docx': read_docx, 'pdf': read_pdf, 'xlsx': read_xlsx}
    if format_name in ('doc', 'xls'):
        result.status = 'failed'
        collector.warn('Старый формат не поддерживается. Конвертируйте .doc в .docx или .xls в .xlsx и загрузите снова.')
        return result
    if format_name not in readers:
        result.status = 'failed'
        collector.warn('Формат не поддерживается. Загрузите DOCX, PDF с текстовым слоем или XLSX.')
        return result
    try:
        if path.stat().st_size == 0:
            raise ParseLimitError('Файл пустой: содержимое отсутствует.')
        # Encrypted OOXML uses an OLE container rather than a ZIP container.
        with path.open('rb') as stream:
            signature = stream.read(8)
        if format_name in ('docx', 'xlsx') and signature == b'\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1':
            raise ParseLimitError('Файл защищён паролем или имеет старый формат. Сохраните незашифрованную копию DOCX/XLSX.')
        readers[format_name](path, collector)
    except ParseLimitError as exc:
        collector.warn(str(exc))
    except Exception as exc:
        # Never expose raw parser messages or document contents through errors.
        if 'password' in type(exc).__name__.lower() or 'encrypt' in type(exc).__name__.lower():
            collector.warn('Документ защищён паролем. Сохраните незашифрованную копию и загрузите снова.')
        else:
            collector.warn('Не удалось прочитать документ: файл повреждён, защищён или не соответствует указанному формату. Проверьте и пересохраните его.')
    if not result.blocks:
        result.status = 'failed'
        if not result.warnings:
            collector.warn('Документ не содержит доступного текста. Проверьте, не является ли он сканом или пустым файлом.')
    else:
        result.status = 'partial' if result.warnings else 'processed'
    return result
