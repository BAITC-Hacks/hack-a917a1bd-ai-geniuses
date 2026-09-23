"""Управляемый синхронный процесс; запускается рабочим потоком FastAPI."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable

from app.schemas import Analysis, FunctionMapping, SourceBlock
from app.services.comparison import add_loss_findings, compare_departments, compare_functions, compare_risks
from app.services.evidence import EvidenceValidator
from app.services.extraction import extract_batch, extraction_batches
from app.services.llm import LLMClient, LLMError, LLMProvider


def prepare_blocks(analysis: Analysis, settings: Any) -> list[SourceBlock]:
    """Лимиты применяются явно; полные исходные блоки остаются доступны в реестре."""
    available: list[SourceBlock] = []
    total_chars = 0
    analysis.complete = True
    if {doc.version for doc in analysis.documents} != {'before', 'after'}:
        analysis.complete = False
        analysis.warnings.append('Для полного сравнения требуются документы ДО и ПОСЛЕ.')
    for doc in analysis.documents:
        if doc.status != 'processed' or not doc.blocks:
            analysis.complete = False
            analysis.warnings.append(f'Документ «{doc.original_name}» обработан не полностью; проверьте его предупреждения.')
        doc_chars = 0
        skipped = 0
        for block in doc.blocks:
            if block.document_id != doc.id or block.version != doc.version:
                analysis.complete = False
                skipped += len(block.text)
                continue
            remaining = min(settings.max_document_chars - doc_chars, settings.max_analysis_chars - total_chars)
            if remaining <= 0:
                skipped += len(block.text)
                continue
            text = block.text[:remaining]
            skipped += len(block.text) - len(text)
            if text.strip():
                available.append(block.model_copy(update={'text': text}))
                doc_chars += len(text)
                total_chars += len(text)
        if skipped:
            analysis.complete = False
            analysis.warnings.append(f'Лимит обработки: в документе «{doc.original_name}» не проанализировано символов: {skipped}. '
                                     'Полный извлечённый текст сохранён в источниках.')
    if {block.version for block in available} != {'before', 'after'}:
        analysis.complete = False
        analysis.warnings.append('Нет читаемых фрагментов хотя бы одного комплекта. Вывод об отсутствии функций недопустим.')
    return available


def ensure_unfinished_mappings(analysis: Analysis) -> None:
    departments = {item.id: item for item in analysis.result.departments}
    mapped = {item.before_function_id for item in analysis.result.function_mappings}
    for function in analysis.result.functions:
        if departments[function.department_id].version == 'before' and function.id not in mapped:
            analysis.result.function_mappings.append(FunctionMapping(
                before_function_id=function.id, after_function_ids=[], relation='uncertain',
                covered_aspects=[], uncovered_aspects=[function.normalized_description],
                explanation='Сопоставление не завершено. Подтверждённый вывод об отсутствии функции сделать нельзя.',
                source_refs=function.source_refs,
                limitations=['Обработка прервана до полного сопоставления документов ПОСЛЕ.'],
            ))
    for mapping in analysis.result.function_mappings:
        if mapping.relation == 'not_found':
            mapping.relation = 'uncertain'
            mapping.explanation = 'Проверка не завершена; вывод об отсутствии закрепления функции требует повторного анализа.'
    # Do not leave a loss conclusion behind when a later stage failed.
    for finding in analysis.result.findings:
        if finding.type == 'potential_loss':
            finding.type = 'insufficient_data'
            finding.evidence_status = 'insufficient'
            finding.title = 'Требуется дополнительная проверка закрепления функции'
            finding.limitations.append('Анализ завершён частично; окончательный вывод об отсутствии не сделан.')


def run_pipeline(analysis: Analysis, settings: Any, save: Callable[[Analysis], None],
                 provider: LLMProvider | None = None) -> Analysis:
    analysis.status = 'running'
    analysis.error = None
    client = None

    def persist(stage: str | None = None) -> None:
        if stage:
            analysis.progress.stage = stage
        analysis.updated_at = datetime.now(timezone.utc).isoformat()
        analysis.warnings = list(dict.fromkeys(analysis.warnings))
        analysis.result.limitations = list(dict.fromkeys(analysis.result.limitations))
        save(analysis)

    def record_call(count: int) -> None:
        analysis.progress.api_calls = count
        persist()

    try:
        blocks = prepare_blocks(analysis, settings)
        validator = EvidenceValidator(analysis)
        batches = extraction_batches(blocks, settings.extraction_batch_chars)
        analysis.progress.processed = 0
        analysis.progress.total = len(batches) + 4
        persist('Подготовка AI-анализа')
        client = LLMClient(settings, analysis.analysis_id, provider, on_call=record_call)
        if not blocks:
            raise LLMError('В документах не найден доступный текст. Проверьте формат, защиту файла или наличие сканированных страниц.')
        rejected_total = 0
        for index, batch in enumerate(batches):
            persist(f'Извлечение подразделений и функций: пакет {index + 1} из {len(batches)}')
            previous_limitations = len(analysis.result.limitations)
            rejected = extract_batch(client, batch, validator, analysis.result)
            if len(analysis.result.limitations) > previous_limitations:
                analysis.complete = False
                analysis.warnings.append('AI-извлечение сообщило об ограничениях; полнота закрепления функций требует проверки.')
            if rejected:
                analysis.complete = False
                analysis.warnings.append(f'Извлечение: сущностей с неподтверждёнными источниками отклонено: {rejected}.')
            rejected_total += rejected
            analysis.progress.processed += 1
            persist()
        if not analysis.result.departments or not analysis.result.functions:
            analysis.complete = False
            analysis.warnings.append('Недостаточно проверенных подразделений или функций для полного сравнения.')
        persist('Сопоставление подразделений и прямых распоряжений')
        rejected_total += compare_departments(analysis, client, validator, blocks)
        analysis.progress.processed += 1
        persist()
        rejected_total += compare_functions(analysis, client, validator,
                                             [block for block in blocks if block.version == 'after'],
                                             settings.comparison_batch_size, settings.extraction_batch_chars, persist)
        analysis.progress.processed += 1
        add_loss_findings(analysis, validator)
        persist('Проверка возможного дублирования и конфликта независимости')
        rejected_total += compare_risks(analysis, client, validator, settings.comparison_batch_size, persist)
        analysis.progress.processed += 1
        if rejected_total:
            analysis.complete = False
            analysis.warnings.append(f'Неподтверждённые сущности или выводы исключены: {rejected_total}. '
                                     'Проверка цитат и смысловых условий не пройдена; результат частичный.')
        if not analysis.complete:
            ensure_unfinished_mappings(analysis)
            analysis.result.limitations.append('Обработка неполная; отсутствие закрепления функции не доказано. '
                                               'Дополните документы или устраните ограничения перед повторной проверкой.')
        analysis.status = 'completed' if analysis.complete else 'partial'
        analysis.progress.processed = analysis.progress.total
        persist('Анализ завершён' if analysis.complete else 'Сохранён частичный результат')
    except LLMError as exc:
        analysis.complete = False
        analysis.error = str(exc)
        analysis.result.limitations.append(str(exc))
        ensure_unfinished_mappings(analysis)
        if analysis.result.functions:
            add_loss_findings_if_missing(analysis)
        analysis.status = 'partial' if analysis.result.departments or analysis.result.functions else 'failed'
        persist('Анализ прерван; данные сохранены')
    except Exception:
        # Never return raw SDK exceptions, response bodies, document content or configuration secrets.
        analysis.complete = False
        analysis.error = 'Внутренняя ошибка обработки анализа. Доступный результат и документы сохранены.'
        analysis.result.limitations.append(analysis.error)
        ensure_unfinished_mappings(analysis)
        analysis.status = 'partial' if analysis.result.departments or analysis.result.functions else 'failed'
        persist('Ошибка анализа; данные сохранены')
    return analysis


def add_loss_findings_if_missing(analysis: Analysis) -> None:
    add_loss_findings(analysis, EvidenceValidator(analysis))
    seen = set()
    unique = []
    for finding in analysis.result.findings:
        if finding.id not in seen:
            unique.append(finding)
            seen.add(finding.id)
    analysis.result.findings = unique
