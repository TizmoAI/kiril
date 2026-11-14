import pytest

from src import config
from src.config import Config


def test_default_ollama_base_url_uses_localhost_outside_docker(monkeypatch):
    monkeypatch.setattr(config.os.path, "exists", lambda path: False)

    assert Config._default_ollama_base_url() == "http://localhost:11434/v1"


def test_default_ollama_base_url_uses_host_gateway_in_docker(monkeypatch):
    monkeypatch.setattr(config.os.path, "exists", lambda path: path == "/.dockerenv")

    assert Config._default_ollama_base_url() == "http://host.docker.internal:11434/v1"


def test_youtube_duration_limit_uses_active_subscription_plan(monkeypatch):
    monkeypatch.setenv("MAX_VIDEO_DURATION", "5400")
    monkeypatch.setenv("PRO_YOUTUBE_MAX_VIDEO_DURATION", "7200")
    monkeypatch.setenv("SCALE_YOUTUBE_MAX_VIDEO_DURATION", "10800")
    config = Config()

    assert config.max_youtube_video_duration_for_plan("free", "active") == 5400
    assert config.max_youtube_video_duration_for_plan("pro", "active") == 7200
    assert config.max_youtube_video_duration_for_plan("scale", "trialing") == 10800
    assert config.max_youtube_video_duration_for_plan("scale", "inactive") == 5400
    assert config.max_youtube_video_duration_for_plan("scale", "past_due", "stripe") == 10800
    assert config.max_youtube_video_duration_for_plan("scale", "past_due") == 5400
    assert config.max_youtube_video_duration_for_plan("scale", "unpaid", "stripe") == 5400


@pytest.mark.parametrize(
    "raw,expected",
    [
        (None, 30.0),
        ("", 30.0),
        ("60", 60.0),
        ("45.5", 45.5),
        ("0", 30.0),
        ("-5", 30.0),
        ("nope", 30.0),
        ("inf", 30.0),
        ("999", 180.0),
    ],
)
def test_youtube_preflight_timeout_default_and_override(monkeypatch, raw, expected):
    if raw is None:
        monkeypatch.delenv("YOUTUBE_PREFLIGHT_TIMEOUT_SECONDS", raising=False)
    else:
        monkeypatch.setenv("YOUTUBE_PREFLIGHT_TIMEOUT_SECONDS", raw)
    assert Config().youtube_preflight_timeout_seconds == expected


def test_hook_teaser_is_off_unless_requested(monkeypatch):
    monkeypatch.delenv("HOOK_FIRST_TEASER", raising=False)
    assert Config().hook_first_teaser is False
    monkeypatch.setenv("HOOK_FIRST_TEASER", "true")
    assert Config().hook_first_teaser is True


def test_groq_is_the_default_transcription_provider(monkeypatch):
    monkeypatch.delenv("TRANSCRIPTION_PROVIDER", raising=False)
    assert Config().transcription_provider == "groq"
    monkeypatch.setenv("TRANSCRIPTION_PROVIDER", "whisper")
    assert Config().transcription_provider == "groq"
    monkeypatch.setenv("TRANSCRIPTION_PROVIDER", "assemblyai")
    assert Config().transcription_provider == "assemblyai"


def test_claude_is_the_default_llm_when_its_key_is_set(monkeypatch):
    monkeypatch.delenv("LLM", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    assert Config().llm == "anthropic:claude-sonnet-5-5"


def _only_keys(monkeypatch, **keys):
    for name in ("LLM", "ANTHROPIC_API_KEY", "GOOGLE_API_KEY", "OPENAI_API_KEY", "OPENROUTER_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    for name, value in keys.items():
        monkeypatch.setenv(name, value)


def test_gemini_is_used_when_there_is_no_claude_key(monkeypatch):
    _only_keys(monkeypatch, GOOGLE_API_KEY="test-google-key")
    assert Config().llm == "google-gla:gemini-3-flash-preview"


def test_claude_wins_over_gemini_when_both_keys_are_set(monkeypatch):
    _only_keys(monkeypatch, GOOGLE_API_KEY="g", ANTHROPIC_API_KEY="a")
    assert Config().llm == "anthropic:claude-sonnet-5-5"
