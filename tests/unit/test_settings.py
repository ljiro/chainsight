from __future__ import annotations

import pytest

from core.settings import AisProvider, Settings


def make(**env: str) -> Settings:
    return Settings(_env_file=None, **env)  # type: ignore[arg-type]


def test_defaults_are_safe() -> None:
    s = make()
    assert s.ais_provider is AisProvider.DISABLED
    assert s.aisstream_api_key is None
    assert not s.is_production


def test_blank_secret_means_not_configured() -> None:
    s = make(aisstream_api_key="   ", maersk_consumer_key="")
    assert s.aisstream_api_key is None
    assert s.maersk_consumer_key is None


def test_secrets_are_masked_in_repr_and_dump() -> None:
    s = make(aisstream_api_key="super-secret-value")
    assert "super-secret-value" not in repr(s)
    assert "super-secret-value" not in s.model_dump_json()
    assert s.aisstream_api_key is not None
    assert s.aisstream_api_key.get_secret_value() == "super-secret-value"


def test_cors_origins_from_comma_separated_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("API_CORS_ORIGINS", "http://a.test, http://b.test")
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.api_cors_origins == ["http://a.test", "http://b.test"]


def test_rejects_invalid_log_format() -> None:
    with pytest.raises(ValueError):
        make(log_format="xml")


PROD_OK = {
    "app_env": "production",
    "jwt_secret": "x" * 40,
    "database_url": "postgresql+psycopg://cs:a-strong-password@db:5432/cs",
    "api_cors_origins": "https://chainsight.example",
}


def test_production_accepts_a_safe_configuration() -> None:
    s = make(**PROD_OK)
    assert s.is_production and s.cookie_secure


@pytest.mark.parametrize(
    ("override", "message"),
    [
        ({"jwt_secret": ""}, "JWT_SECRET"),
        ({"jwt_secret": "short"}, "JWT_SECRET"),
        ({"database_url": "postgresql+psycopg://chainsight:chainsight@db:5432/cs"}, "password"),
        ({"auth_cookie_secure": "false"}, "AUTH_COOKIE_SECURE"),
        ({"api_cors_origins": "http://chainsight.example"}, "https"),
    ],
)
def test_production_refuses_unsafe_configuration(override: dict[str, str], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        make(**(PROD_OK | override))


def test_development_cookies_are_not_secure_by_default() -> None:
    assert make().cookie_secure is False
    assert make(auth_cookie_secure="true").cookie_secure is True
