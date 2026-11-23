import json

import config


def test_al_keys_are_isolated_from_function_key_pools(monkeypatch):
    old_al = dict(config.AL_USER_KEYS)
    old_pool = list(config.KEY_POOLS.get("gemini", []))
    writes = []

    def fake_persist_write(filename, key, raw, **kwargs):
        writes.append(json.loads(raw))
        return True

    monkeypatch.setattr(config, "has_durable_runtime_storage", lambda: True)
    monkeypatch.setattr(config, "persist_write", fake_persist_write)
    config.AL_USER_KEYS.clear()
    config.KEY_POOLS["gemini"] = [{"key": "BOT_FUNCTION_KEY", "model": "gemini-3.8-flash"}]

    try:
        assert config.set_al_user_key(111, "gemini", "USER_111_KEY", "gemini-3.8-flash")
        assert config.set_al_user_key(222, "gemini", "USER_222_KEY", "gemini-3.8-flash")

        assert config.get_al_user_key(111, "gemini")["key"] == "USER_111_KEY"
        assert config.get_al_user_key(222, "gemini")["key"] == "USER_222_KEY"
        assert config.get_al_user_key(111, "groq") == {}
        assert config.KEY_POOLS["gemini"][0]["key"] == "BOT_FUNCTION_KEY"

        saved = writes[-1]
        assert saved["al_user_keys"]["111"]["key"] == "USER_111_KEY"
        assert saved["al_user_keys"]["222"]["key"] == "USER_222_KEY"
        assert saved["key_pools"]["gemini"][0]["key"] == "BOT_FUNCTION_KEY"
    finally:
        config.AL_USER_KEYS.clear()
        config.AL_USER_KEYS.update(old_al)
        config.KEY_POOLS["gemini"] = old_pool


def test_al_user_key_requires_durable_storage(monkeypatch):
    old_al = dict(config.AL_USER_KEYS)
    monkeypatch.setattr(config, "has_durable_runtime_storage", lambda: False)
    try:
        assert not config.set_al_user_key(333, "gemini", "TEMP_KEY", "gemini-3.8-flash")
        assert "333" not in config.AL_USER_KEYS
    finally:
        config.AL_USER_KEYS.clear()
        config.AL_USER_KEYS.update(old_al)


def test_al_user_keys_survive_runtime_config_reload(monkeypatch):
    old_al = dict(config.AL_USER_KEYS)
    old_pool = list(config.KEY_POOLS.get("gemini", []))
    saved = {}

    def fake_persist_write(filename, key, raw, **kwargs):
        saved["raw"] = raw
        return True

    monkeypatch.setattr(config, "has_durable_runtime_storage", lambda: True)
    monkeypatch.setattr(config, "persist_write", fake_persist_write)
    assert config.set_al_user_key(444, "gemini", "PERSISTED_USER_KEY", "gemini-3.8-flash")

    config.AL_USER_KEYS.clear()
    monkeypatch.setattr(config, "persist_read", lambda filename, key: (saved["raw"], "TEST_BACKEND"))
    config._load_runtime_overrides()
    try:
        assert config.get_al_user_key(444, "gemini")["key"] == "PERSISTED_USER_KEY"
    finally:
        config.AL_USER_KEYS.clear()
        config.AL_USER_KEYS.update(old_al)
        config.KEY_POOLS["gemini"] = old_pool
