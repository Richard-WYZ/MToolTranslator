from translation.models import router


def test_api_checkpoint_identity_separates_connection_and_protocol(monkeypatch):
    config = {
        "base_url": "https://gateway.example/v1",
        "style": "openai",
        "preset": "opendesign_go",
        "profile_id": "design",
        "api_key": "private-test-secret",
        "disable_thinking": True,
        "model_protocols": {"shared-model": "chat_completions"},
    }
    monkeypatch.setattr(router, "third_party_api_config", lambda: config)
    initial = router.model_configuration("api:shared-model")
    assert "private-test-secret" not in repr(initial)

    for field, replacement in (
        ("style", "responses"),
        ("preset", "openai"),
        ("profile_id", "another-account"),
        ("disable_thinking", False),
        ("model_protocols", {"shared-model": "responses"}),
    ):
        original = config[field]
        config[field] = replacement
        assert router.model_configuration("api:shared-model") != initial
        config[field] = original
    assert router.model_configuration("api:shared-model") == initial
