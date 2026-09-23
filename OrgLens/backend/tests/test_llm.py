from dataclasses import replace
from types import SimpleNamespace

import httpx
import httpx2
import openai
import pytest

from app.config import Settings
from app.schemas import Model
from app.services.llm import CallBudgetExceeded, LLMClient, LLMError, OpenAIProvider, SYSTEM_INSTRUCTIONS
from tests.fakes import FakeLLMProvider
from app.services.extraction import ExtractionOutput, ExtractionValidationOutput
from app.services.comparison import DepartmentComparisonOutput, FunctionComparisonOutput, AbsenceReviewOutput, RiskComparisonOutput, RiskValidationOutput


class Reply(Model):
    text: str


def test_cache_is_content_model_prompt_and_analysis_scoped(tmp_path):
    settings = Settings(api_key='', data_dir=tmp_path, model='test-model')
    fake = FakeLLMProvider(responses=[{'text': 'ответ'}] * 5)
    client = LLMClient(settings, 'one', fake)
    data = {'version': 'before', 'text': 'функция'}
    assert client.ask('stage', 'Инструкция', data, Reply).text == 'ответ'
    assert client.ask('stage', 'Инструкция', data, Reply).text == 'ответ'
    assert client.calls == 1
    client.ask('stage', 'Инструкция', {**data, 'version': 'after'}, Reply)
    assert client.calls == 2
    other_model = LLMClient(replace(settings, model='other'), 'one', fake)
    other_model.ask('stage', 'Инструкция', data, Reply)
    other_version = LLMClient(replace(settings, prompt_version='other'), 'one', fake)
    other_version.ask('stage', 'Инструкция', data, Reply)
    other_analysis = LLMClient(settings, 'two', fake)
    other_analysis.ask('stage', 'Инструкция', data, Reply)
    assert len(fake.calls) == 5
    assert not list(tmp_path.rglob('*.tmp'))


def test_only_temporary_errors_are_retried_and_count_toward_budget(tmp_path):
    fake = FakeLLMProvider(responses=[LLMError('Временная ошибка.', retryable=True), {'text': 'ответ'}])
    delays = []
    client = LLMClient(Settings(api_key='', data_dir=tmp_path), 'one', fake, sleep=delays.append)
    assert client.ask('stage', 'Инструкция', {}, Reply).text == 'ответ'
    assert client.calls == 2 and delays == [0.5]
    permanent = FakeLLMProvider(responses=[LLMError('Неверный ключ.'), {'text': 'нельзя'}])
    with pytest.raises(LLMError, match='Неверный ключ'):
        LLMClient(Settings(api_key='', data_dir=tmp_path), 'two', permanent).ask('stage', 'Инструкция', {}, Reply)
    assert len(permanent.calls) == 1


def test_budget_limits_physical_retry_attempts(tmp_path):
    fake = FakeLLMProvider(responses=[LLMError('Временная ошибка.', retryable=True)] * 3)
    client = LLMClient(Settings(api_key='', data_dir=tmp_path, max_calls=1), 'one', fake, sleep=lambda _: None)
    with pytest.raises(CallBudgetExceeded):
        client.ask('stage', 'Инструкция', {}, Reply)
    assert client.calls == len(fake.calls) == 1


def test_responses_api_uses_structured_output_no_tools_and_no_storage(tmp_path):
    provider = OpenAIProvider(Settings(api_key='test-key-that-is-not-real', data_dir=tmp_path))
    captured = {}

    def parse(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(status='completed', output=[], output_parsed=Reply(text='ответ'))

    provider.client = SimpleNamespace(responses=SimpleNamespace(parse=parse))
    answer = provider.generate('stage', 'Документ', Reply)
    assert answer.text == 'ответ'
    assert captured['text_format'] is Reply
    assert captured['store'] is False
    assert 'tools' not in captured
    assert captured['instructions'] == SYSTEM_INSTRUCTIONS
    assert 'test-key-that-is-not-real' not in repr(captured)


@pytest.mark.parametrize('response_model', [ExtractionOutput, ExtractionValidationOutput, DepartmentComparisonOutput,
                                           FunctionComparisonOutput, AbsenceReviewOutput, RiskComparisonOutput, RiskValidationOutput])
def test_real_sdk_serializes_strict_nested_schema_and_parses_mock_http_response(tmp_path, response_model):
    """Проверяет настоящий SDK через локальный HTTP transport; запросов в сеть нет."""
    import json
    captured = {}

    def transport(request):
        captured.update(json.loads(request.content))
        return httpx2.Response(200, json={
            'id': 'resp_test', 'object': 'response', 'created_at': 1, 'status': 'completed',
            'model': 'gpt-4.1-mini', 'output': [{'id': 'msg_test', 'type': 'message', 'role': 'assistant',
                'status': 'completed', 'content': [{'type': 'output_text', 'text': response_model().model_dump_json(),
                                                  'annotations': []}]}],
        })

    provider = OpenAIProvider(Settings(api_key='test-only-key', data_dir=tmp_path))
    provider.client = openai.OpenAI(api_key='test-only-key', max_retries=0,
                                    http_client=httpx2.Client(transport=httpx2.MockTransport(transport)))
    try:
        parsed = provider.generate('test', 'Проверка синтаксиса SDK без сети.', response_model)
    finally:
        provider.client.close()
    assert isinstance(parsed, response_model)
    output_format = captured['text']['format']
    assert output_format['type'] == 'json_schema'
    assert output_format['strict'] is True

    def assert_strict(schema):
        if isinstance(schema, dict):
            if schema.get('type') == 'object':
                assert schema.get('additionalProperties') is False
                assert set(schema.get('required', [])) == set(schema.get('properties', {}))
            for child in schema.values():
                assert_strict(child)
        if isinstance(schema, list):
            for child in schema:
                assert_strict(child)

    assert_strict(output_format['schema'])
    assert captured['store'] is False
    assert 'tools' not in captured


@pytest.mark.parametrize('mode, message', [('refusal', 'отказалась'), ('incomplete', 'неполный'), ('invalid', 'структурированный')])
def test_refusal_incomplete_and_missing_parsed_response(tmp_path, mode, message):
    provider = OpenAIProvider(Settings(api_key='test-key', data_dir=tmp_path))
    response = SimpleNamespace(status='incomplete' if mode == 'incomplete' else 'completed',
                               output=[SimpleNamespace(content=[SimpleNamespace(type='refusal')])] if mode == 'refusal' else [],
                               output_parsed=None)
    provider.client = SimpleNamespace(responses=SimpleNamespace(parse=lambda **_: response))
    with pytest.raises(LLMError, match=message):
        provider.generate('stage', 'Данные', Reply)


@pytest.mark.parametrize('status, code, expected, retryable', [
    (401, 'invalid_api_key', 'отклонил API-ключ', False),
    (403, 'permission_denied', 'Нет доступа', False),
    (404, 'model_not_found', 'недоступна', False),
    (429, 'insufficient_quota', 'Исчерпана квота', False),
    (429, 'rate_limit_exceeded', 'частоту запросов', True),
    (500, 'server_error', 'Временная ошибка', True),
])
def test_api_errors_are_classified_without_secret_leaks(tmp_path, status, code, expected, retryable):
    provider = OpenAIProvider(Settings(api_key='secret-never-return', data_dir=tmp_path))
    classes = {401: openai.AuthenticationError, 403: openai.PermissionDeniedError,
               404: openai.NotFoundError, 429: openai.RateLimitError, 500: openai.InternalServerError}
    response = httpx.Response(status, request=httpx.Request('POST', 'https://api.openai.com/v1/responses'))
    error = classes[status]('secret-never-return', response=response, body={'code': code})

    def parse(**_):
        raise error

    provider.client = SimpleNamespace(responses=SimpleNamespace(parse=parse))
    with pytest.raises(LLMError, match=expected) as caught:
        provider.generate('stage', 'Данные', Reply)
    assert caught.value.retryable is retryable
    assert 'secret-never-return' not in str(caught.value)
