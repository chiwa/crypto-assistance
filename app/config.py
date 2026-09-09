from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "Crypto Assistance"
    database_path: str = "data/crypto_assistance.db"
    bitkub_base_url: str = "https://api.bitkub.com"
    deep_seek_api_key: str | None = None
    telegram_bot_token: str | None = None
    telegram_chat_id: str | None = None

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()
