"""Flask web application for TV library organization."""

import json
import logging
import os

from flask import Flask, jsonify, redirect, render_template, request, url_for

from .classifier import auto_classify_all, auto_suggest
from .config import Config
from .jellyfin_client import JellyfinClient, JellyfinError
from .models import Classification, Database
from .mover import execute_plan
from .planner import generate_plan
from .sync import sync_library

logger = logging.getLogger(__name__)


def create_app(config: Config = None) -> Flask:
    """Create and configure the Flask application."""
    app = Flask(__name__, template_folder=os.path.join(os.path.dirname(__file__), "..", "templates"))

    if config is None:
        config = Config.from_env()

    app.config["ORGANIZER_CONFIG"] = config
    app.secret_key = os.environ.get("SECRET_KEY", "tv-organizer-dev-key")

    def get_db() -> Database:
        return Database(config.db_path)

    def get_client() -> JellyfinClient:
        return JellyfinClient(config.jellyfin_url, config.jellyfin_api_key, config.jellyfin_user_id)

    # ── Dashboard ──────────────────────────────────────────────────────

    @app.route("/")
    def index():
        db = get_db()
        stats = db.get_classification_stats()
        last_sync = db.get_cache_meta("last_sync") or "Never"
        errors = config.validate()
        return render_template("index.html", stats=stats, last_sync=last_sync,
                               config=config, errors=errors)

    # ── Jellyfin Connection ────────────────────────────────────────────

    @app.route("/api/test-connection")
    def test_connection():
        try:
            client = get_client()
            info = client.test_connection()
            return jsonify({"ok": True, "server": info.get("ServerName"),
                            "version": info.get("Version")})
        except JellyfinError as e:
            return jsonify({"ok": False, "error": str(e)}), 400

    @app.route("/api/sync", methods=["POST"])
    def sync():
        try:
            client = get_client()
            db = get_db()
            stats = sync_library(client, db)
            return jsonify({"ok": True, "stats": stats})
        except JellyfinError as e:
            return jsonify({"ok": False, "error": str(e)}), 400

    # ── Shows / Classification ─────────────────────────────────────────

    @app.route("/shows")
    def shows():
        db = get_db()
        all_shows = db.get_all_shows()
        stats = db.get_classification_stats()

        # Apply filters
        q = request.args.get("q", "").strip().lower()
        classification_filter = request.args.get("classification", "")
        rating_filter = request.args.get("rating", "")

        filtered = all_shows
        if q:
            filtered = [s for s in filtered if q in s.name.lower()]
        if classification_filter:
            filtered = [s for s in filtered if s.classification.value == classification_filter]
        if rating_filter:
            filtered = [s for s in filtered if s.official_rating == rating_filter]

        # Collect all unique ratings and genres for filter dropdowns
        all_ratings = sorted({s.official_rating for s in all_shows if s.official_rating})
        all_genres = sorted({g for s in all_shows for g in s.genres})

        # Auto-suggestions for unclassified shows
        suggestions = {}
        for show in filtered:
            if show.classification == Classification.UNCLASSIFIED:
                suggestion = auto_suggest(show)
                if suggestion:
                    suggestions[show.jellyfin_id] = suggestion.value

        return render_template("shows.html", shows=filtered, stats=stats,
                               suggestions=suggestions, all_ratings=all_ratings,
                               all_genres=all_genres, q=q,
                               classification_filter=classification_filter,
                               rating_filter=rating_filter, config=config)

    @app.route("/api/classify", methods=["POST"])
    def classify():
        db = get_db()
        data = request.get_json()
        show_id = data.get("show_id")
        classification = data.get("classification")

        if not show_id or not classification:
            return jsonify({"ok": False, "error": "Missing show_id or classification"}), 400

        try:
            cls = Classification(classification)
        except ValueError:
            return jsonify({"ok": False, "error": f"Invalid classification: {classification}"}), 400

        db.set_classification(show_id, cls)
        return jsonify({"ok": True})

    @app.route("/api/classify-bulk", methods=["POST"])
    def classify_bulk():
        db = get_db()
        data = request.get_json()
        show_ids = data.get("show_ids", [])
        classification = data.get("classification")

        if not show_ids or not classification:
            return jsonify({"ok": False, "error": "Missing show_ids or classification"}), 400

        try:
            cls = Classification(classification)
        except ValueError:
            return jsonify({"ok": False, "error": f"Invalid classification: {classification}"}), 400

        db.bulk_set_classification(show_ids, cls)
        return jsonify({"ok": True, "count": len(show_ids)})

    @app.route("/api/auto-classify", methods=["POST"])
    def auto_classify():
        db = get_db()
        counts = auto_classify_all(db)
        return jsonify({"ok": True, "counts": counts})

    @app.route("/api/classify-by-rating", methods=["POST"])
    def classify_by_rating():
        db = get_db()
        data = request.get_json()
        ratings = data.get("ratings", [])
        classification = data.get("classification")

        if not ratings or not classification:
            return jsonify({"ok": False, "error": "Missing ratings or classification"}), 400

        try:
            cls = Classification(classification)
        except ValueError:
            return jsonify({"ok": False, "error": f"Invalid classification: {classification}"}), 400

        count = db.classify_by_rating(ratings, cls)
        return jsonify({"ok": True, "count": count})

    @app.route("/api/classify-by-genre", methods=["POST"])
    def classify_by_genre():
        db = get_db()
        data = request.get_json()
        genre = data.get("genre")
        classification = data.get("classification")

        if not genre or not classification:
            return jsonify({"ok": False, "error": "Missing genre or classification"}), 400

        try:
            cls = Classification(classification)
        except ValueError:
            return jsonify({"ok": False, "error": f"Invalid classification: {classification}"}), 400

        count = db.classify_by_genre(genre, cls)
        return jsonify({"ok": True, "count": count})

    # ── Show detail with episodes ──────────────────────────────────────

    @app.route("/shows/<show_id>")
    def show_detail(show_id):
        db = get_db()
        show = db.get_show(show_id)
        if not show:
            return "Show not found", 404
        episodes = db.get_episodes_for_show(show_id)
        suggestion = auto_suggest(show) if show.classification == Classification.UNCLASSIFIED else None
        return render_template("show_detail.html", show=show, episodes=episodes,
                               suggestion=suggestion, config=config)

    # ── Preview ────────────────────────────────────────────────────────

    @app.route("/preview")
    def preview():
        db = get_db()
        stats = db.get_classification_stats()
        plan = generate_plan(db, config.kids_dest, config.adults_dest)
        return render_template("preview.html", plan=plan, stats=stats, config=config)

    # ── Execute ────────────────────────────────────────────────────────

    @app.route("/execute", methods=["POST"])
    def execute():
        db = get_db()
        plan = generate_plan(db, config.kids_dest, config.adults_dest)

        dry_run = request.form.get("dry_run", "true") == "true"
        results = execute_plan(plan, db, dry_run=dry_run)

        return render_template("results.html", results=results, dry_run=dry_run, config=config)

    @app.route("/api/trigger-scan", methods=["POST"])
    def trigger_scan():
        try:
            client = get_client()
            client.trigger_library_scan()
            return jsonify({"ok": True})
        except JellyfinError as e:
            return jsonify({"ok": False, "error": str(e)}), 400

    # ── Move log ───────────────────────────────────────────────────────

    @app.route("/log")
    def move_log():
        db = get_db()
        log = db.get_move_log()
        return render_template("log.html", log=log, config=config)

    # ── Jellyfin image proxy ───────────────────────────────────────────

    @app.route("/api/image/<item_id>")
    def image_proxy(item_id):
        client = get_client()
        image_type = request.args.get("type", "Primary")
        max_width = request.args.get("maxWidth", "300")
        url = client.get_image_url(item_id, image_type, int(max_width))
        return redirect(url)

    return app
