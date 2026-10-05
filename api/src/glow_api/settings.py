import json
from typing import Any, List, Optional

from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import field_validator


def parse_list(v: Any) -> List[str]:
    if isinstance(v, str):
        v = v.strip()
        if v.startswith("["):
            return json.loads(v)
        return [s.strip() for s in v.split(",") if s.strip()]
    return v


class Settings(BaseSettings):
    # ODK Central configuration
    ODK_API_URL: str = "http://localhost:8383"  # Default for testing
    ODK_API_EMAIL: str = "test@example.com"  # Default for testing
    ODK_API_PASSWORD: str = "test-password"  # Default for testing
    ODK_PROJECT_ID: int = 1  # Default for testing
    ODK_HOST_HEADER: str = ""  # e.g. odk.local when ODK_API_URL is https://nginx
    ODK_DEMOGRAPHICS_FORM_ID: str = "demographics_questionnaire"

    # Data refresh configuration
    DATA_CACHE_PATH: Optional[str] = None  # If set, cache DataFrame and ETAG
    DATA_REFRESH_HOURS: int = 1  # Poll ODK Central every hour
    DATA_PREFIXES: List[str] = ["bw", "phq9"]
    DATA_DEMOGRAPHIC_PREFIXES: List[str] = ["d"]

    # Period derivation configuration
    PERIOD_TIMEZONE: str = (
        "Europe/London"  # Deployment timezone for period calculations
    )
    PERIOD_CUTOFF_MONTH: int = 9  # September (academic year starts)
    PERIOD_CUTOFF_DAY: int = 1  # 1st of month

    # Deployment
    APP_VERSION: str = (
        "dev"  # Set from the deployed git tag; "dev" outside the deploy pipeline
    )

    # Security
    MIN_N: int = 5
    SECRET_KEY: str = "change-me-in-production"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 8  # 8 hours
    API_KEY_EXPIRE_DAYS: int = 90  # default lifetime for a newly-created WRC API key

    # Cognito / auth mode
    COGNITO_USER_POOL_ID: Optional[str] = None
    COGNITO_CLIENT_ID: Optional[str] = None
    COGNITO_REGION: Optional[str] = None
    DEMO_MODE: bool = False

    # Database
    METADATA_DATABASE_URL: str = "sqlite:///./metadata.db"
    CORS_ORIGINS: List[str] = ["*"]

    # Logging configuration
    LOG_LEVEL: str = "INFO"  # Log level for glow_api module
    LOG_UVICORN_ACCESS: str = "WARNING"  # Log level for uvicorn.access; duplicates our own request_completed logs
    LOG_UVICORN: str = "INFO"  # Log level for uvicorn.error (server logs)

    # Audit log sink (durable, append-only JSONL for audit-tier requests)
    AUDIT_LOG_PATH: Optional[str] = (
        None  # e.g. /var/log/glow-api/audit.jsonl; None disables the file sink
    )
    AUDIT_LOG_MAX_BYTES: int = 50_000_000  # rotate at ~50MB
    AUDIT_LOG_BACKUP_COUNT: int = 20  # keep up to 20 rotated files (~1GB total)

    model_config = SettingsConfigDict(
        env_prefix="GLOW_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @field_validator(
        "DATA_PREFIXES", "DATA_DEMOGRAPHIC_PREFIXES", "CORS_ORIGINS", mode="before"
    )
    @classmethod
    def parse_list(cls, v) -> List[str]:
        return parse_list(v)

    def warn_insecure_defaults(self) -> None:
        """Emit a warning if the default insecure SECRET_KEY is still in use."""
        import warnings

        if self.SECRET_KEY == "change-me-in-production":
            warnings.warn(
                "SECRET_KEY is set to the default insecure value. "
                "Set GLOW_SECRET_KEY to a strong random secret before deploying.",
                UserWarning,
                stacklevel=2,
            )

    def validate_auth_config(self) -> None:
        """Raise if the auth configuration is ambiguous or incomplete.

        Two things must hold:
        - DEMO_MODE and a Cognito pool are never both configured.
        - A configured Cognito pool always comes with the client id and
          region PyJWT needs to actually verify `aud`/build the JWKS URL -
          without them, verification would silently degrade (no audience
          check, or a garbage issuer/JWKS URL).
        """
        if self.DEMO_MODE and self.COGNITO_USER_POOL_ID:
            raise RuntimeError(
                "GLOW_DEMO_MODE is enabled but GLOW_COGNITO_USER_POOL_ID is also "
                "set. Use Cognito verification in any environment with real user data; "
                "enable the demo mode only when no Cognito pool is configured."
            )
        if self.COGNITO_USER_POOL_ID and not (
            self.COGNITO_CLIENT_ID and self.COGNITO_REGION
        ):
            raise RuntimeError(
                "GLOW_COGNITO_USER_POOL_ID is set but GLOW_COGNITO_CLIENT_ID and/or "
                "GLOW_COGNITO_REGION are not. Both are required to verify Cognito "
                "tokens (audience check, JWKS endpoint URL)."
            )


settings = Settings()
