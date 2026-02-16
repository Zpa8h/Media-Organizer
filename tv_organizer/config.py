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
            jellyfin_url=os.environ.get("JELLYFIN_URL", ""),
            jellyfin_api_key=os.environ.get("JELLYFIN_API_KEY", ""),
            jellyfin_user_id=os.environ.get("JELLYFIN_USER_ID", ""),
            source_dir=os.environ.get("SOURCE_DIR", "/TV"),
            kids_dest=os.environ.get("KIDS_DEST", "/tv-kids"),
            adults_dest=os.environ.get("ADULTS_DEST", "/TV"),
            db_path=os.environ.get("ORGANIZER_DB", "organizer.db"),
            host=os.environ.get("HOST", "0.0.0.0"),
            port=int(os.environ.get("PORT", "5000")),
            debug=os.environ.get("DEBUG", "").lower() in ("1", "true", "yes"),
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
