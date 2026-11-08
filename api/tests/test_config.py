from pathlib import Path

import pytest

from app.config import Settings

REQUIRED = {
    "CLIENT_ID": "demo-client",
    "CLIENT_SECRET": "demo-secret",
    "TOKEN_SECRET": "demo-token-secret-0123456789abcdef",
}


OPTIONAL = ("DATA_DIR", "TOKEN_TTL_SECONDS", "CORS_ORIGINS", "SOURCE_API_URL", "HTTP_TIMEOUT", "MAX_CONCURRENT_JOBS")


@pytest.fixture
def env(monkeypatch):
    for name in OPTIONAL:
        monkeypatch.delenv(name, raising=False)
    for name, value in REQUIRED.items():
        monkeypatch.setenv(name, value)
    return monkeypatch


def test_required_settings_come_from_environment(env):
    settings = Settings.from_env()
    assert (settings.client_id, settings.client_secret, settings.token_secret) == tuple(REQUIRED.values())


def test_defaults(env):
    settings = Settings.from_env()
    assert settings.data_dir == Path("/data")
    assert settings.token_ttl_seconds == 300
    assert settings.cors_origins == ("http://localhost:8080",)
    assert settings.source_api_url == "http://source-stub:8001"
    assert settings.http_timeout == 10.0
    assert settings.max_concurrent_jobs == 10


def test_optional_settings_come_from_environment(env, tmp_path):
    env.setenv("DATA_DIR", str(tmp_path))
    env.setenv("TOKEN_TTL_SECONDS", "60")
    env.setenv("CORS_ORIGINS", "http://a.test, http://b.test")
    env.setenv("SOURCE_API_URL", "http://src.test")
    env.setenv("HTTP_TIMEOUT", "2.5")
    env.setenv("MAX_CONCURRENT_JOBS", "3")
    settings = Settings.from_env()
    assert settings.data_dir == tmp_path
    assert settings.token_ttl_seconds == 60
    assert settings.cors_origins == ("http://a.test", "http://b.test")
    assert settings.source_api_url == "http://src.test"
    assert settings.http_timeout == 2.5
    assert settings.max_concurrent_jobs == 3


def test_empty_values_fall_back_to_defaults(env):
    env.setenv("SOURCE_API_URL", "")
    env.setenv("DATA_DIR", "")
    settings = Settings.from_env()
    assert settings.source_api_url == "http://source-stub:8001"
    assert settings.data_dir == Path("/data")


def test_empty_cors_origins_fall_back_to_default(env):
    env.setenv("CORS_ORIGINS", " , ")
    assert Settings.from_env().cors_origins == ("http://localhost:8080",)


@pytest.mark.parametrize("ttl", ["0", "-5", "abc"])
def test_token_lifetime_must_be_positive(env, ttl):
    env.setenv("TOKEN_TTL_SECONDS", ttl)
    with pytest.raises(ValueError, match="TOKEN_TTL_SECONDS"):
        Settings.from_env()


@pytest.mark.parametrize("timeout", ["0", "-1", "abc", "nan"])
def test_http_timeout_must_be_positive(env, timeout):
    env.setenv("HTTP_TIMEOUT", timeout)
    with pytest.raises(ValueError, match="HTTP_TIMEOUT"):
        Settings.from_env()


def test_missing_required_settings_stop_startup(env):
    for name in REQUIRED:
        env.delenv(name)
    with pytest.raises(ValueError, match="CLIENT_ID, CLIENT_SECRET, TOKEN_SECRET"):
        Settings.from_env()


@pytest.mark.parametrize("limit", ["0", "-1", "abc"])
def test_max_concurrent_jobs_must_be_positive(env, limit):
    env.setenv("MAX_CONCURRENT_JOBS", limit)
    with pytest.raises(ValueError, match="MAX_CONCURRENT_JOBS"):
        Settings.from_env()
