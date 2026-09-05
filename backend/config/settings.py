from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import field_validator

class Settings(BaseSettings):
    AUTH_MODE: str = "none"
    REDIS_HOST: str = "localhost"
    SUPPORTED_IMAGES: str = "patchwork-django:latest,patchwork-express:latest"

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @field_validator("AUTH_MODE")
    def validate_auth_mode(cls, v):
        if v not in ["none", "api_key"]:
            raise ValueError(f"Unsupported AUTH_MODE: {v}")
        return v

settings = Settings()