"""Configuration module using pydantic-settings."""
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""
    
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False
    )
    
    # Telegram Bot
    bot_token: str
    notification_chat_id: str | None = None  # Chat ID for notifications about QR requests
    
    # Yandex Disk
    yandex_disk_token: str
    yadisk_folder: str = "/dopps"
    
    # Temporary files
    tmp_dir: str = "/tmp/dopp_bot"
    
    # QR code extraction settings
    qr_dpi: int = 150
    
    # Logging
    log_level: str = "INFO"


# Global settings instance
settings = Settings()
