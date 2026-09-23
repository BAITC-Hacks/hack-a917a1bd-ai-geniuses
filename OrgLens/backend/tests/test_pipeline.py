from dataclasses import replace
from pathlib import Path

from app.config import Settings
from app.services.evidence import EvidenceValidator
from app.services.llm import LLMError
from app.services.pipeline import run_pipeline
from tests.fakes import FakeLLMProvider, make_fixture, row, scenario_provider


def run(tmp_path, rows, mappings=None, risks=None, **settings_overrides):
    analysis = make_fixture(rows)
    provider = scenario_provider(rows, mappings, risks)
    snapshots = []
    settings = Settings(api_key='', data_dir=tmp_path, model='test-model', **settings_overrides)
    result = run_pipeline(analysis, settings, lambda value: snapshots.append(value.model_dump()), provider)
    return result, provider, snapshots


def test_transfer_across_different_departments_has_no_loss(tmp_path):
    old = row('before', 'Служба А', 'Резервное копирование')
    new = row('after', 'Служба Б', 'Резервное копирование')
    result, provider, snapshots = run(tmp_path, [old, new], {old['text']: {'after': [new['text']]}})
    assert result.status == 'completed'
    assert result.result.function_mappings[0].relation == 'transferred'
    assert not result.result.findings
    assert snapshots and snapshots[-1]['status'] == 'completed'
    assert result.progress.api_calls == len(provider.calls)


def test_search_visits_all_after_batches_and_never_uses_department_name_filter(tmp_path):
    old = row('before', 'Старое подразделение', 'Резервное копирование')
    irrelevant = row('after', 'Старое подразделение', 'Подготовка', object='отчётов')
    target = row('after', 'Совсем другое подразделение', 'Резервное копирование')
    result, provider, _ = run(tmp_path, [old, irrelevant, target],
                              {old['text']: {'after': [target['text']]}}, comparison_batch_size=1)
    assert result.result.function_mappings[0].relation == 'transferred'
    comparisons = [data for stage, data in provider.calls if stage == 'functions']
    assert len(comparisons) == 2
    assert {item['original_text'] for data in comparisons for item in data['after_functions']} == {irrelevant['text'], target['text']}


def test_partial_coverage_is_not_preserved(tmp_path):
    old = row('before', 'Служба А', 'Администрирование', scope='внутренние и внешние системы')
    new = row('after', 'Служба Б', 'Администрирование')
    result, provider, _ = run(tmp_path, [old, new], {old['text']: {'after': [new['text']], 'relation': 'partially_covered'}})
    assert result.status == 'completed'
    mapping = result.result.function_mappings[0]
    assert mapping.relation == 'partially_covered'
    assert mapping.uncovered_aspects
    assert any(stage == 'absence_review' for stage, _ in provider.calls)
    assert any(finding.type == 'potential_loss' for finding in result.result.findings)


def test_not_found_requires_additional_review_of_raw_after_blocks(tmp_path):
    old = row('before', 'Служба А', 'Ведение', object='реестра лицензий')
    new = row('after', 'Служба Б', 'Резервное копирование')
    result, provider, _ = run(tmp_path, [old, new])
    mapping = result.result.function_mappings[0]
    assert mapping.relation == 'not_found'
    assert mapping.searched_document_ids == ['document-after']
    assert 'В проанализированном комплекте документов ПОСЛЕ' in mapping.explanation
    raw_reviews = [data for stage, data in provider.calls if stage == 'absence_review']
    assert raw_reviews
    assert {block['block_id'] for data in raw_reviews for block in data['source_blocks_after']} == {
        block.block_id for doc in result.documents if doc.version == 'after' for block in doc.blocks}


def test_incomplete_document_forbids_loss(tmp_path):
    rows = [row('before', 'Служба А', 'Ведение', object='реестра'), row('after', 'Служба Б', 'Копирование')]
    analysis = make_fixture(rows)
    analysis.documents[1].status = 'partial'
    analysis.documents[1].warnings = ['Страница 2 содержит скан.']
    result = run_pipeline(analysis, Settings(api_key='', data_dir=tmp_path), lambda _: None, scenario_provider(rows))
    assert result.status == 'partial'
    assert not result.complete
    assert result.result.function_mappings[0].relation == 'uncertain'
    assert all(finding.type != 'potential_loss' for finding in result.result.findings)


def test_extraction_reported_limitations_forbid_confident_absence(tmp_path):
    rows = [row('before', 'Служба А', 'Ведение', object='реестра'), row('after', 'Служба Б', 'Копирование')]
    analysis = make_fixture(rows)
    basic = scenario_provider(rows)

    def handler(stage, data):
        answer = basic.handler(stage, data)
        if stage == 'extraction' and data['version'] == 'after':
            answer['limitations'] = ['Не удалось установить принадлежность части обязанностей в таблице.']
        return answer

    result = run_pipeline(analysis, Settings(api_key='', data_dir=tmp_path), lambda _: None, FakeLLMProvider(handler=handler))
    assert result.status == 'partial'
    assert result.result.function_mappings[0].relation == 'uncertain'
    assert not any(finding.type == 'potential_loss' for finding in result.result.findings)


def test_semantic_extraction_review_rejects_wrong_object_scope_and_assignment(tmp_path):
    rows = [row('before', 'Служба А', 'Ведение', object='реестра'), row('after', 'Служба Б', 'Копирование')]
    analysis = make_fixture(rows)
    basic = scenario_provider(rows)

    def handler(stage, data):
        answer = basic.handler(stage, data)
        if stage == 'extraction' and data['version'] == 'after':
            answer['functions'][0]['object'] = 'платежи'
            answer['functions'][0]['scope'] = 'все платежи компании'
        if stage == 'extraction_validation':
            for review in answer['reviews']:
                candidate = next(item for item in data['functions'] if item['id'] == review['function_id'])
                if candidate['object'] == 'платежи':
                    review['supported'] = False
                    review['object_supported'] = False
                    review['scope_supported'] = False
                    review['explanation'] = 'Цитата о системах не подтверждает платежи.'
        return answer

    result = run_pipeline(analysis, Settings(api_key='', data_dir=tmp_path), lambda _: None, FakeLLMProvider(handler=handler))
    assert result.status == 'partial'
    assert not any(function.object == 'платежи' for function in result.result.functions)
    assert not any(finding.type == 'potential_loss' for finding in result.result.findings)


def test_same_responsibility_in_two_departments_is_potential_duplication(tmp_path):
    old = row('before', 'Служба А', 'Подготовка', object='отчётов')
    first = row('after', 'Инфраструктура', 'Администрирование', object='прав доступа', scope='сотрудники внутренних систем', text='Администрирование прав доступа сотрудников внутренних систем.')
    second = row('after', 'Безопасность', 'Администрирование', object='прав доступа', scope='сотрудники внутренних систем', text='Обеспечивает администрирование прав доступа сотрудников внутренних систем.')
    result, _, _ = run(tmp_path, [old, first, second], risks=[('potential_duplication', [first['text'], second['text']])])
    findings = [item for item in result.result.findings if item.type == 'potential_duplication']
    assert len(findings) == 1
    assert len(findings[0].after_function_ids) == 2
    assert len(findings[0].evidence_refs) == 2


def test_execution_and_approval_are_not_duplication_even_if_model_claims_it(tmp_path):
    old = row('before', 'Служба А', 'Подготовка', object='отчётов')
    first = row('after', 'Закупки', 'Подготовка', object='заявок на оплату', scope='закупки')
    second = row('after', 'Бухгалтерия', 'Согласование', object='заявок на оплату', scope='закупки', role='approval')
    result, _, _ = run(tmp_path, [old, first, second], risks=[('potential_duplication', [first['text'], second['text']])])
    assert not any(item.type == 'potential_duplication' for item in result.result.findings)
    assert result.status == 'partial'  # A rejected model proposition is disclosed, not silently endorsed.


def test_execution_and_independent_audit_of_own_work_is_conflict(tmp_path):
    old = row('before', 'Служба А', 'Разработка')
    first = row('after', 'Разработка', 'Разработка', text='Разработка внутренних систем.')
    second = row('after', 'Разработка', 'Независимый аудит', role='independent_audit',
                 text='Независимый аудит внутренних систем, разработанных этим же отделом.')
    result, _, _ = run(tmp_path, [old, first, second], risks=[('potential_conflict', [first['text'], second['text']])])
    assert any(item.type == 'potential_conflict' for item in result.result.findings)


def test_missing_api_key_is_configuration_error_not_fake_result(tmp_path):
    analysis = make_fixture([row('before', 'Служба А', 'Копирование'), row('after', 'Служба Б', 'Копирование')])
    result = run_pipeline(analysis, Settings(api_key='', data_dir=tmp_path), lambda _: None)
    assert result.status == 'failed'
    assert 'OPENAI_API_KEY' in result.error
    assert result.progress.api_calls == 0
    assert not result.result.functions
    assert result.documents[0].blocks


def test_api_error_is_saved_in_russian(tmp_path):
    analysis = make_fixture([row('before', 'Служба А', 'Копирование'), row('after', 'Служба Б', 'Копирование')])
    provider = FakeLLMProvider(responses=[LLMError('OpenAI отклонил API-ключ.')])
    result = run_pipeline(analysis, Settings(api_key='', data_dir=tmp_path), lambda _: None, provider)
    assert result.status == 'failed'
    assert result.error == 'OpenAI отклонил API-ключ.'
    assert len(provider.calls) == 1


def test_budget_preserves_extracted_entities_and_no_confident_loss(tmp_path):
    rows = [row('before', 'Служба А', 'Копирование'), row('after', 'Служба Б', 'Копирование')]
    result, provider, snapshots = run(tmp_path, rows, max_calls=2)
    assert result.status == 'partial'
    assert result.result.functions
    assert result.progress.api_calls == 2
    assert 'лимит запросов' in result.error
    assert all(item.relation == 'uncertain' for item in result.result.function_mappings)
    assert not any(item.type == 'potential_loss' for item in result.result.findings)


def test_production_pipeline_does_not_import_fixture_or_expected_answers():
    service_dir = Path(__file__).resolve().parents[1] / 'app' / 'services'
    code = '\n'.join(path.read_text(encoding='utf-8') for path in service_dir.glob('*.py'))
    assert 'expected_findings' not in code
    assert 'FakeLLMProvider' not in code
    assert 'tests.fakes' not in code
