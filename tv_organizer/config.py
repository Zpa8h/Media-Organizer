"""Application configuration."""

import os
from dataclasses import dataclass, field


@dataclass
class Config:
    """Configuration loaded from environment variables or .env file."""

    # Jellyfin connection
    jellyfin_url: str = ""
    jellyfin_api_key: str = ""
    jellyfin_user_id: str = ""

    # Paths
    source_dir: str = "/TV"
    kids_dest: str = "/tv-kids"
    adults_dest: str = "/TV"

    # Database
    db_path: str = "organizer.db"

    # Web UI
    host: str = "0.0.0.0"
    port: int = 5000
    debug: bool = False

    @classmethod
    def from_env(cls) -> "Config":
        """Load configuration from environment variables."""
        return cls(
            jellyfin_url=_clean_env("JELLYFIN_URL", ""),
            jellyfin_api_key=_clean_env("JELLYFIN_API_KEY", ""),
            jellyfin_user_id=_clean_env("JELLYFIN_USER_ID", ""),
            source_dir=_clean_env("SOURCE_DIR", "/TV"),
            kids_dest=_clean_env("KIDS_DEST", "/tv-kids"),
            adults_dest=_clean_env("ADULTS_DEST", "/TV"),
            db_path=_clean_env("ORGANIZER_DB", "organizer.db"),
            host=_clean_env("HOST", "0.0.0.0"),
            port=int(_clean_env("PORT", "5000")),
            debug=_clean_env("DEBUG", "").lower() in ("1", "true", "yes"),
        )

    def validate(self) -> list[str]:
        """Return a list of configuration errors."""
        errors = []
        if not self.jellyfin_url:
            errors.append("JELLYFIN_URL is required")
        if not self.jellyfin_api_key:
            errors.append("JELLYFIN_API_KEY is required")
        if not self.kids_dest:
            errors.append("KIDS_DEST is required")
        if not self.adults_dest:
            errors.append("ADULTS_DEST is required")
        return errors


def _clean_env(key: str, default: str = "") -> str:
    """Read an env var, stripping whitespace and inline comments."""
    val = os.environ.get(key, default).strip()
    if val.startswith("#"):
        return default
    return val
