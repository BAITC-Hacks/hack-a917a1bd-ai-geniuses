"""Сопоставление по всем подразделениям и проверка организационных рисков."""

from __future__ import annotations

from itertools import combinations_with_replacement
from typing import Callable, Literal

from pydantic import Field

from app.schemas import Analysis, DepartmentChange, EvidenceRef, Finding, Function, FunctionMapping, Model, SourceBlock
from app.services.evidence import EvidenceValidator, unique_refs
from app.services.extraction import extraction_batches, stable_id
from app.services.llm import LLMClient


class DepartmentComparisonOutput(Model):
    changes: list[DepartmentChange] = Field(default_factory=list)


class MappingCandidate(Model):
    mapping: FunctionMapping
    action_compatible: bool
    role_compatible: bool
    object_relation: Literal['same', 'overlap', 'different', 'unknown']
    scope_relation: Literal['full', 'partial', 'none', 'unknown']
    semantic_explanation: str


class FunctionComparisonOutput(Model):
    candidates: list[MappingCandidate] = Field(default_factory=list)


class AbsenceReviewItem(Model):
    before_function_id: str
    outcome: Literal['no_assignment', 'assignment_found', 'explicitly_discontinued', 'old_rules_continue', 'unclear']
    explanation: str
    source_refs: list[EvidenceRef]
    nearest_after_function_ids: list[str]


class AbsenceReviewOutput(Model):
    reviews: list[AbsenceReviewItem] = Field(default_factory=list)


class RiskComparisonOutput(Model):
    findings: list[Finding] = Field(default_factory=list)


class SemanticRiskReview(Model):
    finding_id: str
    supported: bool
    action_relation: Literal['same', 'different', 'unknown']
    object_relation: Literal['same', 'overlap', 'disjoint', 'unknown']
    scope_relation: Literal['same', 'overlap', 'disjoint', 'unknown']
    independence_required: bool
    existed_before: Literal['yes', 'no', 'unknown']
    explanation: str


class RiskValidationOutput(Model):
    reviews: list[SemanticRiskReview] = Field(default_factory=list)


def groups(items: list, size: int) -> list[list]:
    return [items[index:index + max(1, size)] for index in range(0, len(items), max(1, size))]


DEPARTMENT_INSTRUCTION = """Сопоставь подразделения ДО и ПОСЛЕ. Сначала ищи прямые приказы:
переименовать, разделить, объединить, создать, упразднить, сохранить или преобразовать.
Поддерживай split (1 ко многим) и merged (многие к 1). Источник прямого изменения — документ ПОСЛЕ
с соответствующим распоряжением, а не одно совпадение названий. Приложи источники обоих названий.
Отсутствие подразделения в наборе ПОСЛЕ не доказывает упразднение: abolished допускается
только при прямом указании. Сходство функций и названий — semantic, гипотеза.
Одинаковые названия могут означать preserved с semantic, явное сохранение — direct.
Не придумывай сущности и ID. Каждая существенная часть объяснения поддерживается source_refs.
Входные source_blocks содержат реальный текст, из которого можно брать точные цитаты."""


MAPPING_INSTRUCTION = """Сопоставь каждую функцию before_functions со ВСЕМИ after_functions этого
пакета независимо от названия подразделения. Это часть общего поиска, не полный комплект.
Дай только смысловые кандидаты с реальными соответствиями; отсутствие кандидата в пакете
не означает потерю. relation not_found и explicitly_discontinued здесь запрещены.
Одна функция ДО может покрываться несколькими ПОСЛЕ. Различай сохранение и передачу в иной отдел.
Учитывай действие, объект, scope и роль. Исполнение не эквивалентно согласованию или контролю.
Если покрыта лишь часть, relation=partially_covered, назови covered_aspects и uncovered_aspects.
Для полного покрытия scope_relation=full; не стирай существенные ограничения scope.
При разной роли или неизвестной области не заявляй полное совпадение. Подкрепи кандидата
цитатами исходной функции ДО и КАЖДОЙ функции ПОСЛЕ, используя их source_refs.
action_compatible, role_compatible, object_relation, scope_relation и semantic_explanation
должны объяснять реальное сопоставление. Не переписывай ID и не выдумывай новые функции."""


ABSENCE_INSTRUCTION = """Выполни дополнительную проверку ИСХОДНЫХ блоков ПОСЛЕ, а не только списка
извлечённых функций. Для каждого before_function_id проверь, закреплена ли искомая обязанность
(или её uncovered_aspects, если функция частично покрыта) в этом пакете за ЛЮБЫМ подразделением.
Пакеты вместе покроют весь доступный комплект ПОСЛЕ; один пакет не доказывает отсутствие.
Проверь прямую отмену функции, передачу, а также прямое сохранение действия старого положения.
Не считай положения ДО автоматически отменёнными; если их действие неясно, поясни это.
Верни ровно один review для КАЖДОЙ before_functions. outcome=no_assignment означает только,
что в этом пакете нет закрепления; source_refs может быть пустым: не сочиняй цитату отсутствия.
Для assignment_found, old_rules_continue и explicitly_discontinued приложи точную цитату
ПОСЛЕ, подтверждающую именно данную обязанность или действие соответствующего положения.
Не считай отмену всего старого положения доказательством отмены конкретной функции, если
параллельно функция передана или закреплена вновь. При неоднозначности outcome=unclear.
nearest_after_function_ids должны ссылаться только на реально переданные функции ПОСЛЕ."""


RISK_INSTRUCTION = """Сравни все пары функций ПОСЛЕ внутри after_functions, включая функции одного
отдела для проверки независимости. Выяви potential_duplication, partial_overlap, potential_conflict.
Дублирование требует РАЗНЫХ подразделений с одинаковым действием, объектом, scope и ролью.
Разные роли (подготовка и согласование заявки), разные этапы процесса и разные области
НЕ дублирование. Повтор положения об одном подразделении в двух файлах НЕ дублирование.
Partial_overlap: одинаковая обязанность разных подразделений пересекается лишь частью области.
Potential_conflict: ОДНО подразделение исполняет работу и независимо проверяет СВОЮ работу
в той же области. Независимый аудит другого объекта или работа двух разных отделов не конфликт.
Обязательно цитируй обе стороны каждой пары, ссылки только из переданных функций.
affected_department_ids должны точно соответствовать участникам. Не объявляй нарушение закона:
это организационный риск, который следует проверить. Не приписывай личные мотивы.
При возможности сопоставь с before_functions, existed_before=yes/no/unknown; если доказательств
недостаточно, unknown. Для yes добавь соответствующие before_function_ids и их источники.
Выводы evidence_status=semantic, без числовой вероятности. Укажи краткую предметную рекомендацию
и ограничения. Все тексты на русском. Не создавай findings других типов."""


RISK_VALIDATION_INSTRUCTION = """Независимо проверь каждый предложенный риск по функциям и цитатам.
Верни review для каждого finding_id. Проверка существования цитаты уже выполняется backend;
твоя задача — проверить СМЫСЛ: поддерживают ли источники конкретное действие, объект, область,
роль, дублирование либо независимый аудит собственной работы. supported=false при домысле.
Одинаковые слова ещё не доказывают совпадение области. Не подтверждай дублирование подготовки
и согласования, разные территории, системы, сотрудников, этапы. Не подтверждай конфликт
между разными подразделениями или обычный контроль без признака независимого аудита.
independence_required=true только если источник прямо говорит о независимом аудите/проверке
своей работы. scope_relation и object_relation должны объяснять совпадение или пересечение.
Для дублирования требуется action_relation=same. existed_before=yes только при проверяемых
источниках обеих функций ДО; no только при прямом основании новизны, иначе unknown.
Не добавляй новые функции или факты. Приведи краткое объяснение проверки на русском."""


def compare_departments(analysis: Analysis, client: LLMClient, validator: EvidenceValidator,
                        blocks: list[SourceBlock]) -> int:
    if not analysis.result.departments:
        return 0
    response = client.ask('departments', DEPARTMENT_INSTRUCTION, {
        'departments': [item.model_dump() for item in analysis.result.departments],
        'functions': [{key: value for key, value in item.model_dump().items()
                       if key in {'id', 'department_id', 'normalized_description', 'scope', 'role'}}
                      for item in analysis.result.functions],
        'source_blocks': [block.model_dump() for block in blocks],
    }, DepartmentComparisonOutput)
    departments = {item.id: item for item in analysis.result.departments}
    rejected = 0
    seen = set()
    for change in response.changes:
        key = (tuple(sorted(change.before_department_ids)), tuple(sorted(change.after_department_ids)), change.change_type)
        if key in seen:
            continue
        seen.add(key)
        if validator.validate_department_change(change, departments):
            analysis.result.department_changes.append(change)
        else:
            rejected += 1
    return rejected


def accept_candidate(candidate: MappingCandidate, validator: EvidenceValidator,
                     functions: dict, departments: dict) -> bool:
    mapping = candidate.mapping
    if mapping.relation not in {'preserved', 'transferred', 'partially_covered'}:
        return False
    if not (candidate.action_compatible and candidate.role_compatible and candidate.semantic_explanation.strip()):
        return False
    if candidate.object_relation not in {'same', 'overlap'} or candidate.scope_relation not in {'full', 'partial'}:
        return False
    if mapping.relation != 'partially_covered' and (candidate.scope_relation != 'full' or candidate.object_relation != 'same'):
        return False
    return validator.validate_mapping(mapping, functions, departments, search_complete=False)


def compare_functions(analysis: Analysis, client: LLMClient, validator: EvidenceValidator,
                      after_blocks: list[SourceBlock], batch_size: int, batch_chars: int,
                      save_progress: Callable[[str], None]) -> int:
    departments = {item.id: item for item in analysis.result.departments}
    functions = {item.id: item for item in analysis.result.functions}
    before = [item for item in functions.values() if departments[item.department_id].version == 'before']
    after = [item for item in functions.values() if departments[item.department_id].version == 'after']
    after_ids = {item.id for item in after}
    candidates: dict[str, list[MappingCandidate]] = {item.id: [] for item in before}
    rejected = 0
    before_groups, after_groups = groups(before, batch_size), groups(after, batch_size)
    total_pairs = len(before_groups) * len(after_groups)
    for index, (old_group, new_group) in enumerate((old, new) for old in before_groups for new in after_groups):
        save_progress(f'Сопоставление функций: пакет {index + 1} из {total_pairs}')
        response = client.ask('functions', MAPPING_INSTRUCTION, {
            'before_functions': [item.model_dump() for item in old_group],
            'after_functions': [item.model_dump() for item in new_group],
            'departments': [item.model_dump() for item in departments.values()
                            if item.id in {fn.department_id for fn in [*old_group, *new_group]}],
        }, FunctionComparisonOutput)
        old_ids, new_ids = {item.id for item in old_group}, {item.id for item in new_group}
        for candidate in response.candidates:
            if (candidate.mapping.before_function_id in old_ids
                    and set(candidate.mapping.after_function_ids) <= new_ids
                    and accept_candidate(candidate, validator, functions, departments)):
                candidates[candidate.mapping.before_function_id].append(candidate)
            else:
                rejected += 1
        # A successful batch can be reviewed even if a later request exhausts the budget.
        for old in old_group:
            options = candidates[old.id]
            if len(options) == 1:
                pending = options[0].mapping.model_copy(deep=True)
                pending.limitations.append('Сопоставление ещё проверяется по остальным пакетам ПОСЛЕ.')
                analysis.result.function_mappings = [item for item in analysis.result.function_mappings
                                                    if item.before_function_id != old.id] + [pending]
        save_progress(f'Сопоставление функций: проверено {index + 1} из {total_pairs} пакетов')
    final_mappings: dict[str, FunctionMapping] = {}
    for old in before:
        options = candidates[old.id]
        if len(options) == 1:
            final_mappings[old.id] = options[0].mapping
        elif len(options) > 1:
            response = client.ask('mapping_union', MAPPING_INSTRUCTION + '''
Объедини проверенные кандидаты для ОДНОЙ функции ДО. Выясни, покрывают ли несколько частичных
функций ПОСЛЕ обязанность полностью совместно. Верни ровно один candidate с объединёнными
after_function_ids и источниками всех выбранных функций; не выдавай частичное покрытие за полное.''', {
                'before_functions': [old.model_dump()],
                'after_functions': [item.model_dump() for item in after
                                    if item.id in {identifier for option in options for identifier in option.mapping.after_function_ids}],
                'candidates': [option.model_dump() for option in options],
            }, FunctionComparisonOutput)
            valid = [candidate for candidate in response.candidates
                     if candidate.mapping.before_function_id == old.id
                     and set(candidate.mapping.after_function_ids) <= after_ids
                     and accept_candidate(candidate, validator, functions, departments)]
            if len(valid) == 1:
                final_mappings[old.id] = valid[0].mapping
            else:
                rejected += 1
        if old.id not in final_mappings:
            final_mappings[old.id] = FunctionMapping(
                before_function_id=old.id, after_function_ids=[], relation='uncertain',
                covered_aspects=[], uncovered_aspects=[old.normalized_description],
                explanation='Подтверждённое соответствие пока не найдено; требуется полный обзор источников ПОСЛЕ.',
                source_refs=old.source_refs,
            )
        final_mappings[old.id].searched_document_ids = [doc.id for doc in analysis.documents if doc.version == 'after']
    analysis.result.function_mappings = list(final_mappings.values())
    save_progress('Дополнительная проверка полного комплекта ПОСЛЕ')
    to_review = [old for old in before if final_mappings[old.id].relation in {'uncertain', 'partially_covered'}]
    for old_group in groups(to_review, batch_size):
        reviews: dict[str, list[AbsenceReviewItem]] = {old.id: [] for old in old_group}
        raw_batches = extraction_batches(after_blocks, batch_chars)
        complete_review = bool(raw_batches)
        for block_group in raw_batches:
            response = client.ask('absence_review', ABSENCE_INSTRUCTION, {
                'before_functions': [item.model_dump() for item in old_group],
                'mappings': [final_mappings[item.id].model_dump() for item in old_group],
                'source_blocks_after': [block.model_dump() for block in block_group],
                'after_functions': [item.model_dump() for item in after],
            }, AbsenceReviewOutput)
            in_batch_ids = {block.block_id for block in block_group}
            seen = set()
            for review in response.reviews:
                if review.before_function_id not in reviews or review.before_function_id in seen:
                    complete_review = False
                    rejected += 1
                    continue
                seen.add(review.before_function_id)
                valid_refs = all(validator.validate_ref(ref, 'after') and ref.block_id in in_batch_ids
                                 for ref in review.source_refs)
                if review.outcome != 'no_assignment' and not review.source_refs:
                    valid_refs = False
                if not valid_refs or not set(review.nearest_after_function_ids) <= after_ids:
                    complete_review = False
                    rejected += 1
                    continue
                reviews[review.before_function_id].append(review)
            if seen != set(reviews):
                complete_review = False
        for old in old_group:
            mapping = final_mappings[old.id]
            items = reviews[old.id]
            mapping.nearest_after_function_ids = sorted({identifier for item in items for identifier in item.nearest_after_function_ids})
            outcomes = {item.outcome for item in items}
            mapping.limitations = ['Проверены только загруженные документы. Фактическое выполнение работы не устанавливалось.',
                                   'Если действие ранее утверждённых положений не определено прямо, его необходимо уточнить.']
            if not complete_review or len(items) != len(raw_batches) or not analysis.complete:
                mapping.relation = 'uncertain'
                mapping.explanation = 'Недостаточно данных для вывода об отсутствии: комплект или проверка обработаны не полностью.'
                mapping.limitations.append('Неполная обработка запрещает уверенный вывод о потере функции.')
            elif outcomes == {'no_assignment'}:
                if mapping.relation != 'partially_covered':
                    mapping.relation = 'not_found'
                    mapping.explanation = 'В проанализированном комплекте документов ПОСЛЕ не найдено подтверждённое закрепление функции.'
            elif 'explicitly_discontinued' in outcomes and outcomes <= {'explicitly_discontinued', 'no_assignment'}:
                mapping.relation = 'explicitly_discontinued'
                mapping.after_function_ids = []
                mapping.source_refs = unique_refs([*old.source_refs, *(ref for item in items for ref in item.source_refs)])
                mapping.explanation = 'В документе ПОСЛЕ прямо указано прекращение функции. Это явная отмена, требующая проверки контекста.'
            else:
                mapping.relation = 'uncertain'
                mapping.source_refs = unique_refs([*mapping.source_refs, *(ref for item in items for ref in item.source_refs)])
                mapping.explanation = 'При полном обзоре найдены указания на закрепление функции, действие старого положения или неоднозначность: ' + ' '.join(
                    item.explanation for item in items if item.outcome != 'no_assignment')
            if not validator.validate_mapping(mapping, functions, departments, search_complete=analysis.complete and complete_review):
                mapping.relation = 'uncertain'
                mapping.source_refs = unique_refs([*old.source_refs, *(ref for ref in mapping.source_refs if validator.validate_ref(ref))])
                mapping.limitations.append('Окончательное сопоставление не прошло проверку доказательств.')
                rejected += 1
        analysis.result.function_mappings = list(final_mappings.values())
        save_progress('Проверен полный комплект ПОСЛЕ для очередной группы функций')
    return rejected


def add_loss_findings(analysis: Analysis, validator: EvidenceValidator) -> None:
    departments = {item.id: item for item in analysis.result.departments}
    functions = {item.id: item for item in analysis.result.functions}
    mappings = {item.before_function_id: item for item in analysis.result.function_mappings}
    for mapping in mappings.values():
        old = functions[mapping.before_function_id]
        if mapping.relation not in {'not_found', 'partially_covered', 'uncertain'}:
            continue
        insufficient = mapping.relation == 'uncertain' or not analysis.complete
        after = [functions[identifier] for identifier in mapping.after_function_ids]
        finding = Finding(
            id=stable_id('finding', mapping.before_function_id, 'coverage'),
            type='insufficient_data' if insufficient else 'potential_loss',
            title=('Требуется уточнить закрепление функции: ' if insufficient else
                   'Не найдено полное закрепление функции: ') + old.normalized_description,
            affected_department_ids=sorted({old.department_id, *(item.department_id for item in after)}),
            before_function_ids=[old.id], after_function_ids=mapping.after_function_ids,
            explanation=mapping.explanation,
            evidence_refs=mapping.source_refs,
            recommendation='Проверить сохранение функции и действующие положения; уточнить ответственное подразделение.'
                           if not insufficient else 'Дополнить или проверить документы и повторить сопоставление функции.',
            evidence_status='insufficient' if insufficient else 'semantic',
            limitations=[*mapping.limitations,
                         'Проверенные документы ПОСЛЕ: ' + ', '.join(doc.original_name for doc in analysis.documents
                                                                   if doc.id in mapping.searched_document_ids)],
        )
        if validator.validate_finding(finding, functions, departments, mappings, search_complete=analysis.complete):
            analysis.result.findings.append(finding)


def compare_risks(analysis: Analysis, client: LLMClient, validator: EvidenceValidator,
                  batch_size: int, save_progress: Callable[[str], None]) -> int:
    departments = {item.id: item for item in analysis.result.departments}
    functions = {item.id: item for item in analysis.result.functions}
    before = [item for item in functions.values() if departments[item.department_id].version == 'before']
    after = [item for item in functions.values() if departments[item.department_id].version == 'after']
    mappings = {item.before_function_id: item for item in analysis.result.function_mappings}
    batches = groups(after, batch_size)
    rejected = 0
    seen = set()
    for pair_index, (left_index, right_index) in enumerate(combinations_with_replacement(range(len(batches)), 2)):
        candidates = batches[left_index] if left_index == right_index else batches[left_index] + batches[right_index]
        if len(candidates) < 2:
            continue
        save_progress(f'Проверка пересечений и независимости: пакет {pair_index + 1}')
        response = client.ask('risks', RISK_INSTRUCTION, {
            'after_functions': [item.model_dump() for item in candidates],
            'before_functions': [item.model_dump() for item in before],
            'departments': [item.model_dump() for item in departments.values()],
            'function_mappings': [item.model_dump() for item in mappings.values()],
        }, RiskComparisonOutput)
        candidate_ids = {item.id for item in candidates}
        plausible = []
        for finding in response.findings:
            if (finding.type not in {'potential_duplication', 'partial_overlap', 'potential_conflict'}
                    or not set(finding.after_function_ids) <= candidate_ids
                    or not validator.validate_refs(finding.evidence_refs)):
                rejected += 1
                continue
            finding.id = stable_id('finding', finding.type, *sorted(finding.after_function_ids))
            if finding.id not in seen:
                plausible.append(finding)
                seen.add(finding.id)
        if not plausible:
            continue
        review_response = client.ask('risk_validation', RISK_VALIDATION_INSTRUCTION, {
            'findings': [item.model_dump() for item in plausible],
            'functions': [item.model_dump() for item in [*before, *candidates]],
        }, RiskValidationOutput)
        reviews = {review.finding_id: review for review in review_response.reviews}
        for finding in plausible:
            review = reviews.get(finding.id)
            finding.evidence_status = 'semantic'
            if review and validator.validate_finding(finding, functions, departments, mappings,
                                                     search_complete=analysis.complete, semantic=review.model_dump()):
                # A claim that a risk predated reorganisation requires two documented BEFORE functions.
                finding.existed_before = 'yes' if review.existed_before == 'yes' and len(finding.before_function_ids) >= 2 else 'unknown'
                if finding.type == 'potential_conflict':
                    finding.limitations = list(dict.fromkeys([*finding.limitations,
                        'Показан организационный риск независимости для проверки; нарушение закона не устанавливалось.']))
                analysis.result.findings.append(finding)
            else:
                rejected += 1
        save_progress('Доказательства очередной группы рисков проверены')
    return rejected
