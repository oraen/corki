"""Provider-owned defaults do not depend on a developer's Corki configuration."""
import json

import pytest

from corki_adaptor.control import Controller, main
from corki_adaptor.openai.models import model_catalog as openai_catalog
from corki_adaptor.qoder.models import model_catalog as qoder_catalog


def test_openai_defaults_only_include_latest_two_generations_and_are_isolated():
    catalog = openai_catalog()
    assert list(catalog) == ['gpt-6.1-sol', 'gpt-6-astra', 'gpt-6-sol', 'gpt-6-luna']
    for settings in catalog.values():
        assert settings['context_window'] == 524288
        assert settings['context_window'] <= settings['max_context_window']
        assert settings['reasoning_effort'] == 'medium'
        assert settings['description']
    catalog['gpt-6.1-sol']['supported_reasoning_levels'].clear()
    assert openai_catalog()['gpt-6.1-sol']['supported_reasoning_levels']


@pytest.mark.parametrize('provider,catalog', [('openai', openai_catalog), ('qoder', qoder_catalog)])
def test_control_check_returns_provider_catalog_without_reading_real_login(
    tmp_path, monkeypatch, capsys, provider, catalog,
):
    def check(self):
        self.startup_provider = self.provider
    monkeypatch.setattr(Controller, 'check', check)
    assert main(['check', '--home', str(tmp_path), '--provider', provider]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result['ready']
    assert result['models'] == catalog()
    assert result['models']
    if provider == 'qoder':
        assert set(result['models']) == {
            'ultimate', 'Qwen', 'Kimi-K2.8-Preview', 'GLM-5.3', 'DeepSeek-V4-Pro',
        }
