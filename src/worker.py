import json
from datetime import datetime, timedelta, timezone
from uuid import UUID

from functools import wraps
from flask import Flask, jsonify, Response, redirect, request, url_for, session
from werkzeug.exceptions import BadRequest
from workers import env, wsgi
from js import Object, Promise, fetch, setTimeout # type: ignore
from pyodide.ffi import run_sync, to_js
from authlib.integrations.flask_client import OAuth

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload
from sqlalchemy_cloudflare_d1 import create_engine_from_binding

from database import Card, FormatList, FormatListCard, ScryfallCardCache

app = Flask(__name__)
app.secret_key = env.FLASK_SECRET_KEY

oauth = OAuth(app)

def get_discord_client():
    env = request.environ["workers.env"]

    client = oauth.create_client("discord")
    if client is None:
        client = oauth.register(
            name="discord",
            client_id=env.DISCORD_CLIENT_ID,
            client_secret=env.DISCORD_CLIENT_SECRET,
            access_token_url="https://discord.com/api/oauth2/token",
            authorize_url="https://discord.com/api/oauth2/authorize",
            api_base_url="https://discord.com/api/",
            client_kwargs={"scope": "identify"}
        )
    return client

def admin_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        session_user = session.get("user")
        if not session_user or session_user.get("id") != request.environ["workers.env"].DISCORD_ADMIN_ID_0:
            return jsonify({"error": "Admin access required"}), 403
        return f(*args, **kwargs)
    return wrapper

def serve_asset(path, **template_context):
    assets = request.environ["workers.env"].ASSETS
    response = run_sync(
        assets.fetch(f"https://assets.local/{path}")
    )

    asset_response = Response(
        run_sync(response.bytes()),
        status=response.status,
        headers=response.headers
    )
    
    if response.status == 200 and path.lower().endswith((".html", ".htm")):
        template_source = asset_response.get_data(as_text=True)
        rendered_template = app.jinja_env.from_string(template_source)
        asset_response.set_data(rendered_template.render(**template_context))
        for header in ("Content-Encoding", "Content-MD5", "ETag"):
            asset_response.headers.pop(header, None)
        asset_response.headers["Cache-Control"] = "no-store"

    return asset_response

@app.route("/")
def index():
    return serve_asset("index.html", discord_username=session.get("user", {}).get("username"), discord_id=session.get("user", {}).get("id"))

@app.route("/rules")
def rules():
    return serve_asset("rules.html")

@app.route("/cards")
def cards():
    return serve_asset("cards.html", is_admin=session.get("user", {}).get("id") == request.environ["workers.env"].DISCORD_ADMIN_ID_0)

@app.route("/add_card")
@admin_required
def add_card():
    return serve_asset("add_card.html")

@app.route("/login")
def login():
    discord = get_discord_client()
    return discord.authorize_redirect(url_for("callback", _external=True))

@app.route("/logout")
def logout():
    session.pop("user", None)
    return jsonify({"message": "Logged out"})

@app.route("/callback")
def callback():
    discord = get_discord_client()
    token = discord.authorize_access_token()
    data = discord.get("users/@me", token=token).json()
    session["user"] = {
        "id": data["id"],
        "username": data["username"]
    }
    return redirect(url_for("index"))

# API

@app.route("/api/")
def api_home():
    return jsonify({
        "message": "Hello from Terraforming"
    })

def get_format_list(slug):
    env = request.environ["workers.env"]
    engine = create_engine_from_binding(env.terraforming_db)

    stmt = (
        select(FormatList)
        .where(FormatList.slug == slug)
        .options(
            selectinload(FormatList.cards)
            .selectinload(FormatListCard.card)
        )
    )

    with Session(engine) as session:
        format_list = session.scalar(stmt)
        if format_list is None:
            return jsonify({"error": f"{slug.title()} not found"}), 404

        result = {
            "name": format_list.name,
            "slug": format_list.slug,
            "cards": [
                {
                    "oracle_id": entry.card.oracle_id,
                    "reason": entry.reason,
                }
                for entry in format_list.cards
            ],
        }

    return jsonify(result)

@app.get("/api/lists/")
def get_lists():
    env = request.environ["workers.env"]
    engine = create_engine_from_binding(env.terraforming_db)

    with Session(engine) as session:
        format_lists = session.scalars(
            select(FormatList).order_by(FormatList.id)
        ).all()
        result = [
            {"name": format_list.name, "slug": format_list.slug}
            for format_list in format_lists
        ]

    return jsonify(result)

@app.post("/api/cards/")
def get_scryfall_cards():
    if not request.is_json:
        return jsonify({"error": "Content-Type must be application/json"}), 415

    try:
        payload = request.get_json()
    except BadRequest:
        return jsonify({"error": "Request body must contain valid JSON"}), 400

    if not isinstance(payload, dict) or set(payload) != {"oracle_ids"}:
        return jsonify({
            "error": "Request body must contain only an 'oracle_ids' array"
        }), 400

    oracle_ids = payload["oracle_ids"]
    if not isinstance(oracle_ids, list) or any(
        not isinstance(oracle_id, str) or not oracle_id.strip()
        for oracle_id in oracle_ids
    ):
        return jsonify({
            "error": "'oracle_ids' must be an array of non-empty strings"
        }), 400

    requested_ids = {}
    try:
        for oracle_id in oracle_ids:
            original_id = oracle_id.strip()
            requested_ids.setdefault(str(UUID(original_id)), original_id)
    except ValueError:
        return jsonify({"error": "'oracle_ids' must contain valid UUIDs"}), 400
    oracle_ids = list(requested_ids)

    if not oracle_ids:
        return jsonify({"cards": []})

    env = request.environ["workers.env"]
    engine = create_engine_from_binding(env.terraforming_db)
    cache_cutoff = datetime.now(timezone.utc) - timedelta(hours=24)

    with Session(engine) as session:
        known_ids = {
            str(UUID(card_id))
            for card_id in session.scalars(
                select(Card.oracle_id).where(
                    Card.oracle_id.in_(requested_ids.values())
                )
            ).all()
        }
        if known_ids != set(oracle_ids):
            return jsonify({
                "error": "Card details can only be requested for cards in the lists"
            }), 400

        cache_entries = session.scalars(
            select(ScryfallCardCache).where(
                ScryfallCardCache.oracle_id.in_(oracle_ids),
                ScryfallCardCache.cached_at >= cache_cutoff,
            )
        ).all()

    cards_by_id = {
        entry.oracle_id: {
            "name": entry.name,
            "image_uri": entry.image_uri,
            "scryfall_uri": entry.scryfall_uri,
        }
        for entry in cache_entries
    }
    missing_ids = [
        oracle_id for oracle_id in oracle_ids if oracle_id not in cards_by_id
    ]
    refreshed_at = datetime.now(timezone.utc)
    cache_updates = []

    for batch_start in range(0, len(missing_ids), 75):
        if batch_start:
            run_sync(Promise.new(
                lambda resolve, _reject: setTimeout(resolve, 500)
            ))

        batch_ids = missing_ids[batch_start:batch_start + 75]
        response = run_sync(fetch(
            "https://api.scryfall.com/cards/collection",
            to_js({
                "method": "POST",
                "headers": {
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                    "User-Agent": "TerraformingMTG/0.1.0",
                },
                "body": json.dumps({
                    "identifiers": [
                        {"oracle_id": oracle_id} for oracle_id in batch_ids
                    ]
                }),
            }, dict_converter=Object.fromEntries),
        ))
        if not response.ok:
            return jsonify({
                "error": "Scryfall card lookup failed",
                "upstream_status": response.status,
            }), 502

        result = json.loads(run_sync(response.text()))
        batch_cards = {oracle_id: None for oracle_id in batch_ids}
        for scryfall_card in result["data"]:
            oracle_id = str(UUID(scryfall_card["oracle_id"]))
            image_uris = scryfall_card.get("image_uris") or {}
            if not image_uris:
                card_faces = scryfall_card.get("card_faces") or []
                if card_faces:
                    image_uris = card_faces[0].get("image_uris") or {}

            batch_cards[oracle_id] = { # type: ignore
                "name": scryfall_card["name"],
                "image_uri": image_uris.get("png"),
                "scryfall_uri": scryfall_card.get("scryfall_uri"),
            }

        for oracle_id, card_data in batch_cards.items():
            if card_data is None:
                card_data = {
                    "name": None,
                    "image_uri": None,
                    "scryfall_uri": None,
                }
            cards_by_id[oracle_id] = card_data # type: ignore
            cache_updates.append(ScryfallCardCache(
                oracle_id=oracle_id,
                name=card_data["name"],
                image_uri=card_data["image_uri"],
                scryfall_uri=card_data["scryfall_uri"],
                cached_at=refreshed_at,
            ))

    if cache_updates:
        with Session(engine) as session:
            for entry in cache_updates:
                session.merge(entry)
            session.commit()

    return jsonify({
        "cards": [
            {
                "oracle_id": requested_ids[oracle_id],
                **cards_by_id[oracle_id],
            }
            for oracle_id in oracle_ids
        ]
    })

@app.get("/api/lists/<string:slug>/")
def get_list(slug):
    return get_format_list(slug)

@app.post("/api/lists/<string:slug>/")
@admin_required
def add_card_to_list(slug):
    if not request.is_json:
        return jsonify({"error": "Content-Type must be application/json"}), 415

    try:
        payload = request.get_json()
    except BadRequest:
        return jsonify({"error": "Request body must contain valid JSON"}), 400

    if not isinstance(payload, dict):
        return jsonify({"error": "Request body must be a JSON object"}), 400

    allowed_fields = {"oracle_id", "reason"}
    if unknown_fields := payload.keys() - allowed_fields:
        return jsonify({
            "error": f"Unknown field(s): {', '.join(sorted(unknown_fields))}"
        }), 400

    oracle_id = payload.get("oracle_id")
    if not isinstance(oracle_id, str) or not oracle_id.strip():
        return jsonify({
            "error": "'oracle_id' must be a non-empty string"
        }), 400

    try:
        oracle_id = str(UUID(oracle_id.strip()))
    except ValueError:
        return jsonify({"error": "'oracle_id' must be a valid UUID"}), 400

    reason = payload.get("reason")
    if reason is not None and not isinstance(reason, str):
        return jsonify({
            "error": "'reason' must be a string or null"
        }), 400

    env = request.environ["workers.env"]
    engine = create_engine_from_binding(env.terraforming_db)

    try:
        with Session(engine) as session:
            format_list = session.scalar(
                select(FormatList).where(FormatList.slug == slug)
            )
            if format_list is None:
                return jsonify({"error": f"{slug.title()} not found"}), 404

            if session.get(
                FormatListCard, (format_list.id, oracle_id)
            ) is not None:
                return jsonify({
                    "error": f"Card is already on the {format_list.name}"
                }), 409

            card = session.get(Card, oracle_id)
            if card is None:
                card = Card(oracle_id=oracle_id)
                session.add(card)

            session.add(FormatListCard(
                list_id=format_list.id,
                oracle_id=oracle_id,
                reason=reason,
            ))
            result = {
                "oracle_id": card.oracle_id,
                "reason": reason,
            }
            session.commit()
    except IntegrityError:
        return jsonify({
            "error": "The card could not be added because of a conflicting database entry"
        }), 409

    return jsonify(result), 201

@app.delete("/api/lists/<string:slug>/<string:oracle_id>/")
@admin_required
def remove_card_from_list(slug, oracle_id):
    try:
        oracle_id = str(UUID(oracle_id.strip()))
    except ValueError:
        return jsonify({"error": "Invalid oracle_id"}), 400

    env = request.environ["workers.env"]
    engine = create_engine_from_binding(env.terraforming_db)

    with Session(engine) as session:
        format_list = session.scalar(
            select(FormatList).where(FormatList.slug == slug)
        )
        if format_list is None:
            return jsonify({"error": f"{slug.title()} not found"}), 404

        entry = session.get(FormatListCard, (format_list.id, oracle_id))
        if entry is None:
            return jsonify({
                "error": f"Card is not on the {format_list.name}"
            }), 404

        session.delete(entry)
        session.commit()

    return "", 204

@app.get("/api/banlist/")
@admin_required
def get_banlist():
    return get_format_list("banlist")

@app.post("/api/banlist/")
@admin_required
def add_banlist_card():
    if not request.is_json:
        return jsonify({"error": "Content-Type must be application/json"}), 415

    try:
        payload = request.get_json()
    except BadRequest:
        return jsonify({"error": "Request body must contain valid JSON"}), 400

    if not isinstance(payload, dict):
        return jsonify({"error": "Request body must be a JSON object"}), 400

    allowed_fields = {
        "oracle_id",
        "reason",
    }
    if unknown_fields := payload.keys() - allowed_fields:
        return jsonify({
            "error": f"Unknown field(s): {', '.join(sorted(unknown_fields))}"
        }), 400

    oracle_id = payload.get("oracle_id")
    if not isinstance(oracle_id, str) or not oracle_id.strip():
        return jsonify({
            "error": "'oracle_id' must be a non-empty string"
        }), 400

    oracle_id = oracle_id.strip()
    if len(oracle_id) > 36:
        return jsonify({
            "error": "'oracle_id' must be at most 36 characters"
        }), 400

    reason = payload.get("reason")
    if reason is not None and not isinstance(reason, str):
        return jsonify({
            "error": "'reason' must be a string or null"
        }), 400

    env = request.environ["workers.env"]
    engine = create_engine_from_binding(env.terraforming_db)

    try:
        with Session(engine) as session:
            banlist = session.scalar(
                select(FormatList).where(FormatList.slug == "banlist")
            )
            if banlist is None:
                return jsonify({"error": "Banlist not found"}), 404

            existing_entry = session.get(
                FormatListCard, (banlist.id, oracle_id)
            )
            if existing_entry is not None:
                return jsonify({"error": "Card is already on the banlist"}), 409

            card = session.get(Card, oracle_id)
            if card is None:
                card = Card(oracle_id=oracle_id)
                session.add(card)

            session.add(FormatListCard(
                list_id=banlist.id,
                oracle_id=oracle_id,
                reason=reason,
            ))
            result = {
                "oracle_id": card.oracle_id,
                "reason": reason,
            }
            session.commit()
    except IntegrityError:
        return jsonify({
            "error": "The card could not be added because of a conflicting database entry"
        }), 409

    return jsonify(result), 201

@app.delete("/api/banlist/<string:oracle_id>/")
@admin_required
def remove_banlist_card(oracle_id):
    oracle_id = oracle_id.strip()
    if not oracle_id or len(oracle_id) > 36:
        return jsonify({"error": "Invalid oracle_id"}), 400

    env = request.environ["workers.env"]
    engine = create_engine_from_binding(env.terraforming_db)

    with Session(engine) as session:
        banlist = session.scalar(
            select(FormatList).where(FormatList.slug == "banlist")
        )
        if banlist is None:
            return jsonify({"error": "Banlist not found"}), 404

        entry = session.get(FormatListCard, (banlist.id, oracle_id))
        if entry is None:
            return jsonify({"error": "Card is not on the banlist"}), 404

        session.delete(entry)
        session.commit()

    return "", 204

@app.get("/api/restricted/")
def get_restricted():
    return get_format_list("restricted")

@app.post("/api/restricted/")
@admin_required
def add_restricted_card():
    if not request.is_json:
        return jsonify({"error": "Content-Type must be application/json"}), 415

    try:
        payload = request.get_json()
    except BadRequest:
        return jsonify({"error": "Request body must contain valid JSON"}), 400

    if not isinstance(payload, dict):
        return jsonify({"error": "Request body must be a JSON object"}), 400

    allowed_fields = {
        "oracle_id",
        "reason",
    }
    if unknown_fields := payload.keys() - allowed_fields:
        return jsonify({
            "error": f"Unknown field(s): {', '.join(sorted(unknown_fields))}"
        }), 400

    oracle_id = payload.get("oracle_id")
    if not isinstance(oracle_id, str) or not oracle_id.strip():
        return jsonify({
            "error": "'oracle_id' must be a non-empty string"
        }), 400

    oracle_id = oracle_id.strip()
    if len(oracle_id) > 36:
        return jsonify({
            "error": "'oracle_id' must be at most 36 characters"
        }), 400

    reason = payload.get("reason")
    if reason is not None and not isinstance(reason, str):
        return jsonify({
            "error": "'reason' must be a string or null"
        }), 400

    env = request.environ["workers.env"]
    engine = create_engine_from_binding(env.terraforming_db)

    try:
        with Session(engine) as session:
            restricted_list = session.scalar(
                select(FormatList).where(FormatList.slug == "restricted")
            )
            if restricted_list is None:
                return jsonify({"error": "Restricted list not found"}), 404

            existing_entry = session.get(
                FormatListCard, (restricted_list.id, oracle_id)
            )
            if existing_entry is not None:
                return jsonify({"error": "Card is already on the restricted list"}), 409

            card = session.get(Card, oracle_id)
            if card is None:
                card = Card(oracle_id=oracle_id)
                session.add(card)

            session.add(FormatListCard(
                list_id=restricted_list.id,
                oracle_id=oracle_id,
                reason=reason,
            ))
            result = {
                "oracle_id": card.oracle_id,
                "reason": reason,
            }
            session.commit()
    except IntegrityError:
        return jsonify({
            "error": "The card could not be added because of a conflicting database entry"
        }), 409

    return jsonify(result), 201

@app.delete("/api/restricted/<string:oracle_id>/")
@admin_required
def remove_restricted_card(oracle_id):
    oracle_id = oracle_id.strip()
    if not oracle_id or len(oracle_id) > 36:
        return jsonify({"error": "Invalid oracle_id"}), 400

    env = request.environ["workers.env"]
    engine = create_engine_from_binding(env.terraforming_db)

    with Session(engine) as session:
        restricted_list = session.scalar(
            select(FormatList).where(FormatList.slug == "restricted")
        )
        if restricted_list is None:
            return jsonify({"error": "Restricted list not found"}), 404

        entry = session.get(FormatListCard, (restricted_list.id, oracle_id))
        if entry is None:
            return jsonify({"error": "Card is not on the restricted list"}), 404

        session.delete(entry)
        session.commit()

    return "", 204

@app.route("/<path:path>")
def frontend(path):
    return serve_asset(path)

Default = wsgi.entrypoint(app)
