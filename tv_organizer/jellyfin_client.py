"""Jellyfin API client for querying TV library metadata."""

import logging
import time
from typing import Optional
from urllib.parse import urljoin

import requests

logger = logging.getLogger(__name__)


class JellyfinError(Exception):
    """Raised when a Jellyfin API call fails."""


class JellyfinClient:
    """Client for the Jellyfin REST API."""

    def __init__(self, server_url: str, api_key: str, user_id: Optional[str] = None):
        self.server_url = server_url.rstrip("/")
        self.api_key = api_key
        self.user_id = user_id
        self.session = requests.Session()
        self.session.headers.update({"X-Emby-Token": self.api_key})

    def _get(self, path: str, params: Optional[dict] = None, retries: int = 3) -> dict:
        url = f"{self.server_url}{path}"
        for attempt in range(retries):
            try:
                resp = self.session.get(url, params=params, timeout=30)
                resp.raise_for_status()
                return resp.json()
            except requests.RequestException as e:
                if attempt < retries - 1:
                    wait = 2 ** (attempt + 1)
                    logger.warning("Jellyfin request failed (attempt %d), retrying in %ds: %s", attempt + 1, wait, e)
                    time.sleep(wait)
                else:
                    raise JellyfinError(f"Failed after {retries} attempts: {e}") from e

    @staticmethod
    def _items(data) -> list[dict]:
        """Extract items list from a response that may be a list or {Items: [...]}."""
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            return data.get("Items", [])
        return []

    def _post(self, path: str, params: Optional[dict] = None) -> Optional[dict]:
        url = f"{self.server_url}{path}"
        try:
            resp = self.session.post(url, params=params, timeout=30)
            resp.raise_for_status()
            if resp.content:
                return resp.json()
            return None
        except requests.RequestException as e:
            raise JellyfinError(f"POST {path} failed: {e}") from e

    def get_user_id(self) -> str:
        """Get the first admin user ID if none was provided."""
        if self.user_id:
            return self.user_id
        data = self._get("/Users")
        if not data:
            raise JellyfinError("No users found on Jellyfin server")
        # Prefer admin users
        for user in data:
            if user.get("Policy", {}).get("IsAdministrator"):
                self.user_id = user["Id"]
                logger.info("Using admin user: %s (%s)", user["Name"], self.user_id)
                return self.user_id
        self.user_id = data[0]["Id"]
        logger.info("Using user: %s (%s)", data[0]["Name"], self.user_id)
        return self.user_id

    def get_tv_library_id(self) -> Optional[str]:
        """Find the TV library (collection type 'tvshows') ID.

        Tries /Library/VirtualFolders first (server-level, no user ID needed),
        then falls back to /Users/{uid}/Views. Returns None if not found
        (get_all_shows will query globally instead).
        """
        # Method 1: /Library/VirtualFolders (server-level, most reliable)
        try:
            data = self._get("/Library/VirtualFolders")
            folders = data if isinstance(data, list) else []
            for folder in folders:
                if folder.get("CollectionType") == "tvshows":
                    item_id = folder.get("ItemId", "")
                    logger.info("Found TV library via VirtualFolders: %s (%s)",
                                folder.get("Name"), item_id)
                    return item_id
            logger.debug("VirtualFolders available: %s",
                         ", ".join(f"{f.get('Name')}({f.get('CollectionType', '?')})" for f in folders))
        except JellyfinError as e:
            logger.debug("VirtualFolders lookup failed: %s", e)

        # Method 2: /Users/{uid}/Views (user-level)
        try:
            uid = self.get_user_id()
            data = self._get(f"/Users/{uid}/Views")
            items = self._items(data)
            for item in items:
                if item.get("CollectionType") == "tvshows":
                    logger.info("Found TV library via Views: %s (%s)",
                                item["Name"], item["Id"])
                    return item["Id"]
            logger.debug("Views available: %s",
                         ", ".join(f"{i.get('Name')}({i.get('CollectionType', '?')})" for i in items))
        except JellyfinError as e:
            logger.debug("Views lookup failed: %s", e)

        logger.warning("Could not find TV library ID, will query all Series globally")
        return None

    def get_all_shows(self, library_id: Optional[str] = None) -> list[dict]:
        """Get all TV shows from the library."""
        if library_id is None:
            library_id = self.get_tv_library_id()
        uid = self.get_user_id()
        params = {
            "IncludeItemTypes": "Series",
            "Recursive": "true",
            "Fields": "Path,OfficialRating,Genres,Tags,Studios,Overview,"
                      "ProductionYear,ProviderIds,ImageTags,CommunityRating",
            "SortBy": "SortName",
            "SortOrder": "Ascending",
        }
        if library_id:
            params["ParentId"] = library_id
        data = self._get(f"/Users/{uid}/Items", params=params)
        return self._items(data)

    def get_seasons(self, show_id: str) -> list[dict]:
        """Get all seasons for a show."""
        uid = self.get_user_id()
        data = self._get(f"/Shows/{show_id}/Seasons", params={
            "UserId": uid,
            "Fields": "Path,IndexNumber",
        })
        return self._items(data)

    def get_episodes(self, show_id: str, season_id: Optional[str] = None) -> list[dict]:
        """Get all episodes for a show, optionally filtered by season."""
        uid = self.get_user_id()
        params = {
            "UserId": uid,
            "Fields": "Path,OfficialRating,Overview,IndexNumber,"
                      "ParentIndexNumber,MediaSources,Container",
        }
        if season_id:
            params["SeasonId"] = season_id
        data = self._get(f"/Shows/{show_id}/Episodes", params=params)
        return self._items(data)

    def get_image_url(self, item_id: str, image_type: str = "Primary",
                      max_width: int = 300) -> str:
        """Build a URL for an item's image."""
        return (f"{self.server_url}/Items/{item_id}/Images/{image_type}"
                f"?maxWidth={max_width}&api_key={self.api_key}")

    def trigger_library_scan(self) -> None:
        """Trigger a full library scan on Jellyfin."""
        self._post("/Library/Refresh")
        logger.info("Triggered Jellyfin library scan")

    def test_connection(self) -> dict:
        """Test connection and return server info."""
        data = self._get("/System/Info/Public")
        logger.info("Connected to Jellyfin %s (%s)", data.get("Version"), data.get("ServerName"))
        return data
