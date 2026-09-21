from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime settings loaded from environment variables."""

    model_config = SettingsConfigDict(env_prefix="UV_PROXY_", env_file=".env", extra="ignore")

    host: str = "127.0.0.1"
    port: int = 8000
    request_timeout: float = 12.0
    max_body_bytes: int = 524_288
    allow_private_targets: bool = False
    allowed_hosts: str = ""

    @property
    def allowed_host_patterns(self) -> tuple[str, ...]:
        return tuple(
            pattern.strip().lower()
            for pattern in self.allowed_hosts.split(",")
            if pattern.strip()
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
