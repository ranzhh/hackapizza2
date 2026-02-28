from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict

_COMMON_SETTINGS_CONFIG = SettingsConfigDict(
    env_file=".env",
    env_file_encoding="utf-8",
    extra="ignore",
)


class Settings(BaseSettings):
    model_config = _COMMON_SETTINGS_CONFIG

    hackapizza_team_api_key: str
    hackapizza_team_id: int
    regolo_api_key: str
    event_proxy_url: str


class SqlLoggingSettings(BaseSettings):
    model_config = _COMMON_SETTINGS_CONFIG

    hackapizza_sql_connstr: str


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()  # type: ignore


@lru_cache(maxsize=1)
def get_sql_logging_settings() -> SqlLoggingSettings:
    return SqlLoggingSettings()  # type: ignore


def main():
    settings = get_settings()
    sql_logging_settings = get_sql_logging_settings()
    print(f"Team ID: {settings.hackapizza_team_id}")
    print(f"Team API Key set: {bool(settings.hackapizza_team_api_key)}")
    print(f"SQL connstr configured: {bool(sql_logging_settings.hackapizza_sql_connstr)}")


if __name__ == "__main__":
    main()
