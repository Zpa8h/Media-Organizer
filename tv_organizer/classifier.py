"""Classification engine with auto-suggestion rules."""

import logging
from typing import Optional

from .models import Classification, Database, Show

logger = logging.getLogger(__name__)

# Ratings that strongly suggest kids content
KIDS_RATINGS = {"TV-Y", "TV-Y7", "TV-Y7-FV", "TV-G", "G", "TV-PG"}
# Ratings that strongly suggest adult content
ADULT_RATINGS = {"TV-14", "TV-MA", "R", "NC-17", "NR"}
# Genres that suggest kids content
KIDS_GENRES = {"Kids", "Children", "Animation", "Family"}


def auto_suggest(show: Show) -> Optional[Classification]:
    """Suggest a classification based on metadata. Returns None if uncertain."""
    rating = show.official_rating.strip().upper() if show.official_rating else ""
    genres_upper = {g.strip() for g in show.genres}

    # Strong adult signals
    if rating in {"TV-MA", "R", "NC-17"}:
        return Classification.ADULTS

    # Strong kids signals
    if rating in {"TV-Y", "TV-Y7", "TV-Y7-FV"}:
        return Classification.KIDS

    # Genre-based kids signals (only if rating doesn't contradict)
    genre_overlap = genres_upper & {"Kids", "Children"}
    if genre_overlap and rating not in {"TV-14", "TV-MA", "R", "NC-17"}:
        return Classification.KIDS

    # TV-G is likely kids
    if rating in {"TV-G", "G"}:
        return Classification.KIDS

    # TV-14 is adults
    if rating == "TV-14":
        return Classification.ADULTS

    # TV-PG is ambiguous - could be either
    # Animation without other signals is ambiguous too
    return None


def auto_classify_all(db: Database) -> dict:
    """Run auto-classification on all unclassified shows. Returns counts."""
    shows = db.get_all_shows()
    counts = {"kids": 0, "adults": 0, "uncertain": 0}

    for show in shows:
        if show.classification != Classification.UNCLASSIFIED:
            continue
        suggestion = auto_suggest(show)
        if suggestion:
            db.set_classification(show.jellyfin_id, suggestion)
            counts[suggestion.value] += 1
            logger.info("Auto-classified '%s' as %s (rating=%s, genres=%s)",
                        show.name, suggestion.value, show.official_rating, show.genres)
        else:
            counts["uncertain"] += 1

    return counts


def apply_rating_rule(db: Database, ratings: list[str],
                      classification: Classification) -> int:
    """Classify all unclassified shows with given ratings."""
    count = db.classify_by_rating(ratings, classification)
    logger.info("Classified %d shows with ratings %s as %s", count, ratings, classification.value)
    return count


def apply_genre_rule(db: Database, genre: str, classification: Classification) -> int:
    """Classify all unclassified shows with a given genre."""
    count = db.classify_by_genre(genre, classification)
    logger.info("Classified %d shows with genre '%s' as %s", count, genre, classification.value)
    return count
