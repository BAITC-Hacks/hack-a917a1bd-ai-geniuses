"""Ограниченная живая оценка модели; ожидаемые ответы читаются ПОСЛЕ анализа."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))

from app.config import Settings
from app.parsers import parse_document
from app.schemas import Analysis, DocumentRecord
from app.services.evidence import EvidenceValidator, normalized
from app.services.pipeline import run_pipeline
from app.services.reporting import build_conclusion, render_report
from app.storage import Storage, atomic_json, now


def has_terms(text: str, terms: list[str]) -> bool:
    return all(term.casefold() in text.casefold() for term in terms)


def evaluate(analysis: Analysis, expected: dict) -> list[dict]:
    """Сравнивает типы событий, участников и смысловые термины, а не полные фразы."""
    checks: list[dict] = []
    departments = {item.id: item for item in analysis.result.departments}
    functions = {item.id: item for item in analysis.result.functions}

    def check(label, passed):
        checks.append({'check': label, 'passed': bool(passed)})

    def names(ids):
        return {normalized(departments[identifier].name).casefold() for identifier in ids if identifier in departments}

    def event_matches(finding, requirement):
        text = ' '.join([finding.title, finding.explanation, *[
            functions[identifier].original_text + ' ' + functions[identifier].normalized_description
            for identifier in finding.before_function_ids + finding.after_function_ids if identifier in functions]])
        return (finding.type == requirement['type']
                and has_terms(text, requirement.get('terms', []))
                and {name.casefold() for name in requirement.get('department_names', [])}
                    <= names(finding.affected_department_ids))

    check('Анализ завершён полностью', analysis.status == 'completed' and analysis.complete)
    for requirement in expected.get('department_changes', []):
        found = any(change.change_type == requirement['change_type']
                    and names(change.before_department_ids) == {name.casefold() for name in requirement['before_names']}
                    and names(change.after_department_ids) == {name.casefold() for name in requirement['after_names']}
                    and (not requirement.get('evidence_status') or change.evidence_status == requirement['evidence_status'])
                    for change in analysis.result.department_changes)
        check('Изменение подразделений: ' + requirement['change_type'] + ' / ' + ', '.join(requirement['after_names']), found)
    for requirement in expected.get('function_mappings', []):
        found = False
        for mapping in analysis.result.function_mappings:
            old = functions.get(mapping.before_function_id)
            if not old or mapping.relation != requirement['relation']:
                continue
            if not has_terms(old.original_text + ' ' + old.normalized_description, requirement['function_terms']):
                continue
            after_names = names([functions[identifier].department_id for identifier in mapping.after_function_ids if identifier in functions])
            if requirement.get('after_department') and requirement['after_department'].casefold() not in after_names:
                continue
            found = True
        check('Сопоставление: ' + requirement['relation'] + ' / ' + ', '.join(requirement['function_terms']), found)
    for requirement in expected.get('findings', []):
        check('Ожидаемый риск: ' + requirement['type'] + ' / ' + ', '.join(requirement.get('terms', [])),
              any(event_matches(finding, requirement) for finding in analysis.result.findings))
    for requirement in expected.get('forbidden_findings', []):
        check('Нет ложного риска: ' + requirement['type'] + ' / ' + ', '.join(requirement.get('terms', requirement.get('department_names', []))),
              not any(event_matches(finding, requirement) for finding in analysis.result.findings))
    validator = EvidenceValidator(analysis)
    source_errors = []
    for department in departments.values():
        if not validator.validate_department(department, department.version):
            source_errors.append(department.id)
    for function in functions.values():
        if not validator.validate_function(function, departments):
            source_errors.append(function.id)
    for change in analysis.result.department_changes:
        if not validator.validate_department_change(change, departments):
            source_errors.append('изменение подразделения')
    for mapping in analysis.result.function_mappings:
        if not validator.validate_mapping(mapping, functions, departments, search_complete=analysis.complete):
            source_errors.append(mapping.before_function_id)
    for finding in analysis.result.findings:
        if (not validator.validate_refs(finding.evidence_refs)
                or any(identifier not in functions or not validator.covers(finding.evidence_refs, functions[identifier].source_refs)
                       for identifier in finding.before_function_ids + finding.after_function_ids)):
            source_errors.append(finding.id)
    check('Все цитаты, версии, участники и ссылки разрешаются в реестре анализа', not source_errors)
    return checks


def main() -> int:
    parser = argparse.ArgumentParser(description='Живая оценка OrgLens на синтетических документах через OpenAI API.')
    parser.add_argument('--max-calls', type=int, default=40, help='Максимум физических запросов API, включая повторы (по умолчанию 40).')
    parser.add_argument('--result', type=Path, help='Проверить ранее сохранённый analysis.json без новых запросов API.')
    args = parser.parse_args()
    if args.result:
        analysis = Analysis.model_validate_json(args.result.read_text(encoding='utf-8'))
    else:
        settings = Settings()
        if not settings.api_key.strip():
            print('Живая оценка НЕ запускалась: OPENAI_API_KEY не настроен. Тесты с FakeLLMProvider не подтверждают качество реальной модели.')
            return 2
        if args.max_calls < 1:
            parser.error('--max-calls должен быть положительным.')
        settings = replace(settings, max_calls=min(args.max_calls, settings.max_calls), max_concurrency=1)
        storage = Storage(settings.data_dir)
        analysis = Analysis(analysis_id=uuid4().hex, created_at=now(), updated_at=now(),
                            model=settings.model, prompt_version=settings.prompt_version)
        remaining = settings.max_analysis_chars
        for version in ('before', 'after'):
            for path in sorted((ROOT / 'demo' / version).iterdir()):
                if path.suffix.lower() not in {'.docx', '.xlsx', '.pdf'}:
                    continue
                content = path.read_bytes()
                record = DocumentRecord(id=uuid4().hex, original_name=path.name, version=version,
                                        sha256=hashlib.sha256(content).hexdigest(), format=path.suffix.lstrip('.'), size=len(content))
                original = storage.document_path(analysis.analysis_id, record.id)
                original.parent.mkdir(parents=True, exist_ok=True)
                original.write_bytes(content)
                record = parse_document(original, record, max_chars=max(0, min(remaining, settings.max_document_chars)))
                remaining -= sum(len(block.text) for block in record.blocks)
                analysis.documents.append(record)
        print(f'Живая оценка: модель {settings.model}; лимит запросов {settings.max_calls}; анализ {analysis.analysis_id}.')
        storage.save(analysis)
        analysis = run_pipeline(analysis, settings, storage.save)
        analysis.result.conclusion = build_conclusion(analysis)
        storage.save(analysis)
        (storage.folder(analysis.analysis_id) / 'report.html').write_text(render_report(analysis), encoding='utf-8')
        print(f'Статус: {analysis.status}; фактических запросов API: {analysis.progress.api_calls}.')
        if analysis.error:
            print(analysis.error)
    # This reference file is evaluation-only and never becomes input to the production pipeline.
    expected = json.loads((ROOT / 'demo' / 'expected_findings.json').read_text(encoding='utf-8'))
    checks = evaluate(analysis, expected)
    for item in checks:
        print(('ПРОЙДЕНО: ' if item['passed'] else 'НЕ ПРОЙДЕНО: ') + item['check'])
    if not args.result:
        atomic_json(storage.folder(analysis.analysis_id) / 'evaluation.json', {
            'analysis_id': analysis.analysis_id, 'model': analysis.model, 'prompt_version': analysis.prompt_version,
            'api_calls': analysis.progress.api_calls, 'checks': checks,
            'limitation': 'Один синтетический комплект не доказывает качество на произвольных реальных документах.',
        })
        print('Результат и проверка сохранены: ' + str(storage.folder(analysis.analysis_id)))
    return 0 if all(item['passed'] for item in checks) else 1


if __name__ == '__main__':
    raise SystemExit(main())
