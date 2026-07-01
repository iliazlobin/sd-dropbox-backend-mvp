from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        env_prefix="DROPBOX_",
    )

    database_url: str = "postgresql+asyncpg://dropbox@127.0.0.1:5432/dropbox"
    block_storage_dir: str = "data/blocks"
    app_port: int = 8000

    @property
    def block_storage_path(self) -> Path:
        return Path(self.block_storage_dir)


settings = Settings()
