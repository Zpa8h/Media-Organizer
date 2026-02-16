"""Move planner: generates the plan of file moves with same-directory safety."""

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .models import Classification, Database, Episode, Show

logger = logging.getLogger(__name__)


@dataclass
class PlannedMove:
    """A single planned file operation."""
    episode_id: str
    show_id: str
    source: str
    destination: str
    show_name: str
    season_number: int
    episode_number: int
    classification: str
    is_associated: bool = False  # True for subtitle/nfo companion files
    issue: str = ""  # Warning text if any

    @property
    def already_correct(self) -> bool:
        """Check if the file is already in its correct location."""
        return _paths_equal(self.source, self.destination)

    @property
    def same_root(self) -> bool:
        """Check if source and destination share the same root directory."""
        src = Path(self.source).resolve()
        dst = Path(self.destination).resolve()
        # Check if they share the first two components (e.g., /TV)
        try:
            src_root = Path(src.parts[0], src.parts[1]) if len(src.parts) > 1 else src
            dst_root = Path(dst.parts[0], dst.parts[1]) if len(dst.parts) > 1 else dst
            return src_root == dst_root
        except IndexError:
            return False


@dataclass
class MovePlan:
    """Complete plan for all file moves."""
    moves: list[PlannedMove] = field(default_factory=list)
    skipped: list[PlannedMove] = field(default_factory=list)  # Already correct
    issues: list[PlannedMove] = field(default_factory=list)  # Have warnings
    stats: dict = field(default_factory=dict)

    @property
    def total_moves(self) -> int:
        return len(self.moves)

    @property
    def total_skipped(self) -> int:
        return len(self.skipped)

    @property
    def same_root_moves(self) -> list[PlannedMove]:
        return [m for m in self.moves if m.same_root]


def _paths_equal(a: str, b: str) -> bool:
    """Compare two paths after resolving and normalizing."""
    try:
        return Path(a).resolve() == Path(b).resolve()
    except (OSError, ValueError):
        return os.path.normpath(os.path.abspath(a)) == os.path.normpath(os.path.abspath(b))


ASSOCIATED_EXTENSIONS = {
    ".srt", ".sub", ".ssa", ".ass", ".vtt",  # Subtitles
    ".nfo",  # Metadata
    ".jpg", ".jpeg", ".png",  # Artwork
    ".smi", ".idx",  # Other subtitle formats
}


def find_associated_files(episode_path: str) -> list[str]:
    """Find subtitle, nfo, and other companion files for an episode."""
    ep_path = Path(episode_path)
    if not ep_path.exists():
        return []

    stem = ep_path.stem
    parent = ep_path.parent
    associated = []

    for f in parent.iterdir():
        if f == ep_path:
            continue
        # Match files with same stem (e.g., "show S01E01.srt", "show S01E01.en.srt")
        if f.name.startswith(stem) and f.suffix.lower() in ASSOCIATED_EXTENSIONS:
            associated.append(str(f))

    return associated


def build_destination_path(episode: Episode, show: Show,
                           dest_root: str) -> str:
    """Build the proper destination path for an episode file."""
    season_folder = f"Season {episode.season_number:02d}"
    show_folder = show.folder_name
    filename = episode.filename
    return str(Path(dest_root) / show_folder / season_folder / filename)


def generate_plan(db: Database, kids_dest: str, adults_dest: str) -> MovePlan:
    """Generate a complete move plan for all classified shows.

    Args:
        db: Database instance with shows/episodes loaded
        kids_dest: Root destination for kids content (e.g., /tv-kids)
        adults_dest: Root destination for adult content (e.g., /TV)

    Returns:
        MovePlan with all moves, skips, and issues identified
    """
    plan = MovePlan()
    shows = db.get_all_shows()

    show_counts = {"kids": 0, "adults": 0, "skip": 0, "unclassified": 0}

    for show in shows:
        if show.classification == Classification.UNCLASSIFIED:
            show_counts["unclassified"] += 1
            continue
        if show.classification == Classification.SKIP:
            show_counts["skip"] += 1
            continue

        if show.classification == Classification.KIDS:
            dest_root = kids_dest
            show_counts["kids"] += 1
        else:
            dest_root = adults_dest
            show_counts["adults"] += 1

        episodes = db.get_episodes_for_show(show.jellyfin_id)

        for ep in episodes:
            if not ep.path:
                plan.issues.append(PlannedMove(
                    episode_id=ep.jellyfin_id,
                    show_id=show.jellyfin_id,
                    source="",
                    destination=build_destination_path(ep, show, dest_root),
                    show_name=show.name,
                    season_number=ep.season_number,
                    episode_number=ep.episode_number,
                    classification=show.classification.value,
                    issue="No file path from Jellyfin (missing file?)",
                ))
                continue

            dest = build_destination_path(ep, show, dest_root)
            move = PlannedMove(
                episode_id=ep.jellyfin_id,
                show_id=show.jellyfin_id,
                source=ep.path,
                destination=dest,
                show_name=show.name,
                season_number=ep.season_number,
                episode_number=ep.episode_number,
                classification=show.classification.value,
            )

            # Check for issues
            if move.already_correct:
                plan.skipped.append(move)
                continue

            if move.same_root:
                move.issue = "Source and destination share the same root directory"

            # Check for duplicate destinations
            existing_dests = {m.destination for m in plan.moves}
            if dest in existing_dests:
                move.issue = f"Duplicate destination (another episode maps here)"

            if move.issue:
                plan.issues.append(move)
            plan.moves.append(move)

            # Plan associated file moves
            for assoc_path in find_associated_files(ep.path):
                assoc_filename = Path(assoc_path).name
                # Preserve the suffix chain (e.g., .en.srt)
                ep_stem = Path(ep.path).stem
                assoc_suffix = Path(assoc_path).name[len(ep_stem):]
                new_ep_stem = Path(dest).stem
                assoc_dest = str(Path(dest).parent / f"{new_ep_stem}{assoc_suffix}")

                assoc_move = PlannedMove(
                    episode_id=ep.jellyfin_id,
                    show_id=show.jellyfin_id,
                    source=assoc_path,
                    destination=assoc_dest,
                    show_name=show.name,
                    season_number=ep.season_number,
                    episode_number=ep.episode_number,
                    classification=show.classification.value,
                    is_associated=True,
                )
                if assoc_move.already_correct:
                    plan.skipped.append(assoc_move)
                else:
                    plan.moves.append(assoc_move)

    plan.stats = {
        **show_counts,
        "total_moves": len(plan.moves),
        "total_skipped": len(plan.skipped),
        "total_issues": len(plan.issues),
        "same_root_moves": len(plan.same_root_moves),
    }

    logger.info("Plan generated: %d moves, %d skipped, %d issues",
                len(plan.moves), len(plan.skipped), len(plan.issues))
    return plan
