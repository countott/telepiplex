import pytest
from telepiplex_sync.config_wizard import SyncConfigWizard


@pytest.mark.parametrize("stage", ["choose", "provider_key", "confirm"])
def test_tmdb_stale_sessions_navigate_without_credentials_patch(stage):
    wizard = SyncConfigWizard({})
    owner = {"chat_id": 10, "user_id": 1}
    wizard._replace_session((10, 1), {"stage": stage, "section": "tmdb", "patch": {"tmdb": {"api_key": "private"}}})
    result = wizard.message({**owner, "text": "private"}) if stage == "provider_key" else wizard.callback({**owner, "payload": "config:tmdb" if stage == "choose" else "config:confirm"})
    assert result["config_navigation"] == "shared_tmdb"
    assert result["session"]["state"] == "close"
    assert "config_patch" not in result
    assert "private" not in repr(result)
    assert not wizard.has_session(owner)
