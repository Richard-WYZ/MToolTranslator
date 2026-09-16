import pytest

from translation.quality.retry import fallback_translate, retry_with_fallback


@pytest.mark.parametrize("outer_fallback", [False, True])
def test_permanent_failure_never_reenters_primary_via_chunking(outer_fallback):
    calls = []

    class PermanentError(RuntimeError):
        retryable = False

    def fail(model, text, **kwargs):
        calls.append(model)
        raise PermanentError("unsupported request")

    if outer_fallback:
        result = fallback_translate(
            "source", model="primary", system_prompt="base", prompt_style="professional",
            system_prompts={"professional": "base"}, fallback_models=[],
            chunk_strategy={"max_chars": 2}, file_path="", row_idx=0, col_idx=0,
            compose_prompt=lambda value: value, translate_func=fail,
        )
        assert result == ""
    else:
        result = retry_with_fallback(
            "source", model="primary", system_prompt="base", fallback_models=[], translator=fail,
        )
        assert result["status"] == "NEEDS_REVIEW"
    assert calls == ["primary"]
