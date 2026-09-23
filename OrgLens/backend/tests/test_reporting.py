from app.config import Settings
from app.schemas import DepartmentChange
from app.services.pipeline import run_pipeline
from app.services.reporting import build_conclusion, render_report
from tests.fakes import make_fixture, row, scenario_provider


def test_populated_report_contains_matrix_risks_conclusion_and_resolvable_citations(tmp_path):
    old = row('before', 'Служба А', 'Копирование', text='Копирование внутренних систем.')
    first = row('after', 'Служба Б', 'Копирование', text='Копирование внутренних систем после изменений.')
    second = row('after', 'Служба В', 'Копирование', text='Обеспечивает копирование внутренних систем.')
    rows = [old, first, second]
    provider = scenario_provider(rows, {old['text']: {'after': [first['text'], second['text']]}},
                                 [('potential_duplication', [first['text'], second['text']])])
    analysis = run_pipeline(make_fixture(rows), Settings(api_key='', data_dir=tmp_path), lambda _: None, provider)
    assert analysis.status == 'completed'
    before = next(department for department in analysis.result.departments if department.version == 'before')
    after = next(department for department in analysis.result.departments if department.version == 'after')
    analysis.result.department_changes.append(DepartmentChange(
        before_department_ids=[before.id], after_department_ids=[after.id], change_type='transformed',
        evidence_status='semantic', explanation='Гипотеза преобразования по закреплённым функциям.',
        source_refs=before.source_refs + after.source_refs,
    ))
    analysis.result.conclusion = build_conclusion(analysis)
    report = render_report(analysis)
    assert 'Преобразование' in report and 'Передана' in report
    assert analysis.result.findings and analysis.result.conclusion
    for conclusion in analysis.result.conclusion:
        assert conclusion.finding_ids or conclusion.evidence_refs
        for reference in conclusion.evidence_refs:
            assert f'href="#source-{reference.block_id}"' in report
            assert f'id="source-{reference.block_id}"' in report
    for finding in analysis.result.findings:
        assert f'id="finding-{finding.id}"' in report
        assert f'href="#finding-{finding.id}"' in report
        assert finding.recommendation in report

