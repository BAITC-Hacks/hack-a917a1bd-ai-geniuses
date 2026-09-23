"""Заключение строится только из прошедших проверку результатов, без нового LLM-вызова."""
from html import escape
from ..schemas import Analysis, ConclusionItem, EvidenceRef, SourceBlock

RELATIONS = {'preserved': 'Сохранена', 'transferred': 'Передана', 'partially_covered': 'Частично покрыта', 'not_found': 'Закрепление не найдено', 'explicitly_discontinued': 'Прямо отменена', 'uncertain': 'Недостаточно данных'}
CHANGES = {'preserved': 'Сохранение', 'renamed': 'Переименование', 'transformed': 'Преобразование', 'split': 'Разделение', 'merged': 'Объединение', 'created': 'Создание', 'abolished': 'Упразднение', 'uncertain': 'Неопределённость'}
STATUSES = {'direct': 'Прямое указание документа', 'semantic': 'Смысловое сопоставление', 'insufficient': 'Недостаточно данных'}
DISCLAIMER = 'Выводы носят рекомендательный характер и требуют проверки ответственным сотрудником.'

def source_address(block: SourceBlock) -> str:
    parts = []
    if block.page is not None:
        parts.append(f'Страница {block.page}')
    if block.section:
        parts.append(f'Пункт {block.section}')
    if block.paragraph_index is not None:
        parts.append(f'Абзац {block.paragraph_index}')
    if block.table is not None:
        parts.append(f'Таблица {block.table}')
    if block.sheet:
        parts.append(f'Лист «{block.sheet}»')
    if block.cell_range:
        parts.append(f'Ячейки {block.cell_range}')
    return ', '.join(parts) or 'Извлечённый фрагмент'

def build_conclusion(analysis: Analysis) -> list[ConclusionItem]:
    result = analysis.result
    functions = {function.id: function for function in result.functions}
    items = []
    for change in result.department_changes:
        items.append(ConclusionItem(text=f'{CHANGES[change.change_type]}. {change.explanation}', evidence_refs=change.source_refs))
    for mapping in result.function_mappings:
        function = functions.get(mapping.before_function_id)
        if function:
            items.append(ConclusionItem(text=f'{function.normalized_description}: {RELATIONS[mapping.relation].lower()}. {mapping.explanation}', evidence_refs=mapping.source_refs))
    for finding in result.findings:
        items.append(ConclusionItem(text=f'{finding.title}. {finding.explanation} Рекомендация: {finding.recommendation}', finding_ids=[finding.id], evidence_refs=finding.evidence_refs))
    return items

def render_report(analysis: Analysis) -> str:
    documents = {document.id: document for document in analysis.documents}
    blocks = {block.block_id: block for document in analysis.documents for block in document.blocks}
    departments = {department.id: department for department in analysis.result.departments}
    functions = {function.id: function for function in analysis.result.functions}
    e = escape

    def refs_html(refs: list[EvidenceRef]) -> str:
        parts = []
        for ref in refs:
            block = blocks.get(ref.block_id)
            if block is None:
                parts.append('<p>Источник недоступен.</p>')
                continue
            document = documents[block.document_id]
            label = f'{document.original_name} · {"ДО" if block.version == "before" else "ПОСЛЕ"} · {source_address(block)}'
            parts.append(f'<blockquote><p>{e(ref.quote)}</p><footer><a href="#source-{e(block.block_id)}">{e(label)}</a></footer></blockquote>')
        return ''.join(parts)

    def department_name(identifier: str) -> str:
        return departments[identifier].name if identifier in departments else 'Подразделение не установлено'

    document_rows = ''.join(f'<tr><td>{e(doc.original_name)}</td><td>{"ДО" if doc.version == "before" else "ПОСЛЕ"}</td><td>{e({"processed":"Прочитан", "partial":"Частично прочитан", "failed":"Ошибка", "pending":"Ожидает обработки"}[doc.status])}</td><td>{e("; ".join(doc.warnings))}</td></tr>' for doc in analysis.documents)
    changes = ''.join(f'<article><h3>{e(CHANGES[change.change_type])}: {e(", ".join(department_name(x) for x in change.before_department_ids))} → {e(", ".join(department_name(x) for x in change.after_department_ids))}</h3><p>{e(STATUSES[change.evidence_status])}. {e(change.explanation)}</p>{refs_html(change.source_refs)}</article>' for change in analysis.result.department_changes)
    matrix = []
    for mapping in analysis.result.function_mappings:
        old = functions.get(mapping.before_function_id)
        new = [functions[x] for x in mapping.after_function_ids if x in functions]
        search = ', '.join(documents[x].original_name for x in mapping.searched_document_ids if x in documents)
        matrix.append(f'<tr><td>{e(old.normalized_description if old else mapping.before_function_id)}<br><small>{e(department_name(old.department_id)) if old else ""}</small></td><td>{"<br>".join(e(f.normalized_description + " — " + department_name(f.department_id)) for f in new) or "—"}</td><td>{e(RELATIONS[mapping.relation])}</td><td>{e(mapping.explanation)}<p>Покрыто: {e("; ".join(mapping.covered_aspects)) or "—"}</p><p>Не покрыто: {e("; ".join(mapping.uncovered_aspects)) or "—"}</p><p>Проверены ПОСЛЕ: {e(search) or "Не указаны"}</p><p>{e("; ".join(mapping.limitations))}</p>{refs_html(mapping.source_refs)}</td></tr>')
    risks = ''.join(f'<article id="finding-{e(f.id)}"><h3>{e(f.title)}</h3><p>{e(STATUSES[f.evidence_status])} · {e(", ".join(department_name(x) for x in f.affected_department_ids))}</p><p>{e(f.explanation)}</p>{refs_html(f.evidence_refs)}<p><strong>Рекомендация:</strong> {e(f.recommendation)}</p><p><strong>Ограничения:</strong> {e("; ".join(f.limitations)) or "Общие ограничения анализа"}</p><small>Идентификатор вывода: {e(f.id)}</small></article>' for f in analysis.result.findings)
    conclusion = ''.join(f'<article><p>{e(item.text)}</p>{" ".join(f"<a href=\"#finding-{e(fid)}\">{e(fid)}</a>" for fid in item.finding_ids)}{refs_html(item.evidence_refs)}</article>' for item in analysis.result.conclusion)
    sources = ''.join(f'<article id="source-{e(block.block_id)}"><h3>{e(documents[block.document_id].original_name)} · {"ДО" if block.version == "before" else "ПОСЛЕ"}</h3><p>{e(source_address(block))}</p><pre>{e(block.text)}</pre><p><small>Контекст: {e(block.context)}</small></p></article>' for block in blocks.values())
    warnings = list(dict.fromkeys(analysis.warnings + analysis.result.limitations + ([analysis.error] if analysis.error else [])))
    limitations = ''.join(f'<li>{e(warning)}</li>' for warning in warnings)
    processed = sum(doc.status == 'processed' for doc in analysis.documents)
    status = {'queued':'В очереди','running':'Выполняется','completed':'Завершён','partial':'Частичный результат','failed':'Анализ не выполнен'}[analysis.status]
    return f'''<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>OrgLens — аналитическое заключение</title><style>
body{{font:15px/1.6 system-ui,sans-serif;color:#22312e;background:#f5f7f6;margin:0}}main{{max-width:1180px;margin:32px auto;padding:40px;background:white}}h1,h2,h3{{line-height:1.3}}h1{{color:#165a49}}h2{{margin-top:40px;border-bottom:1px solid #ccd8d1;padding-bottom:10px}}table{{border-collapse:collapse;width:100%;font-size:13px}}td,th{{border:1px solid #dce4e0;padding:12px;text-align:left;vertical-align:top}}th{{background:#edf3ef}}article{{border:1px solid #dce4e0;padding:18px;margin:14px 0;break-inside:avoid}}blockquote{{border-left:3px solid #337d67;margin:12px 0;padding:8px 16px;background:#f2f7f4}}footer,small{{color:#53645d}}pre{{white-space:pre-wrap;overflow-wrap:anywhere;font:inherit}}a{{color:#17624c}}.notice{{padding:16px;background:#fff4d8;border:1px solid #dcb968}}@media print{{body{{background:white}}main{{margin:0;padding:0;max-width:none}}a{{color:inherit;text-decoration:none}}h2{{break-after:avoid}}thead{{display:table-header-group}}table{{font-size:10px}}td,th{{padding:5px}}}}
</style></head><body><main><h1>OrgLens</h1><p>Проверка функций и ответственности при реорганизации</p><p>Анализ {e(analysis.analysis_id)} · {e(analysis.created_at)} · {e(status)}</p><p class="notice">{DISCLAIMER}</p><p>Полностью прочитано документов: {processed} из {len(analysis.documents)}. Подразделений: {len(departments)}. Функций: {len(functions)}. Сопоставлений: {len(analysis.result.function_mappings)}.</p><h2>Полнота и ограничения</h2><p>{"Доступный комплект обработан. Полнота корпоративного комплекта не установлена." if analysis.complete else "Анализ неполный. Отсутствие функции нельзя считать окончательно установленным."}</p><ul>{limitations}</ul><h2>Документы и версии</h2><table><thead><tr><th>Документ</th><th>Комплект</th><th>Чтение</th><th>Предупреждения</th></tr></thead><tbody>{document_rows}</tbody></table><h2>Изменения подразделений</h2>{changes or '<p>Проверенные изменения пока отсутствуют.</p>'}<h2>Матрица функций</h2><table><thead><tr><th>ДО</th><th>ПОСЛЕ</th><th>Статус</th><th>Обоснование и источники</th></tr></thead><tbody>{''.join(matrix)}</tbody></table><h2>Риски и пересечения</h2>{risks or '<p>Проверенные выводы пока отсутствуют. Это не подтверждает отсутствие рисков.</p>'}<h2>Итоговое заключение</h2>{conclusion or '<p>Заключение не сформировано.</p>'}<h2>Реестр источников</h2>{sources}<p class="notice">{DISCLAIMER}</p></main></body></html>'''

