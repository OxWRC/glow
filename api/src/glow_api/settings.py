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
    DATA_CACHE_PATH: Optional[str] = None  # If set, cache the slim counts frame
    # Hourly slim poll (uid/school/date only) behind the live counts
    # endpoints. The full dataset is fetched on vintage cuts only.
    DATA_REFRESH_HOURS: int = 1
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
    API_KEY_CLAIM_HOURS: int = 24  # lifetime of the emailed one-time claim link

    # Public dashboard origin, for links in outgoing email (claim links)
    DASHBOARD_URL: str = "http://localhost:3000"

    # Outbound mail (shared with ODK Central's GLOW_SMTP_* settings).
    # Empty host disables sending; issuing API keys then fails with 503.
    SMTP_HOST: str = ""
    SMTP_PORT: int = 587
    SMTP_SECURE: bool = False  # implicit TLS; otherwise STARTTLS when offered
    SMTP_USER: str = ""
    SMTP_PASSWORD: str = ""
    SMTP_FROM: str = "no-reply@localhost"

    # OIDC / auth mode
    OIDC_ISSUER: Optional[str] = None
    OIDC_CLIENT_ID: Optional[str] = None
    DEMO_MODE: bool = False

    # Vintages (periodic, immutable dataset archives /query and /export pin against)
    VINTAGES_DIR: Optional[str] = None
    VINTAGE_CADENCE_CRON_DAY: int = 1  # day of month
    VINTAGE_CADENCE_CRON_HOUR: int = 3  # UTC hour

    # Database
    METADATA_DATABASE_URL: str = "sqlite:///./metadata.db"
    CORS_ORIGINS: List[str] = ["*"]

    # Logging configuration
    LOG_LEVEL: str = "INFO"  # Log level for glow_api module
    LOG_UVICORN_ACCESS: str = "WARNING"  # Log level for uvicorn.access; duplicates our own request_completed logs
    LOG_UVICORN: str = "INFO"  # Log level for uvicorn.error (server logs)

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

        Three things must hold:
        - DEMO_MODE and an OIDC issuer are never both configured.
        - A configured issuer always comes with the client id PyJWT needs
          to verify `aud` (without it, the audience check would degrade).
        - The issuer is https (tokens' keys are fetched from it).
        """
        if self.DEMO_MODE and self.OIDC_ISSUER:
            raise RuntimeError(
                "GLOW_DEMO_MODE is enabled but GLOW_OIDC_ISSUER is also set. "
                "Use OIDC verification in any environment with real user data; "
                "enable the demo mode only when no issuer is configured."
            )
        if not self.OIDC_ISSUER:
            return
        if not self.OIDC_CLIENT_ID:
            raise RuntimeError(
                "GLOW_OIDC_ISSUER is set but GLOW_OIDC_CLIENT_ID is not. "
                "It is required for the audience check."
            )
        if not self.OIDC_ISSUER.startswith("https://"):
            raise RuntimeError("GLOW_OIDC_ISSUER must start with https://")


settings = Settings()
