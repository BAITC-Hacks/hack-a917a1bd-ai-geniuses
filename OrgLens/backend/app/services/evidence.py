"""Проверка источников и обязательных смысловых условий результатов."""

from __future__ import annotations

import re
from typing import Iterable

from app.schemas import Analysis, Department, DepartmentChange, EvidenceRef, Finding, Function, FunctionMapping


def normalized(text: str) -> str:
    return ' '.join(text.split())


def unique_refs(refs: Iterable[EvidenceRef]) -> list[EvidenceRef]:
    result: list[EvidenceRef] = []
    seen: set[tuple[str, str]] = set()
    for ref in refs:
        key = (ref.block_id, normalized(ref.quote))
        if key not in seen:
            seen.add(key)
            result.append(ref)
    return result


def words(text: str) -> set[str]:
    # A conservative lexical guard; semantic equivalence is assessed by the model separately.
    stop = {'для', 'при', 'всех', 'всем', 'своих', 'этого', 'того', 'после', 'отдел', 'функция'}
    return {word[:6] for word in re.findall(r'[а-яёa-z0-9]+', text.lower()) if len(word) > 2 and word not in stop}


def may_overlap(left: str, right: str) -> bool:
    """Reject explicitly disjoint scopes; unknown is not positive evidence."""
    left_words, right_words = words(left), words(right)
    return bool(left_words and right_words and left_words & right_words)


def affirmative_signal(quote: str, signal: str) -> bool:
    """An explicit event must be affirmative; a ban on abolition is not abolition."""
    for match in re.finditer(signal, quote, flags=re.IGNORECASE):
        preceding = quote[max(0, match.start() - 60):match.start()].lower()
        preceding = re.split(r'[.;!?]', preceding)[-1]
        following = re.split(r'[.;!?]', quote[match.end():match.end() + 100].lower())[0]
        if re.search(r'\bне\b|запрещ|недопуст|воздерж|отказ', preceding):
            continue
        if re.search(r'\bне\b|запрещ|отменя|приостанов', following):
            continue
        return True
    return False


def explicit_department_event(quote: str, signal: str, names: list[str]) -> bool:
    for sentence in re.split(r'[.!?;\n]|,\s*(?:а|но|однако)\b', quote, flags=re.IGNORECASE):
        if affirmative_signal(sentence, signal) and all(name_mentioned(name, sentence) for name in names):
            return True
    return False


def name_mentioned(name: str, text: str) -> bool:
    """Permit ordinary Russian case endings in a name while retaining every name token."""
    endings = ('иями', 'ями', 'ами', 'ого', 'его', 'ому', 'ему', 'ией', 'иях', 'иям', 'ыми', 'ими',
               'ия', 'ию', 'ии', 'ых', 'их', 'ах', 'ях', 'ов', 'ев', 'ую', 'юю', 'ая', 'яя', 'ое', 'ее',
               'ий', 'ый', 'ой', 'ам', 'ям', 'ы', 'и', 'а', 'я', 'у', 'ю', 'е')

    def tokens(value):
        output = []
        for word in re.findall(r'[а-яёa-z0-9]+', value.casefold()):
            root = word
            for ending in endings:
                if len(word) - len(ending) >= 4 and word.endswith(ending):
                    root = word[:-len(ending)]
                    break
            output.append(root)
        return output

    target, source = tokens(name), tokens(text)
    return bool(target) and any(source[index:index + len(target)] == target for index in range(len(source) - len(target) + 1))


class EvidenceValidator:
    def __init__(self, analysis: Analysis):
        self.analysis = analysis
        self.blocks = {}
        self.documents = {doc.id: doc for doc in analysis.documents}
        duplicates: set[str] = set()
        for doc in analysis.documents:
            for block in doc.blocks:
                if block.document_id != doc.id or block.version != doc.version:
                    continue
                if block.block_id in self.blocks:
                    duplicates.add(block.block_id)
                self.blocks[block.block_id] = block
        for block_id in duplicates:
            self.blocks.pop(block_id, None)

    def validate_ref(self, ref: EvidenceRef, version: str | None = None) -> bool:
        block = self.blocks.get(ref.block_id)
        quote = normalized(ref.quote)
        return bool(block and quote and (version is None or block.version == version)
                    and quote in normalized(block.text))

    def validate_refs(self, refs: list[EvidenceRef], version: str | None = None) -> bool:
        return bool(refs) and all(self.validate_ref(ref, version) for ref in refs)

    def covers(self, refs: list[EvidenceRef], entity_refs: list[EvidenceRef]) -> bool:
        return any(ref.block_id == original.block_id
                   and normalized(original.quote) in normalized(ref.quote)
                   for ref in refs for original in entity_refs)

    def validate_department(self, department: Department, version: str) -> bool:
        return (department.version == version and bool(department.name.strip())
                and self.validate_refs(department.source_refs, version)
                and any(normalized(department.name).casefold() in normalized(ref.quote).casefold()
                        for ref in department.source_refs))

    def validate_function(self, function: Function, departments: dict[str, Department]) -> bool:
        department = departments.get(function.department_id)
        if function.role == 'independent_audit' and not any(
                re.search(r'аудит|независим', ref.quote, re.IGNORECASE) for ref in function.source_refs):
            return False
        return bool(department and function.original_text.strip() and function.normalized_description.strip()
                    and self.validate_refs(function.source_refs, department.version)
                    and any(normalized(function.original_text) in normalized(ref.quote) for ref in function.source_refs))

    def validate_department_change(self, change: DepartmentChange,
                                   departments: dict[str, Department]) -> bool:
        before = [departments.get(identifier) for identifier in change.before_department_ids]
        after = [departments.get(identifier) for identifier in change.after_department_ids]
        if not self.validate_refs(change.source_refs) or not (before or after):
            return False
        if any(department is None or department.version != 'before' for department in before):
            return False
        if any(department is None or department.version != 'after' for department in after):
            return False
        if len({*change.before_department_ids}) != len(before) or len({*change.after_department_ids}) != len(after):
            return False
        if change.change_type == 'split' and not (len(before) == 1 and len(after) >= 2):
            return False
        if change.change_type == 'merged' and not (len(before) >= 2 and len(after) == 1):
            return False
        if change.change_type == 'created' and (before or len(after) != 1):
            return False
        if change.change_type == 'abolished' and (not before or after):
            return False
        if change.change_type in {'preserved', 'renamed', 'transformed'} and not (len(before) == len(after) == 1):
            return False
        for department in [*before, *after]:
            if not self.covers(change.source_refs, department.source_refs):
                # Explicit orders can mention names in a different source block.
                if not any(name_mentioned(department.name, ref.quote)
                           for ref in change.source_refs):
                    return False
        if change.change_type == 'abolished' and change.evidence_status != 'direct':
            return False
        if change.evidence_status == 'direct' and change.change_type != 'uncertain':
            signals = {
                'preserved': r'сохран|продолжа|без изменен', 'renamed': r'переимен',
                'transformed': r'преобраз|реорганиз', 'split': r'раздел|выдел',
                'merged': r'объедин|слиян', 'created': r'созда|образова',
                'abolished': r'упраздн|ликвидир',
            }
            if not any(self.blocks[ref.block_id].version == 'after'
                       and explicit_department_event(ref.quote, signals[change.change_type],
                                                     [department.name for department in [*before, *after]])
                       for ref in change.source_refs):
                return False
        return True

    def validate_mapping(self, mapping: FunctionMapping, functions: dict[str, Function],
                         departments: dict[str, Department], *, search_complete: bool) -> bool:
        before = functions.get(mapping.before_function_id)
        if not before or departments[before.department_id].version != 'before':
            return False
        if not self.validate_refs(mapping.source_refs) or not self.covers(mapping.source_refs, before.source_refs):
            return False
        after = [functions.get(identifier) for identifier in mapping.after_function_ids]
        if any(item is None or departments[item.department_id].version != 'after' for item in after):
            return False
        if any(not self.covers(mapping.source_refs, item.source_refs) for item in after):
            return False
        if mapping.relation in {'preserved', 'transferred', 'partially_covered'}:
            if not after or any(item.role != before.role or item.role == 'unknown' for item in after):
                return False
            if mapping.relation == 'partially_covered' and not mapping.uncovered_aspects:
                return False
            if mapping.relation in {'preserved', 'transferred'} and mapping.uncovered_aspects:
                return False
        if mapping.relation == 'not_found':
            after_doc_ids = {doc.id for doc in self.analysis.documents if doc.version == 'after'}
            if after or not search_complete or set(mapping.searched_document_ids) != after_doc_ids:
                return False
        if mapping.relation == 'explicitly_discontinued':
            if after or not any(self.blocks[ref.block_id].version == 'after'
                                and affirmative_signal(ref.quote, r'отмен|прекрати|исключ')
                                and may_overlap(before.object, ref.quote)
                                for ref in mapping.source_refs):
                return False
        return True

    def validate_finding(self, finding: Finding, functions: dict[str, Function],
                         departments: dict[str, Department], mappings: dict[str, FunctionMapping],
                         *, search_complete: bool, semantic: dict | None = None) -> bool:
        if not self.validate_refs(finding.evidence_refs):
            return False
        before = [functions.get(identifier) for identifier in finding.before_function_ids]
        after = [functions.get(identifier) for identifier in finding.after_function_ids]
        if any(item is None or departments[item.department_id].version != 'before' for item in before):
            return False
        if any(item is None or departments[item.department_id].version != 'after' for item in after):
            return False
        if any(not self.covers(finding.evidence_refs, item.source_refs) for item in [*before, *after]):
            return False
        participant_ids = {item.department_id for item in [*before, *after]}
        if not participant_ids or set(finding.affected_department_ids) != participant_ids:
            return False
        if finding.type == 'potential_loss':
            if not before or not search_complete or finding.evidence_status == 'insufficient':
                return False
            return all(item.id in mappings and mappings[item.id].relation in {'not_found', 'partially_covered'}
                       for item in before)
        if finding.type == 'insufficient_data':
            return finding.evidence_status == 'insufficient'
        if not semantic or not semantic.get('supported') or not semantic.get('explanation', '').strip():
            return False
        if semantic.get('scope_relation') not in {'same', 'overlap'}:
            return False
        if semantic.get('object_relation') not in {'same', 'overlap'}:
            return False
        if len(after) < 2 or len({item.id for item in after}) != len(after):
            return False
        if finding.type in {'potential_duplication', 'partial_overlap'}:
            if len({item.department_id for item in after}) < 2:
                return False
            if len({item.role for item in after}) != 1 or after[0].role == 'unknown':
                return False
            if semantic.get('action_relation') != 'same':
                return False
            if finding.type == 'potential_duplication' and (
                    semantic.get('scope_relation') != 'same' or semantic.get('object_relation') != 'same'):
                return False
            # A cited action must actually occur in both descriptions; unsupported similarity is rejected.
            if not all(may_overlap(after[0].action, item.action) or
                       may_overlap(after[0].normalized_description, item.normalized_description)
                       for item in after[1:]):
                return False
        if finding.type == 'potential_conflict':
            if len({item.department_id for item in after}) != 1:
                return False
            if not ({'execution', 'independent_audit'} <= {item.role for item in after}):
                return False
            if not semantic.get('independence_required'):
                return False
            if not any(re.search(r'независим|собствен|разработанн.*(?:отдел|подраздел)', ref.quote, re.IGNORECASE)
                       for item in after if item.role == 'independent_audit' for ref in item.source_refs):
                return False
        return True
