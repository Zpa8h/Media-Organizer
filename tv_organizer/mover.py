"""File mover with safety checks, logging, and same-directory handling."""

import logging
import os
import shutil
import tempfile
from pathlib import Path

from .models import Database
from .planner import MovePlan, PlannedMove

logger = logging.getLogger(__name__)


class MoveError(Exception):
    """Raised when a file move fails."""


def _ensure_dir(path: str):
    """Create parent directories if they don't exist."""
    Path(path).parent.mkdir(parents=True, exist_ok=True)


def _check_disk_space(source: str, dest: str) -> bool:
    """Check if destination has enough space for the file."""
    try:
        file_size = os.path.getsize(source)
        dest_stat = os.statvfs(Path(dest).parent if Path(dest).parent.exists()
                               else Path(dest).parent.parent)
        free_space = dest_stat.f_bavail * dest_stat.f_frsize
        # Require at least the file size + 100MB headroom
        return free_space > file_size + (100 * 1024 * 1024)
    except OSError:
        return True  # If we can't check, proceed anyway


def _safe_move(source: str, destination: str, use_staging: bool = False,
               staging_dir: str = "") -> str:
    """Move a file safely, using staging area if source and dest overlap.

    Args:
        source: Current file path
        destination: Target file path
        use_staging: Whether to use a temporary staging location
        staging_dir: Directory for staging (temp dir if not specified)

    Returns:
        The final destination path
    """
    src_path = Path(source).resolve()
    dst_path = Path(destination).resolve()

    if src_path == dst_path:
        logger.debug("Source and destination are identical, skipping: %s", source)
        return str(dst_path)

    if not src_path.exists():
        raise MoveError(f"Source file does not exist: {source}")

    if dst_path.exists():
        if src_path.samefile(dst_path):
            logger.debug("Source and destination are the same file: %s", source)
            return str(dst_path)
        raise MoveError(f"Destination already exists: {destination}")

    _ensure_dir(str(dst_path))

    if use_staging:
        # Two-phase move: source → staging → destination
        if not staging_dir:
            staging_dir = tempfile.mkdtemp(prefix="tv_organizer_staging_")
        staging_path = Path(staging_dir) / src_path.name

        logger.debug("Staging move: %s → %s → %s", src_path, staging_path, dst_path)

        # Phase 1: Move to staging
        shutil.move(str(src_path), str(staging_path))

        # Phase 2: Move from staging to final destination
        try:
            shutil.move(str(staging_path), str(dst_path))
        except Exception:
            # Rollback: try to move back from staging to source
            logger.error("Failed to move from staging to destination, rolling back")
            try:
                shutil.move(str(staging_path), str(src_path))
            except Exception as rollback_err:
                logger.critical("ROLLBACK FAILED! File stranded at: %s (error: %s)",
                                staging_path, rollback_err)
            raise
    else:
        logger.debug("Direct move: %s → %s", src_path, dst_path)
        shutil.move(str(src_path), str(dst_path))

    return str(dst_path)


def execute_plan(plan: MovePlan, db: Database, dry_run: bool = True,
                 staging_dir: str = "") -> dict:
    """Execute the move plan.

    Args:
        plan: The MovePlan to execute
        db: Database for logging moves
        dry_run: If True, only simulate moves (no files touched)
        staging_dir: Optional staging directory for same-root moves

    Returns:
        Summary dict with counts
    """
    results = {
        "completed": 0,
        "failed": 0,
        "skipped": 0,
        "errors": [],
    }

    if dry_run:
        logger.info("DRY RUN - no files will be moved")

    # Sort moves: process non-overlapping moves first, then same-root moves
    normal_moves = [m for m in plan.moves if not m.same_root]
    same_root_moves = [m for m in plan.moves if m.same_root]

    # Create staging dir for same-root moves if needed
    active_staging_dir = staging_dir
    if same_root_moves and not dry_run and not active_staging_dir:
        active_staging_dir = tempfile.mkdtemp(prefix="tv_organizer_staging_")
        logger.info("Created staging directory: %s", active_staging_dir)

    all_moves = normal_moves + same_root_moves

    for move in all_moves:
        move_id = None
        try:
            # Log planned move
            move_id = db.log_move(
                source=move.source,
                dest=move.destination,
                show_id=move.show_id,
                episode_id=move.episode_id,
                status="dry_run" if dry_run else "planned",
            )

            if dry_run:
                results["completed"] += 1
                db.update_move_status(move_id, "dry_run_ok")
                continue

            # Pre-flight checks
            if not os.path.exists(move.source):
                raise MoveError(f"Source file missing: {move.source}")

            if not _check_disk_space(move.source, move.destination):
                raise MoveError(f"Insufficient disk space for: {move.source}")

            # Execute the move
            use_staging = move.same_root
            _safe_move(
                source=move.source,
                destination=move.destination,
                use_staging=use_staging,
                staging_dir=active_staging_dir if use_staging else "",
            )

            db.update_move_status(move_id, "completed")
            results["completed"] += 1
            logger.info("Moved: %s → %s", move.source, move.destination)

        except MoveError as e:
            results["failed"] += 1
            results["errors"].append({"move": move.source, "error": str(e)})
            if move_id:
                db.update_move_status(move_id, f"failed: {e}")
            logger.error("Failed to move %s: %s", move.source, e)

        except Exception as e:
            results["failed"] += 1
            results["errors"].append({"move": move.source, "error": str(e)})
            if move_id:
                db.update_move_status(move_id, f"error: {e}")
            logger.error("Unexpected error moving %s: %s", move.source, e)

    # Clean up staging directory
    if active_staging_dir and not staging_dir and not dry_run:
        try:
            os.rmdir(active_staging_dir)
            logger.info("Removed staging directory: %s", active_staging_dir)
        except OSError:
            logger.warning("Could not remove staging directory (not empty?): %s",
                           active_staging_dir)

    # Clean up empty source directories
    if not dry_run:
        _cleanup_empty_dirs(plan)

    logger.info("Execution complete: %d moved, %d failed, %d skipped",
                results["completed"], results["failed"], results["skipped"])
    return results


def _cleanup_empty_dirs(plan: MovePlan):
    """Remove empty directories left behind after moves."""
    dirs_to_check = set()
    for move in plan.moves:
        if move.source:
            dirs_to_check.add(str(Path(move.source).parent))

    for dir_path in sorted(dirs_to_check, key=len, reverse=True):
        try:
            p = Path(dir_path)
            if p.exists() and p.is_dir() and not any(p.iterdir()):
                p.rmdir()
                logger.info("Removed empty directory: %s", dir_path)
        except OSError as e:
            logger.debug("Could not remove directory %s: %s", dir_path, e)
