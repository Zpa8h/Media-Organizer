#!/usr/bin/env python3
"""Entry point for the TV Library Organizer."""

import argparse
import logging
import sys

from tv_organizer.config import Config


def setup_logging(verbose: bool = False):
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def cmd_serve(args, config: Config):
    """Start the web UI."""
    from tv_organizer.app import create_app
    app = create_app(config)
    print(f"Starting TV Organizer on http://{config.host}:{config.port}")
    app.run(host=config.host, port=config.port, debug=config.debug)


def cmd_sync(args, config: Config):
    """Sync library data from Jellyfin."""
    from tv_organizer.jellyfin_client import JellyfinClient
    from tv_organizer.models import Database
    from tv_organizer.sync import sync_library

    client = JellyfinClient(config.jellyfin_url, config.jellyfin_api_key, config.jellyfin_user_id)
    db = Database(config.db_path)

    print("Testing connection...")
    info = client.test_connection()
    print(f"Connected to {info.get('ServerName')} (v{info.get('Version')})")

    print("Syncing library...")
    stats = sync_library(client, db)
    print(f"Done: {stats['shows']} shows, {stats['seasons']} seasons, {stats['episodes']} episodes")


def cmd_auto_classify(args, config: Config):
    """Run auto-classification."""
    from tv_organizer.classifier import auto_classify_all
    from tv_organizer.models import Database

    db = Database(config.db_path)
    counts = auto_classify_all(db)
    print(f"Auto-classified: {counts['kids']} kids, {counts['adults']} adults, {counts['uncertain']} uncertain")


def cmd_preview(args, config: Config):
    """Preview planned moves."""
    from tv_organizer.models import Database
    from tv_organizer.planner import generate_plan

    db = Database(config.db_path)
    plan = generate_plan(db, config.kids_dest, config.adults_dest)

    print(f"\nMove Plan Summary:")
    print(f"  Files to move:      {plan.total_moves}")
    print(f"  Already correct:    {plan.total_skipped}")
    print(f"  Same-root moves:    {len(plan.same_root_moves)}")
    print(f"  Issues:             {len(plan.issues)}")

    if plan.issues:
        print(f"\nIssues:")
        for m in plan.issues[:20]:
            print(f"  [{m.show_name}] S{m.season_number:02d}E{m.episode_number:02d}: {m.issue}")

    if args.verbose and plan.moves:
        print(f"\nPlanned moves:")
        for m in plan.moves:
            tag = "[companion] " if m.is_associated else ""
            print(f"  {tag}{m.source}")
            print(f"    -> {m.destination}")


def cmd_execute(args, config: Config):
    """Execute the move plan."""
    from tv_organizer.models import Database
    from tv_organizer.mover import execute_plan
    from tv_organizer.planner import generate_plan

    db = Database(config.db_path)
    plan = generate_plan(db, config.kids_dest, config.adults_dest)

    dry_run = not args.for_real

    if dry_run:
        print("DRY RUN MODE (use --for-real to actually move files)")
    else:
        print(f"EXECUTING: {plan.total_moves} file moves")
        confirm = input("Type 'yes' to confirm: ")
        if confirm.strip().lower() != "yes":
            print("Aborted.")
            return

    results = execute_plan(plan, db, dry_run=dry_run)
    print(f"\nResults: {results['completed']} completed, {results['failed']} failed, {results['skipped']} skipped")

    if results["errors"]:
        print(f"\nErrors:")
        for err in results["errors"]:
            print(f"  {err['move']}: {err['error']}")


def cmd_test(args, config: Config):
    """Test Jellyfin connection."""
    from tv_organizer.jellyfin_client import JellyfinClient

    client = JellyfinClient(config.jellyfin_url, config.jellyfin_api_key, config.jellyfin_user_id)
    print(f"Connecting to {config.jellyfin_url}...")

    info = client.test_connection()
    print(f"Server: {info.get('ServerName')}")
    print(f"Version: {info.get('Version')}")

    uid = client.get_user_id()
    print(f"User ID: {uid}")

    lib_id = client.get_tv_library_id()
    print(f"TV Library ID: {lib_id}")

    shows = client.get_all_shows(lib_id)
    print(f"\nFound {len(shows)} TV shows:")
    for show in shows[:20]:
        year = show.get("ProductionYear", "")
        rating = show.get("OfficialRating", "")
        genres = ", ".join(show.get("Genres", []))
        ep_count = show.get("ChildCount", "?")
        print(f"  {show['Name']} ({year}) [{rating}] - {genres} - {ep_count} seasons")

    if len(shows) > 20:
        print(f"  ... and {len(shows) - 20} more")


def main():
    # Try loading .env file
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass

    parser = argparse.ArgumentParser(description="TV Library Organizer using Jellyfin")
    parser.add_argument("-v", "--verbose", action="store_true", help="Verbose output")
    parser.add_argument("--db", help="Database path (default: organizer.db)")
    parser.add_argument("--jellyfin-url", help="Jellyfin server URL")
    parser.add_argument("--api-key", help="Jellyfin API key")
    parser.add_argument("--kids-dest", help="Destination for kids content")
    parser.add_argument("--adults-dest", help="Destination for adult content")

    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    subparsers.add_parser("serve", help="Start the web UI")
    subparsers.add_parser("test", help="Test Jellyfin connection and list shows")
    subparsers.add_parser("sync", help="Sync library from Jellyfin")
    subparsers.add_parser("auto-classify", help="Auto-classify shows by rating/genre")

    preview_parser = subparsers.add_parser("preview", help="Preview planned moves")
    preview_parser.add_argument("-v", "--verbose", action="store_true", dest="verbose")

    exec_parser = subparsers.add_parser("execute", help="Execute file moves")
    exec_parser.add_argument("--for-real", action="store_true",
                             help="Actually move files (default is dry run)")

    args = parser.parse_args()
    setup_logging(args.verbose)

    config = Config.from_env()
    # CLI overrides
    if args.db:
        config.db_path = args.db
    if args.jellyfin_url:
        config.jellyfin_url = args.jellyfin_url
    if args.api_key:
        config.jellyfin_api_key = args.api_key
    if args.kids_dest:
        config.kids_dest = args.kids_dest
    if args.adults_dest:
        config.adults_dest = args.adults_dest

    commands = {
        "serve": cmd_serve,
        "test": cmd_test,
        "sync": cmd_sync,
        "auto-classify": cmd_auto_classify,
        "preview": cmd_preview,
        "execute": cmd_execute,
    }

    if args.command in commands:
        errors = config.validate()
        if errors and args.command != "serve":
            print("Configuration errors:")
            for e in errors:
                print(f"  - {e}")
            sys.exit(1)
        commands[args.command](args, config)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
