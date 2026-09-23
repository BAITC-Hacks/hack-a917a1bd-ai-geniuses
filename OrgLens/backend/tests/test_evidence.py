import pytest

from app.schemas import Department, DepartmentChange, EvidenceRef, Finding, Function
from app.services.evidence import EvidenceValidator
from tests.fakes import make_fixture, row


@pytest.fixture
def analysis():
    return make_fixture([row('before', 'Служба А', 'Копирование'), row('after', 'Служба Б', 'Копирование')])


def test_exact_quote_and_whitespace_normalization(analysis):
    block = analysis.documents[0].blocks[-1]
    validator = EvidenceValidator(analysis)
    assert validator.validate_ref(EvidenceRef(block_id=block.block_id, quote=block.text), 'before')
    assert validator.validate_ref(EvidenceRef(block_id=block.block_id, quote=block.text.replace(' ', '\n  ')), 'before')
    assert not validator.validate_ref(EvidenceRef(block_id=block.block_id, quote='Пересказ отсутствующего положения.'), 'before')


def test_unknown_block_wrong_version_and_empty_quote_are_rejected(analysis):
    block = analysis.documents[0].blocks[-1]
    validator = EvidenceValidator(analysis)
    assert not validator.validate_ref(EvidenceRef(block_id='foreign', quote=block.text))
    assert not validator.validate_ref(EvidenceRef(block_id=block.block_id, quote=block.text), 'after')
    assert not validator.validate_ref(EvidenceRef(block_id=block.block_id, quote=' \n '))


def test_punctuation_or_short_fragment_does_not_cover_whole_entity(analysis):
    block = analysis.documents[0].blocks[-1]
    validator = EvidenceValidator(analysis)
    full = EvidenceRef(block_id=block.block_id, quote=block.text)
    assert not validator.covers([EvidenceRef(block_id=block.block_id, quote='.')], [full])
    assert not validator.covers([EvidenceRef(block_id=block.block_id, quote='систем')], [full])
    assert validator.covers([full], [full])


def test_audit_role_cannot_be_invented_from_copying_quote(analysis):
    dep = department(analysis, 'before', 'Служба А', 'old')
    block = analysis.documents[0].blocks[-1]
    function = Function(id='function', department_id=dep.id, original_text=block.text,
                        normalized_description='Независимый аудит всех платежей компании', action='Аудит',
                        object='платежи', scope='все платежи', role='independent_audit',
                        source_refs=[EvidenceRef(block_id=block.block_id, quote=block.text)])
    assert not EvidenceValidator(analysis).validate_function(function, {dep.id: dep})


def test_sources_are_scoped_to_analysis(analysis):
    foreign = analysis.model_copy(deep=True)
    foreign.analysis_id = 'another-analysis'
    foreign.documents[0].id = 'foreign-document'
    foreign.documents[0].blocks[-1].block_id = 'foreign-block'
    foreign.documents[0].blocks[-1].document_id = 'foreign-document'
    foreign_ref = EvidenceRef(block_id='foreign-block', quote=foreign.documents[0].blocks[-1].text)
    assert EvidenceValidator(foreign).validate_ref(foreign_ref)
    assert not EvidenceValidator(analysis).validate_ref(foreign_ref)


def test_block_with_wrong_document_membership_is_rejected(analysis):
    block = analysis.documents[0].blocks[-1]
    block.document_id = 'foreign-document'
    assert not EvidenceValidator(analysis).validate_ref(EvidenceRef(block_id=block.block_id, quote=block.text))


def test_duplicate_block_ids_are_not_resolvable(analysis):
    first = analysis.documents[0].blocks[-1]
    analysis.documents[1].blocks[-1].block_id = first.block_id
    assert not EvidenceValidator(analysis).validate_ref(EvidenceRef(block_id=first.block_id, quote=first.text))


def department(analysis, version, name, identifier):
    block = next(block for doc in analysis.documents if doc.version == version for block in doc.blocks if block.text == name)
    return Department(id=identifier, name=name, version=version, source_refs=[EvidenceRef(block_id=block.block_id, quote=name)])


def test_split_and_merge_require_correct_cardinality_and_explicit_order():
    rows = [row('before', 'Центр А', 'Разработка'), row('after', 'Отдел Б', 'Разработка'), row('after', 'Отдел В', 'Копирование')]
    analysis = make_fixture(rows)
    old = department(analysis, 'before', 'Центр А', 'old')
    first = department(analysis, 'after', 'Отдел Б', 'new-1')
    second = department(analysis, 'after', 'Отдел В', 'new-2')
    order = analysis.documents[1].blocks[-1].model_copy(update={
        'block_id': 'order', 'text': 'Разделить Центр А на Отдел Б и Отдел В.'})
    analysis.documents[1].blocks.append(order)
    validator = EvidenceValidator(analysis)
    change = DepartmentChange(before_department_ids=['old'], after_department_ids=['new-1', 'new-2'],
                              change_type='split', evidence_status='direct', explanation=order.text,
                              source_refs=[EvidenceRef(block_id=order.block_id, quote=order.text)])
    registry = {item.id: item for item in [old, first, second]}
    assert validator.validate_department_change(change, registry)
    change.change_type = 'merged'
    assert not validator.validate_department_change(change, registry)
    change.change_type = 'split'
    change.source_refs = old.source_refs + first.source_refs + second.source_refs
    assert not validator.validate_department_change(change, registry)


def test_abolition_cannot_be_inferred_from_absence(analysis):
    old = department(analysis, 'before', 'Служба А', 'old')
    change = DepartmentChange(before_department_ids=['old'], after_department_ids=[], change_type='abolished',
                              evidence_status='semantic', explanation='Нет в новом списке.', source_refs=old.source_refs)
    assert not EvidenceValidator(analysis).validate_department_change(change, {'old': old})


@pytest.mark.parametrize('order_text', ['Не упразднять Службу А.', 'Запрещается упразднять Службу А.',
                                      'Упразднить Отдел В. Служба А сохраняется.',
                                      'Упразднить Отдел В, а Службу А сохранить.',
                                      'Упразднять Службу А запрещается.',
                                      'Служба А: упразднение не предусматривается.',
                                      'Упразднение Службы А отменяется.'])
def test_negative_or_unrelated_order_does_not_prove_abolition(analysis, order_text):
    old = department(analysis, 'before', 'Служба А', 'old')
    block = analysis.documents[1].blocks[-1].model_copy(update={'block_id': 'order', 'text': order_text})
    analysis.documents[1].blocks.append(block)
    change = DepartmentChange(before_department_ids=['old'], after_department_ids=[], change_type='abolished',
                              evidence_status='direct', explanation='Упразднение.',
                              source_refs=[*old.source_refs, EvidenceRef(block_id=block.block_id, quote=block.text)])
    assert not EvidenceValidator(analysis).validate_department_change(change, {'old': old})


def test_direct_order_supports_russian_case_endings():
    analysis = make_fixture([row('before', 'Бухгалтерия', 'Согласование'), row('after', 'Бухгалтерия', 'Согласование')])
    old = department(analysis, 'before', 'Бухгалтерия', 'old')
    new = department(analysis, 'after', 'Бухгалтерия', 'new')
    block = analysis.documents[1].blocks[-1].model_copy(update={'block_id': 'order', 'text': 'Сохранить Отдел закупок и Бухгалтерию.'})
    analysis.documents[1].blocks.append(block)
    change = DepartmentChange(before_department_ids=['old'], after_department_ids=['new'], change_type='preserved',
                              evidence_status='direct', explanation='Бухгалтерия сохранена приказом.',
                              source_refs=[*old.source_refs, *new.source_refs, EvidenceRef(block_id=block.block_id, quote=block.text)])
    assert EvidenceValidator(analysis).validate_department_change(change, {'old': old, 'new': new})


def test_two_sided_evidence_is_required_for_duplication():
    analysis = make_fixture([row('before', 'Служба А', 'Копирование'), row('after', 'Служба Б', 'Копирование'),
                             row('after', 'Служба В', 'Копирование')])
    departments = {name: department(analysis, 'after', name, name) for name in ['Служба Б', 'Служба В']}
    functions = {}
    for index, name in enumerate(departments):
        block = next(block for block in analysis.documents[1].blocks if block.context == name)
        functions[str(index)] = Function(id=str(index), department_id=name, original_text=block.text,
                                         normalized_description=block.text, action='Копирование', object='систем',
                                         scope='внутренние системы', role='execution',
                                         source_refs=[EvidenceRef(block_id=block.block_id, quote=block.text)])
    finding = Finding(id='risk', type='potential_duplication', title='Пересечение', affected_department_ids=list(departments),
                      before_function_ids=[], after_function_ids=list(functions), explanation='Одинаковая обязанность.',
                      evidence_refs=functions['0'].source_refs, recommendation='Уточнить ответственность.',
                      evidence_status='semantic', limitations=[])
    semantic = {'supported': True, 'explanation': 'Проверено', 'scope_relation': 'same', 'object_relation': 'same', 'action_relation': 'same'}
    validator = EvidenceValidator(analysis)
    assert not validator.validate_finding(finding, functions, departments, {}, search_complete=True, semantic=semantic)
    finding.evidence_refs += functions['1'].source_refs
    assert validator.validate_finding(finding, functions, departments, {}, search_complete=True, semantic=semantic)
    semantic['scope_relation'] = 'disjoint'
    assert not validator.validate_finding(finding, functions, departments, {}, search_complete=True, semantic=semantic)
