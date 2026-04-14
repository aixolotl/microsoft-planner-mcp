from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    CLIENT_ID: str
    CLIENT_SECRET: str
    TENANT_ID: str
    BASE_URL: str = "http://localhost:8000"
    ALLOWED_ORIGINS: list[str] = ["http://localhost:8000"]

settings = Settings()  # ty:ignore[missing-argument]
