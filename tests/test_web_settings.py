import json
import os
import stat
import subprocess
import sys
import threading

import pytest

from cslurm.config import config_path, load_config
from cslurm.web import settings


@pytest.fixture
def configured(isolated_home):
    config_path().write_text('# preserve this\n[ai]\nformat = "openai"\nmodel = "original"\n'
                             'base_url = "https://example.invalid/v1"\napi_key = "private-key"\n'
                             'extra_body_json = \'{"private":"body-secret"}\'\n'
                             '[tracking]\nstatic_analysis = "false"\n# unrelated\n'
                             '[notification.feishu]\nwebhook_url = "https://example.invalid/private-hook"\n'
                             'secret = "private-sign"\n')
    return config_path()


def edit(kind="ai", profile_id="current", **kw):
    return settings.save(dict(kind=kind, id=profile_id, revision=settings.snapshot()["revision"], **kw))


def test_redaction_and_environment_names(configured, monkeypatch):
    monkeypatch.setenv("CSLURM_AI_API_KEY", "environment-secret")
    result = settings.snapshot()
    serialized = json.dumps(result)
    for secret in ["private-key", "private-hook", "private-sign", "body-secret", "environment-secret"]:
        assert secret not in serialized
    assert result["groups"]["ai"][0]["secrets"]["api_key"] is True
    assert "CSLURM_AI_API_KEY" in result["environment_overrides"]


def test_save_preserves_other_sections_secrets_and_backup(configured):
    before = configured.read_bytes()
    edit(values={"model": "changed", "api_key": None, "extra_body_json": None})
    config = load_config()
    assert config["ai"]["model"] == "changed"
    assert config["ai"]["api_key"] == "private-key"
    assert config["notification.feishu"]["secret"] == "private-sign"
    assert '[tracking]\nstatic_analysis = "false"\n# unrelated\n' in configured.read_text()
    backups = list((configured.parent / "config-backups").glob("*.toml"))
    assert len(backups) == 1 and backups[0].read_bytes() == before
    assert stat.S_IMODE(configured.stat().st_mode) == 0o600
    assert stat.S_IMODE(backups[0].stat().st_mode) == 0o600
    assert not list(configured.parent.glob(".config-*"))


def test_literal_credentials_in_legacy_env_fields_are_private(configured):
    configured.write_text('[notification.feishu]\nwebhook_url_env = "https://example.invalid/private-hook"\nsecret_env = "privateSigningSecret"\n')
    result = settings.snapshot()
    assert 'private-hook' not in json.dumps(result)
    assert 'privateSigningSecret' not in json.dumps(result)
    assert result['groups']['feishu'][0]['secrets'] == {'webhook_url': True, 'secret': True}
    edit(kind='feishu', values={'message_format': 'text', 'webhook_url': None, 'secret': None})
    config = load_config()['notification.feishu']
    assert config['webhook_url'] == 'https://example.invalid/private-hook'
    assert config['secret'] == 'privateSigningSecret'
    assert 'secret_env' not in config and 'webhook_url_env' not in config


def test_add_update_activate_and_clear_secret(configured):
    saved = edit(profile_id="new", name="Anthropic profile", values={
        "format": "anthropic", "base_url": "https://other.invalid", "model": "other-model", "api_key": "other-key"})
    profile_id = saved["saved_id"]
    assert load_config()["ai"]["model"] == "original"
    edit(profile_id=profile_id, name="Renamed", values={"model": "new-model", "api_key": None})
    edit(profile_id=profile_id, action="activate")
    assert load_config()["ai"]["api_key"] == "other-key"
    assert load_config()["ai"]["format"] == "anthropic"
    assert "extra_body_json" not in load_config()["ai"]
    edit(values={"api_key": ""})
    assert "api_key" not in load_config()["ai"]
    assert load_config()[f"web.ai.{profile_id}"]["api_key"] == "other-key"


def test_duplicate_current_preserves_credentials_without_returning_them(configured):
    result = edit(action="duplicate")
    saved = load_config()[f"web.ai.{result['saved_id']}"]
    assert saved['api_key'] == 'private-key' and saved['model'] == 'original'
    assert 'private-key' not in json.dumps(result)
    assert load_config()['ai']['model'] == 'original'


def test_stale_revision_never_overwrites(configured):
    revision = settings.snapshot()["revision"]
    configured.write_text(configured.read_text() + '# external change\n')
    before = configured.read_bytes()
    with pytest.raises(settings.SettingsConflict):
        settings.save(dict(kind="ai", revision=revision, values={"model": "bad"}))
    assert configured.read_bytes() == before


def test_simultaneous_saves_only_one_revision_wins(configured):
    revision = settings.snapshot()['revision']
    outcomes = []
    barrier = threading.Barrier(2)
    def worker(model):
        barrier.wait()
        try:
            settings.save(dict(kind='ai', revision=revision, values={'model': model}))
            outcomes.append('saved')
        except settings.SettingsConflict:
            outcomes.append('conflict')
    threads = [threading.Thread(target=worker, args=(model,)) for model in ['first', 'second']]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)
        assert not thread.is_alive()
    assert sorted(outcomes) == ['conflict', 'saved']
    assert load_config()['ai']['model'] in {'first', 'second'}


def test_failed_publish_keeps_original(configured, monkeypatch):
    before = configured.read_bytes()
    def fail(*args):
        raise OSError('simulated disk failure')
    monkeypatch.setattr(settings.os, 'replace', fail)
    with pytest.raises(OSError):
        edit(values={'model': 'not-published'})
    assert configured.read_bytes() == before
    assert not list(configured.parent.glob('.config-*'))


def test_config_snapshot_is_consistent_and_thread_local(configured):
    from cslurm.config import consistent_config
    @consistent_config
    def read_while_changed():
        before = load_config()['ai']['model']
        edit(values={'model': 'after'})
        other_thread = []
        thread = threading.Thread(target=lambda: other_thread.append(load_config()['ai']['model']))
        thread.start()
        thread.join()
        assert other_thread == ['after']
        assert load_config()['ai']['model'] == before
    read_while_changed()
    assert load_config()['ai']['model'] == 'after'


@pytest.mark.parametrize("values", [
    {"format": "invalid"}, {"timeout_seconds": "0"}, {"temperature": "nan"},
    {"request_retries": "1.5"}, {"api_key_env": "key\n[bad]"},
    {"extra_body_json": "[]"}, {"base_url": "file:///secret"},
    {"base_url": "http://remote.invalid"}, {"base_url": "https://key:secret@example.invalid"},
    {"model": 123}, {"arbitrary": "bad"},
])
def test_invalid_values_leave_file_untouched(configured, values):
    before = configured.read_bytes()
    with pytest.raises((ValueError, TypeError)):
        edit(values=values)
    assert configured.read_bytes() == before


def test_feishu_profiles_do_not_change_policy(configured):
    edit(kind="notification", values={"enabled": "false", "auto_dispatch": "false"})
    saved = edit(kind="feishu", profile_id="new", name="Second bot", values={"webhook_url": "https://example.invalid/second"})
    edit(kind="feishu", profile_id=saved["saved_id"], action="activate")
    assert load_config()["notification"]["enabled"] == "false"
    assert load_config()["notification"]["auto_dispatch"] == "false"
    assert "secret" not in load_config()["notification.feishu"]


@pytest.mark.parametrize("protocol", ["openai", "anthropic"])
def test_model_test_uses_selected_profile_and_no_fallback(configured, monkeypatch, protocol):
    from cslurm.ai.client import ModelClient
    saved = edit(profile_id="new", name="Test", values={"format": protocol, "model": "test-model",
        "base_url": "https://test.invalid", "api_key": "test-key", "enable_thinking": "false"})
    def fake_chat(client, messages):
        assert client.model == "test-model" and client.format == protocol
        assert client.api_key == "test-key" and client.base_url == "https://test.invalid"
        assert client.enable_thinking is False and client.extra_body == {}
        assert client.fallback_models == [] and client.timeout == 30 and client.request_retries == 0
        assert messages == [{"role": "user", "content": "Reply with OK."}]
        return "OK"
    monkeypatch.setattr(ModelClient, "chat_raw", fake_chat)
    result = settings.test_connection(dict(kind="ai", id=saved["saved_id"], revision=saved["revision"]))
    assert "成功" in result["message"]
    assert load_config()["ai"]["model"] == "original"


def test_feishu_test_requires_confirmation_and_sends_only_test(configured, monkeypatch):
    from cslurm.notify import feishu
    calls = []
    monkeypatch.setattr(feishu, "send_payload", lambda *args, **kwargs: calls.append((args, kwargs)))
    body = dict(kind="feishu", id="current", revision=settings.snapshot()["revision"])
    with pytest.raises(ValueError):
        settings.test_connection(body)
    assert not calls
    settings.test_connection({**body, "confirm_send": True})
    assert len(calls) == 1
    args, kwargs = calls[0]
    assert args[0] == "https://example.invalid/private-hook"
    assert "测试" in json.dumps(args[1], ensure_ascii=False)
    assert "sign" in args[1] and kwargs["attempts"] == 1


def test_discover_unsaved_draft_reuses_secret_without_saving(configured, monkeypatch):
    from cslurm.ai import models
    calls = []
    def fake(**kwargs):
        calls.append(kwargs)
        return {'models': [{'id': 'available', 'name': ''}], 'truncated': False}
    monkeypatch.setattr(models, 'list_models', fake)
    before = configured.read_bytes()
    body = dict(kind='ai', id='current', revision=settings.snapshot()['revision'],
                values={'base_url': 'https://draft.invalid', 'api_key': None})
    assert settings.fetch_models(body)['models'][0]['id'] == 'available'
    assert calls[-1]['api_key'] == 'private-key' and calls[-1]['base_url'] == 'https://draft.invalid'
    body.update(id='new', values={'format': 'anthropic', 'base_url': 'https://new.invalid', 'api_key': 'new-key'})
    settings.fetch_models(body)
    assert calls[-1]['api_key'] == 'new-key' and calls[-1]['format'] == 'anthropic'
    assert configured.read_bytes() == before
    assert not (configured.parent / 'config-backups').exists()
    monkeypatch.setenv('MODEL_TEST_KEY', 'env-key')
    body['values'] = {'base_url': 'https://new.invalid', 'api_key_env': 'MODEL_TEST_KEY'}
    settings.fetch_models(body)
    assert calls[-1]['api_key'] == 'env-key'
    with pytest.raises(settings.SettingsConflict):
        settings.fetch_models({**body, 'revision': 'old'})
    with pytest.raises(ValueError):
        settings.fetch_models({**body, 'kind': 'feishu'})
    with pytest.raises(ValueError):
        settings.fetch_models({**body, 'values': {'base_url': 'https://a.invalid', 'api_key': ''}})


@pytest.mark.parametrize('format,suffix', [('openai', '/chat/completions'), ('anthropic', '/v1/messages')])
def test_saved_selection_is_used_by_fresh_process(configured, format, suffix):
    saved = edit(profile_id='new', name='Selected catalog model', values={
        'format': format, 'base_url': 'https://selected.invalid',
        'api_key': 'selected-key', 'model': 'selected-model',
        'fallback_models': '', 'request_retries': '0'})
    assert load_config()['ai']['model'] == 'original'
    edit(profile_id=saved['saved_id'], action='activate')
    script = '''
import io, json
from cslurm.ai.client import ModelClient
sent = []
def transport(request, **kwargs):
    payload = json.loads(request.data)
    sent.append({'url': request.full_url, 'model': payload['model']})
    return io.BytesIO(json.dumps({
        'choices': [{'message': {'content': 'OK'}}],
        'content': [{'type': 'text', 'text': 'OK'}]
    }).encode())
client = ModelClient(urlopen=transport)
reply = client.chat_raw([{'role': 'user', 'content': 'Reply with OK.'}])
print(json.dumps({'sent': sent, 'reply': reply, 'last_model': client.last_model,
                  'fallback_models': client.fallback_models, 'format': client.format}))
'''
    env = {key: value for key, value in os.environ.items() if not key.startswith('CSLURM_AI_')}
    result = subprocess.run([sys.executable, '-c', script], env=env, check=True,
                            capture_output=True, text=True, timeout=10)
    assert json.loads(result.stdout) == {
        'sent': [{'url': 'https://selected.invalid' + suffix, 'model': 'selected-model'}],
        'reply': 'OK', 'last_model': 'selected-model', 'fallback_models': [], 'format': format}
