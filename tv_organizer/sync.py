"""Sync Jellyfin library data into the local database."""

import logging
from datetime import datetime

from .jellyfin_client import JellyfinClient
from .models import Classification, Database, Episode, Show

logger = logging.getLogger(__name__)


def sync_library(client: JellyfinClient, db: Database) -> dict:
    """Fetch all shows and episodes from Jellyfin and store in local DB.

    Preserves existing classification decisions.

    Returns:
        Summary dict with counts
    """
    stats = {"shows": 0, "episodes": 0, "seasons": 0}

    logger.info("Fetching TV shows from Jellyfin...")
    jf_shows = client.get_all_shows()
    logger.info("Found %d shows", len(jf_shows))

    for jf_show in jf_shows:
        show_id = jf_show["Id"]

        # Check if we already have a classification for this show
        existing = db.get_show(show_id)
        existing_classification = existing.classification if existing else Classification.UNCLASSIFIED

        # Get season/episode counts
        seasons = client.get_seasons(show_id)
        episodes = client.get_episodes(show_id)

        image_tag = ""
        image_tags = jf_show.get("ImageTags", {})
        if "Primary" in image_tags:
            image_tag = image_tags["Primary"]

        show = Show(
            jellyfin_id=show_id,
            name=jf_show.get("Name", "Unknown"),
            sort_name=jf_show.get("SortName", jf_show.get("Name", "")),
            year=jf_show.get("ProductionYear"),
            official_rating=jf_show.get("OfficialRating", ""),
            genres=jf_show.get("Genres", []),
            tags=jf_show.get("Tags", []),
            overview=jf_show.get("Overview", ""),
            community_rating=jf_show.get("CommunityRating"),
            path=jf_show.get("Path", ""),
            image_tag=image_tag,
            classification=existing_classification,
            episode_count=len(episodes),
            season_count=len(seasons),
        )
        db.upsert_show(show)
        stats["shows"] += 1
        stats["seasons"] += len(seasons)

        for jf_ep in episodes:
            ep = Episode(
                jellyfin_id=jf_ep["Id"],
                show_id=show_id,
                show_name=jf_show.get("Name", "Unknown"),
                season_number=jf_ep.get("ParentIndexNumber", 0) or 0,
                episode_number=jf_ep.get("IndexNumber", 0) or 0,
                name=jf_ep.get("Name", ""),
                path=jf_ep.get("Path", ""),
                container=jf_ep.get("Container", ""),
                year=jf_show.get("ProductionYear"),
            )
            db.upsert_episode(ep)
            stats["episodes"] += 1

    db.set_cache_meta("last_sync", datetime.now().isoformat())
    logger.info("Sync complete: %(shows)d shows, %(seasons)d seasons, %(episodes)d episodes", stats)
    return stats
