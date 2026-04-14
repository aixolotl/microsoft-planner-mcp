from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    CLIENT_ID: str
    CLIENT_SECRET: str
    TENANT_ID: str
    BASE_URL: str = "http://localhost:8000"
    CORS_ORIGINS: list[str] = ["*"]


settings = Settings()  # ty:ignore[missing-argument]
