from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str
    # signs the users' JWTs; HS256 wants at least 32 characters
    jwt_secret: str = Field(min_length=32)
    jwt_expiry_hours: int = 8
    # how long a pairing code from the dashboard stays valid
    pairing_code_minutes: int = Field(default=10, ge=1, le=60)

    # resolved from this file, so the API starts from any working directory
    model_config = SettingsConfigDict(env_file=Path(__file__).parent / ".env")


settings = Settings()
