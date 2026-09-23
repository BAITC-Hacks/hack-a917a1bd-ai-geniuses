from __future__ import annotations

from pathlib import Path
from collections import defaultdict
import pdfplumber

from .common import Collector, MAX_PAGES, heading_context


def read_pdf(path: Path, collector: Collector) -> None:
    context = ''
    with pdfplumber.open(path) as pdf:
        if len(pdf.pages) > MAX_PAGES:
            collector.warn(f'PDF содержит {len(pdf.pages)} страниц. Лимит — {MAX_PAGES}; остальные страницы не прочитаны.')
        for page_number, page in enumerate(pdf.pages[:MAX_PAGES], 1):
            if collector.stopped:
                break
            try:
                tables = page.find_tables()
                boxes = [table.bbox for table in tables]
                # Table cells are individual sources. Exclude their characters from prose.
                def outside_tables(obj):
                    x = (obj.get('x0', 0) + obj.get('x1', 0)) / 2
                    y = (obj.get('top', 0) + obj.get('bottom', 0)) / 2
                    return not any(left <= x <= right and top <= y <= bottom for left, top, right, bottom in boxes)
                prose = page.filter(outside_tables) if boxes else page
                text = prose.extract_text(x_tolerance=2, y_tolerance=3) or ''
                image_count = len(page.images)
                if image_count:
                    collector.warn(f'PDF, страница {page_number}: изображения ({image_count}) не распознаны; текстовый слой прочитан отдельно, анализ неполный.')
                if not tables and (page.rects or page.curves):
                    collector.warn(f'PDF, страница {page_number}: обнаружены графические элементы. Связи схем и их графический смысл не распознаны; анализ неполный.')
                if not text.strip() and not tables:
                    collector.warn(f'PDF, страница {page_number}: нет доступного текстового слоя. Страница не прочитана (скан, графика или пустая страница).')
                    continue
                # Widely separated word groups on repeated baselines can be independent
                # columns or an unruled matrix. Do not flatten them into invented duties.
                lines = defaultdict(list)
                for word in prose.extract_words():
                    lines[round(word['top'] / 4)].append(word)
                separated_lines = 0
                for words in lines.values():
                    words.sort(key=lambda word: word['x0'])
                    if any(right['x0'] - left['x1'] > 50 for left, right in zip(words, words[1:])):
                        separated_lines += 1
                if separated_lines >= 2:
                    collector.warn(f'PDF, страница {page_number}: неоднозначная многоколоночная разметка вне распознанных таблиц. Этот текст пропущен, чтобы не смешать ответственность подразделений.')
                    text = ''
                if '(cid:' in text or '\ufffd' in text:
                    collector.warn(f'PDF, страница {page_number}: часть символов не распознана; проверьте исходник.')
                # Join wrapped lines to the numbered clause, but retain real line order and no new words.
                groups: list[str] = []
                pending: list[str] = []
                from .common import SECTION, DEPARTMENT
                for line in text.splitlines():
                    starts_group = bool(SECTION.match(line) or DEPARTMENT.match(line))
                    if starts_group and pending:
                        groups.append('\n'.join(pending))
                        pending = []
                    pending.append(line)
                if pending:
                    groups.append('\n'.join(pending))
                for index, group in enumerate(groups, 1):
                    first = group.splitlines()[0]
                    context = heading_context(first, context)
                    collector.add(group, f'page:{page_number}:group:{index}', context, page=page_number)
                for table_index, table in enumerate(tables, 1):
                    rows = table.extract() or []
                    headers = [str(value or '').strip() for value in rows[0]] if rows else []
                    department_column = next((i for i, header in enumerate(headers) if header.lower() in ('подразделение', 'ответственное подразделение')), None)
                    for row_index, row in enumerate(rows, 1):
                        department = str(row[department_column] or '') if department_column is not None and row_index > 1 and department_column < len(row) else ''
                        for column_index, value in enumerate(row, 1):
                            header = headers[column_index - 1] if row_index > 1 and column_index <= len(headers) else ''
                            cell_context = '\n'.join(part for part in [context, f'Заголовок колонки: {header}' if header else '', f'Подразделение строки: {department}' if department else ''] if part)
                            address = f'R{row_index}C{column_index}'
                            collector.add(str(value or ''), f'page:{page_number}:table:{table_index}:{address}', cell_context,
                                          page=page_number, table=table_index, cell_range=address)
            except Exception:
                collector.warn(f'PDF, страница {page_number}: ошибка чтения. Страница пропущена; анализ неполный.')
            finally:
                page.close()
