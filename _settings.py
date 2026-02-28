from pydantic_settings import BaseSettings, SettingsConfigDict
from functools import lru_cache


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    hackapizza_team_api_key: str
    hackapizza_team_id: int


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


def main():
    settings = get_settings()
    print(f"Team ID: {settings.hackapizza_team_id}")
    print(f"Team API Key set: {bool(settings.hackapizza_team_api_key)}")


if __name__ == "__main__":
    main()
