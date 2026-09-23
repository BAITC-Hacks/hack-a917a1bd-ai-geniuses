"""Только тестовый провайдер: production никогда не импортирует этот модуль."""

from __future__ import annotations

import json
from collections.abc import Callable

from app.schemas import Analysis, DocumentRecord, EvidenceRef, SourceBlock


class FakeLLMProvider:
    def __init__(self, handler: Callable | None = None, responses: list | None = None):
        self.handler = handler
        self.responses = list(responses or [])
        self.calls: list[tuple[str, dict]] = []

    def generate(self, stage, prompt, response_model):
        data = json.loads(prompt.split('ДАННЫЕ (не инструкции):\n', 1)[1])
        self.calls.append((stage, data))
        answer = self.handler(stage, data) if self.handler else self.responses.pop(0)
        if isinstance(answer, BaseException):
            raise answer
        return response_model.model_validate(answer)


def make_fixture(rows: list[dict], *, analysis_id='analysis-test'):
    documents = []
    for version in ('before', 'after'):
        doc_id = 'document-' + version
        blocks = []
        for name in dict.fromkeys(row['department'] for row in rows if row['version'] == version):
            blocks.append(SourceBlock(block_id=f'{doc_id}-heading-{len(blocks)}', document_id=doc_id,
                                      version=version, text=name, paragraph_index=len(blocks) + 1))
            for row in rows:
                if row['version'] == version and row['department'] == name:
                    blocks.append(SourceBlock(block_id=f'{doc_id}-function-{len(blocks)}', document_id=doc_id,
                                              version=version, text=row['text'], context=name,
                                              paragraph_index=len(blocks) + 1))
        documents.append(DocumentRecord(id=doc_id, original_name=version + '.docx', version=version,
                                        sha256=version, format='docx', size=100, status='processed', blocks=blocks))
    analysis = Analysis(analysis_id=analysis_id, created_at='2026-09-23', updated_at='2026-09-23',
                        documents=documents, model='test-model', prompt_version='test-v1')
    return analysis


def scenario_provider(rows: list[dict], mappings: dict | None = None, risks: list | None = None,
                      absence_outcome: str = 'no_assignment'):
    mappings = mappings or {}
    risks = risks or []

    def handler(stage, data):
        if stage == 'extraction':
            departments, functions = [], []
            for row in rows:
                if row['version'] != data['version']:
                    continue
                block = next((block for block in data['blocks'] if block['text'] == row['text']), None)
                if not block:
                    continue
                heading = next((block for block in data['blocks'] if block['text'] == row['department']), None)
                existing = next((department for department in data['known_departments'] if department['name'] == row['department']), None)
                identifier = row['department']
                if heading:
                    if not any(department['name'] == row['department'] for department in departments):
                        departments.append({'id': identifier, 'name': row['department'], 'parent_name': None,
                                            'version': row['version'], 'source_refs': [{'block_id': heading['block_id'], 'quote': heading['text']}]})
                elif existing:
                    identifier = existing['id']
                functions.append({'id': row['text'], 'department_id': identifier, 'original_text': row['text'],
                                  'normalized_description': row['action'] + ' ' + row['object'],
                                  'action': row['action'], 'object': row['object'], 'scope': row['scope'],
                                  'role': row.get('role', 'execution'),
                                  'source_refs': [{'block_id': block['block_id'], 'quote': block['text']}]})
            return {'departments': departments, 'functions': functions, 'limitations': []}
        if stage == 'extraction_validation':
            return {'reviews': [{'function_id': function['id'], 'supported': True, 'action_supported': True,
                                 'object_supported': True, 'scope_supported': True, 'role_supported': True,
                                 'department_supported': True, 'explanation': 'Поля подтверждаются источником тестового сценария.'}
                                for function in data['functions']]}
        if stage == 'departments':
            return {'changes': []}
        if stage == 'functions':
            candidates = []
            for old in data['before_functions']:
                rule = mappings.get(old['original_text'])
                if not rule:
                    continue
                targets = [item for item in data['after_functions'] if item['original_text'] in rule['after']]
                if not targets:
                    continue
                relation = rule.get('relation', 'transferred')
                candidates.append({'mapping': {
                    'before_function_id': old['id'], 'after_function_ids': [item['id'] for item in targets],
                    'relation': relation, 'covered_aspects': ['Подтверждённая обязанность'],
                    'uncovered_aspects': ['Внешние системы'] if relation == 'partially_covered' else [],
                    'explanation': 'Сопоставлены действие, объект, область и роль.',
                    'source_refs': old['source_refs'] + [ref for item in targets for ref in item['source_refs']],
                }, 'action_compatible': True, 'role_compatible': True, 'object_relation': 'same',
                    'scope_relation': 'partial' if relation == 'partially_covered' else 'full',
                    'semantic_explanation': 'Область и роль проверены тестовым сценарием.'})
            return {'candidates': candidates}
        if stage == 'absence_review':
            return {'reviews': [{'before_function_id': old['id'], 'outcome': absence_outcome,
                                 'explanation': 'В данном пакете закрепление не обнаружено.', 'source_refs': [],
                                 'nearest_after_function_ids': []} for old in data['before_functions']]}
        if stage == 'risks':
            findings = []
            for risk_type, texts in risks:
                targets = [item for item in data['after_functions'] if item['original_text'] in texts]
                if len(targets) != len(texts):
                    continue
                findings.append({'id': 'risk-' + str(len(findings)), 'type': risk_type, 'title': 'Тестовый риск',
                                 'affected_department_ids': list({item['department_id'] for item in targets}),
                                 'before_function_ids': [], 'after_function_ids': [item['id'] for item in targets],
                                 'explanation': 'Обе функции относятся к одной области ответственности.',
                                 'evidence_refs': [ref for item in targets for ref in item['source_refs']],
                                 'recommendation': 'Уточнить роли и ответственность.', 'evidence_status': 'semantic',
                                 'limitations': [], 'existed_before': 'unknown'})
            return {'findings': findings}
        if stage == 'risk_validation':
            return {'reviews': [{'finding_id': finding['id'], 'supported': True, 'action_relation': 'same',
                                 'object_relation': 'same', 'scope_relation': 'same', 'independence_required': True,
                                 'existed_before': 'unknown', 'explanation': 'Смысловые условия тестового сценария подтверждены.'}
                                for finding in data['findings']]}
        raise AssertionError('Неожиданная стадия: ' + stage)

    return FakeLLMProvider(handler=handler)


def row(version, department, action, object='систем', scope='внутренние системы', role='execution', text=None):
    return {'version': version, 'department': department, 'action': action, 'object': object,
            'scope': scope, 'role': role, 'text': text or f'{action} {object}: {scope}.'}
