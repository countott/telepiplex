from telepiplex_search.config_wizard import SearchConfigWizard
import pytest

OWNER = {"chat_id": 10, "user_id": 1}


@pytest.mark.parametrize("stage", ["tmdb_api_key", "api_value", "confirm", "api_fields"])
def test_stale_tmdb_secret_prompts_and_confirmations_only_navigate(stage):
    wizard = SearchConfigWizard({})
    wizard._replace_session((10, 1), {"stage": stage, "provider": "tmdb", "field": "api_key", "patch": {"metadata": {"tmdb": {"api_key": "private"}}}})
    result = wizard.message({**OWNER, "text": "private"}) if stage in {"tmdb_api_key", "api_value"} else wizard.callback({**OWNER, "payload": "config:confirm" if stage == "confirm" else "config:field:tmdb:api_key"})
    assert result["config_navigation"] == "shared_tmdb"
    assert result["session"]["state"] == "close"
    assert "config_patch" not in result
    assert "private" not in repr(result)
    assert not wizard.has_session(OWNER)


def test_simple_tmdb_enable_keeps_local_gate_without_collecting_token():
    wizard = SearchConfigWizard({})
    wizard.start(OWNER)
    wizard.callback({**OWNER, "payload": "config:tmdb"})
    wizard.callback({**OWNER, "payload": "config:boolean:on"})
    result = wizard.callback({**OWNER, "payload": "config:confirm"})
    assert result["config_patch"] == {"metadata": {"tmdb": {"enable": True}}}
