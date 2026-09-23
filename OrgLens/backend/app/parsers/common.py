"""Общие ограничения и адресация извлечённых фрагментов."""
from __future__ import annotations

import hashlib
import re
import zipfile
from pathlib import Path

from app.schemas import DocumentRecord, SourceBlock

MAX_EXPANDED_BYTES = 60 * 1024 * 1024
MAX_ZIP_ENTRIES = 3000
MAX_BLOCK_CHARS = 2400
MAX_CELLS = 50000
MAX_ROWS = 10000
MAX_COLUMNS = 256
MAX_PAGES = 300
SECTION = re.compile(r"^\s*(?:п(?:ункт)?\.?\s*)?(\d+(?:\.\d+){0,7})[.)]?\s+", re.I)
DEPARTMENT = re.compile(r"^(?:\d+(?:\.\d+)*[.)]?\s+)?(?:Подразделение:\s*)?(?:Отдел\b|Центр\b|Департамент\b|Управление\b|Бухгалтерия\b|Служба\b)", re.I)


class ParseLimitError(ValueError):
    pass


class Collector:
    def __init__(self, document: DocumentRecord, max_chars: int):
        self.document = document
        self.remaining = max(0, max_chars)
        self.stopped = False

    def warn(self, message: str) -> None:
        if message not in self.document.warnings:
            self.document.warnings.append(message)

    def add(self, text: str, locator: str, context: str = '', **metadata) -> None:
        text = text.strip()
        if not text or self.stopped:
            return
        # Split at visible whitespace without combining neighbouring source cells.
        offset = 0
        while offset < len(text):
            if self.remaining <= 0:
                self.warn('Превышен лимит объёма извлечённого текста. Оставшиеся фрагменты не обработаны; анализ неполный.')
                self.stopped = True
                break
            end = min(offset + MAX_BLOCK_CHARS, len(text), offset + self.remaining)
            if end < len(text):
                boundary = max(text.rfind('\n', offset + MAX_BLOCK_CHARS // 3, end),
                               text.rfind(' ', offset + MAX_BLOCK_CHARS // 3, end))
                if boundary > offset:
                    end = boundary
            chunk = text[offset:end].strip()
            if chunk:
                digest = hashlib.sha256(f'{self.document.id}|{locator}|{offset}'.encode()).hexdigest()[:24]
                section = metadata.get('section')
                match = SECTION.match(text)
                if section is None and match:
                    section = match.group(1)
                self.document.blocks.append(SourceBlock(
                    block_id=f'b_{digest}', document_id=self.document.id,
                    version=self.document.version, text=chunk, context=context[:4000],
                    **(metadata | {'section': section}),
                ))
                self.remaining -= len(chunk)
            offset = end
            while offset < len(text) and text[offset].isspace():
                offset += 1


def inspect_archive(path: Path) -> list[str]:
    """Check expansion budgets before a format library opens the OOXML archive."""
    with zipfile.ZipFile(path) as archive:
        entries = archive.infolist()
        if len(entries) > MAX_ZIP_ENTRIES:
            raise ParseLimitError('В архиве документа слишком много элементов; файл пропущен из-за лимита безопасности.')
        if sum(item.file_size for item in entries) > MAX_EXPANDED_BYTES:
            raise ParseLimitError('Распакованный документ превышает лимит 60 МБ; файл пропущен.')
        for item in entries:
            if item.flag_bits & 1:
                raise ParseLimitError('Документ защищён паролем. Сохраните незашифрованную копию.')
            if item.file_size > 1024 * 1024 and item.file_size > max(item.compress_size, 1) * 300:
                raise ParseLimitError('Документ имеет чрезмерный коэффициент сжатия и не обработан.')
        return [item.filename for item in entries]


def heading_context(text: str, previous: str, styled: bool = False) -> str:
    if text and len(text) <= 220:
        if DEPARTMENT.match(text):
            return text
        if styled:
            # A subheading such as "Функции" must not erase its department.
            department = next((line for line in previous.splitlines() if DEPARTMENT.match(line)), '')
            return '\n'.join(part for part in [department, text] if part)
    return previous
