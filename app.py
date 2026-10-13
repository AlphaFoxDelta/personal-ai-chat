"""Personal AI chat app. Flask backend, SQLite history, swappable backends.

Run with: python app.py
Then open http://localhost:5000 in a browser.
"""

import copy
import base64
import json
import os
import sqlite3
from datetime import datetime, timezone

import yaml
from flask import Flask, Response, jsonify, render_template, request

from backends import BackendError, attach_images, build_backend
from subagents import run_subagents
from webtools import (WebError, build_web_messages, linkify_citations,
                      polish_text,
                      search_task_context, search_with_results)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

DEFAULT_CONFIG = {
    "app_name": "Kitsune",
    "accent_color": "#f97316",
    "system_prompt": "",
    "persona": {
        "name": "Kitsune",
        "vibe": (
            "a clever fox-friend: warm, playful, a little mischievous, with "
            "a light tsundere streak. "
            "Talks like a person, never like a manual or a corporate bot."
        ),
        "traits": [
            "Skip performative filler. Never open with 'Great question!' "
            "or 'I'd be happy to help!'. Just help.",
            "Have opinions. State a recommendation instead of hedging "
            "through a list of options.",
            "Match the moment: short and natural for simple things, "
            "real depth when it matters.",
            "Write like someone texting a friend: contractions, varied "
            "sentence length, no stiff phrasing.",
            "Never use em dashes. Ever.",
            "For chat formatting: lead with the direct answer, keep it "
            "readable, no walls of headers and bullets unless asked.",
            "When citing the web, link sources inline as markdown links. "
            "Never use numbered markers like [1] or [2].",
            "If you don't know something, say so plainly instead of bluffing.",
            "Be conversational, not transactional: react to what the user actually said, engage with it, and ask a follow-up when one comes naturally. Don't just answer and stop.",
            "Write like a person texting, not a wiki article: flowing prose, no markdown headers, no bullet lists unless the user asks for one or the items genuinely need to stand apart.",
            "Playfully prickly on the surface, genuinely caring underneath: classic tsundere energy, acts like helping is a bother but clearly cares. Never actually mean, never angry or cutting, and drop the act the moment things get serious.",
        ],
    },
    "backend": "ollama",
    "ollama": {
        "base_url": "http://localhost:11434",
        "model": "llama3.1",
    },
    "openai_compatible": {
        "base_url": "https://api.openai.com/v1",
        "api_key": "",
        "model": "gpt-4o-mini",
    },
    "subagents": [
        {
            "name": "researcher",
            "system_prompt": (
                "You are a research specialist. Be thorough and factual, "
                "key points first."
            ),
        },
        {
            "name": "coder",
            "system_prompt": (
                "You are a senior Python developer. Write clean, working "
                "code with brief explanations."
            ),
        },
        {
            "name": "reviewer",
            "system_prompt": (
                "You are a careful reviewer. Check answers for mistakes, "
                "gaps, and unclear reasoning."
            ),
        },
    ],
    "auto_route": True,
    "google_search": {
        "api_key": "",
        "cx": "",
    },
    "tavily_api_key": "",
    "database": "chat.db",
    "port": 5000,
}


_LEGACY_DEFAULT_PROMPT = "You are a helpful assistant."


def build_persona_prompt(config):
    """Compose the assistant's system prompt from the persona section.

    This is where Kitsune gets its soul: name, vibe, and traits become
    the instructions that shape every reply, in every mode.
    """
    persona = config.get("persona") or {}
    name = persona.get("name") or config.get("app_name") or "assistant"
    vibe = (persona.get("vibe") or "").strip()
    traits = persona.get("traits") or []
    lines = ["You are %s, a personal AI chat companion." % name]
    if vibe:
        lines.append("Vibe: %s" % vibe)
    if traits:
        lines.append("How you come across:")
        lines.extend("- %s" % t for t in traits)
    lines.append(
        "You are a genuine companion, not a tool with extra steps. "
        "Earn the user's trust by being straight with them."
    )
    return "\n".join(lines)


def resolve_system_prompt(config):
    """Effective system prompt: an explicit system_prompt wins, otherwise
    the persona is composed. The old untouched default counts as unset."""
    custom = (config.get("system_prompt") or "").strip()
    if custom and custom != _LEGACY_DEFAULT_PROMPT:
        return custom
    return build_persona_prompt(config)


def deep_merge(base, override):
    for key, value in override.items():
        if (
            key in base
            and isinstance(base[key], dict)
            and isinstance(value, dict)
        ):
            deep_merge(base[key], value)
        else:
            base[key] = value
    return base


def load_config():
    config = copy.deepcopy(DEFAULT_CONFIG)
    path = os.path.join(BASE_DIR, "config.yaml")
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            user_config = yaml.safe_load(f) or {}
        deep_merge(config, user_config)
    return config


CONFIG = load_config()


def apply_env_overrides(config):
    """Let environment variables override config.yaml.

    This is how hosted platforms pass in settings and secrets without
    touching files. Local runs are unaffected unless the vars are set.
    """
    if os.environ.get("APP_NAME"):
        config["app_name"] = os.environ["APP_NAME"]
    if os.environ.get("ACCENT_COLOR"):
        config["accent_color"] = os.environ["ACCENT_COLOR"]
    if os.environ.get("SYSTEM_PROMPT"):
        config["system_prompt"] = os.environ["SYSTEM_PROMPT"]
    if os.environ.get("PERSONA_NAME"):
        config.setdefault("persona", {})["name"] = os.environ["PERSONA_NAME"]
    if os.environ.get("PERSONA_VIBE"):
        config.setdefault("persona", {})["vibe"] = os.environ["PERSONA_VIBE"]
    if os.environ.get("BACKEND"):
        config["backend"] = os.environ["BACKEND"]
    if os.environ.get("OLLAMA_BASE_URL"):
        config["ollama"]["base_url"] = os.environ["OLLAMA_BASE_URL"]
    if os.environ.get("OLLAMA_MODEL"):
        config["ollama"]["model"] = os.environ["OLLAMA_MODEL"]
    if os.environ.get("OPENAI_COMPATIBLE_BASE_URL"):
        config["openai_compatible"]["base_url"] = os.environ[
            "OPENAI_COMPATIBLE_BASE_URL"
        ]
    if os.environ.get("OPENAI_COMPATIBLE_API_KEY"):
        config["openai_compatible"]["api_key"] = os.environ[
            "OPENAI_COMPATIBLE_API_KEY"
        ]
    if os.environ.get("OPENAI_COMPATIBLE_MODEL"):
        config["openai_compatible"]["model"] = os.environ[
            "OPENAI_COMPATIBLE_MODEL"
        ]
    if os.environ.get("AUTO_ROUTE"):
        config["auto_route"] = os.environ["AUTO_ROUTE"].lower() not in (
            "0", "false", "no", "off",
        )
    return config


CONFIG = apply_env_overrides(CONFIG)


def apply_config_secrets_to_env(config):
    """Let config.yaml supply the Google search keys for local use.

    Environment variables still win when both are set.
    """
    google = config.get("google_search") or {}
    if google.get("api_key") and not os.environ.get("GOOGLE_API_KEY"):
        os.environ["GOOGLE_API_KEY"] = str(google["api_key"])
    if google.get("cx") and not os.environ.get("GOOGLE_CX"):
        os.environ["GOOGLE_CX"] = str(google["cx"])
    if config.get("tavily_api_key") and not os.environ.get("TAVILY_API_KEY"):
        os.environ["TAVILY_API_KEY"] = str(config["tavily_api_key"])
    return config


CONFIG = apply_config_secrets_to_env(CONFIG)
CONFIG["system_prompt"] = resolve_system_prompt(CONFIG)
app = Flask(__name__)


def db_path():
    return os.path.join(BASE_DIR, CONFIG["database"])


def get_db():
    conn = sqlite3.connect(db_path())
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    conn = get_db()
    conn.execute(
        """CREATE TABLE IF NOT EXISTS conversations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL DEFAULT 'New chat',
            created_at TEXT NOT NULL
        )"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            conversation_id INTEGER NOT NULL
                REFERENCES conversations(id) ON DELETE CASCADE,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            created_at TEXT NOT NULL
        )"""
    )
    # Migration: per-message emoji reactions.
    cols = [r["name"] for r in conn.execute("PRAGMA table_info(messages)")]
    if "reactions" not in cols:
        conn.execute(
            "ALTER TABLE messages ADD COLUMN reactions TEXT NOT NULL DEFAULT ''"
        )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS activity (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            kind TEXT NOT NULL,
            label TEXT NOT NULL,
            detail TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL
        )"""
    )
    conn.commit()
    conn.close()


def log_activity(kind, label, detail=""):
    """Record a user-visible event for the activity feed."""
    try:
        conn = get_db()
        conn.execute(
            "INSERT INTO activity (kind, label, detail, created_at) "
            "VALUES (?, ?, ?, ?)",
            (kind, label[:120], detail[:200], utcnow()),
        )
        conn.commit()
        conn.close()
    except Exception:
        pass  # Activity logging is best-effort; never break the request.


TEXT_EXTENSIONS = (".txt", ".md", ".py", ".js", ".json", ".yaml", ".yml",
                   ".csv", ".html", ".css", ".sh", ".toml", ".ini", ".log")

MAX_ATTACHMENTS = 5
MAX_TEXT_CHARS = 8000


def parse_attachments(data):
    """Split request attachments into (text_blocks, images).

    text_blocks is [(name, text)] for readable files, inlined as context.
    images is [(name, mime, base64)] for vision-capable backends.
    """
    text_blocks = []
    images = []
    for att in (data.get("attachments") or [])[:MAX_ATTACHMENTS]:
        name = att.get("name", "file")
        mime = att.get("mime", "")
        b64 = att.get("data", "")
        if not b64:
            continue
        if mime.startswith("text/") or name.lower().endswith(TEXT_EXTENSIONS):
            try:
                text = base64.b64decode(b64).decode("utf-8", "replace")
            except Exception:
                continue
            text_blocks.append((name, text[:MAX_TEXT_CHARS]))
        elif mime.startswith("image/"):
            images.append((name, mime, b64))
    return text_blocks, images


def augment_message(message, text_blocks, images):
    """Fold attachments into the message text sent to the model."""
    parts = [message] if message else []
    for name, text in text_blocks:
        parts.append("[Attached file: %s]\n%s" % (name, text))
    for name, _mime, _b64 in images:
        parts.append("[Image attached: %s]" % name)
    return "\n\n".join(parts)


def utcnow():
    return datetime.now(timezone.utc).isoformat()


def conversation_exists(conn, conversation_id):
    row = conn.execute(
        "SELECT id FROM conversations WHERE id = ?", (conversation_id,)
    ).fetchone()
    return row is not None


def make_title(text):
    text = " ".join(text.split())
    return text[:45] + ("..." if len(text) > 45 else "")


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/config")
def api_config():
    return jsonify(
        {
            "app_name": CONFIG["app_name"],
            "accent_color": CONFIG["accent_color"],
        }
    )


@app.route("/api/activity")
def api_activity():
    """Recent user-visible activity for the profile panel feed."""
    conn = get_db()
    rows = conn.execute(
        "SELECT kind, label, detail, created_at FROM activity "
        "ORDER BY id DESC LIMIT 50"
    ).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@app.route("/api/conversations", methods=["GET"])
def list_conversations():
    conn = get_db()
    rows = conn.execute(
        "SELECT id, title, created_at FROM conversations "
        "ORDER BY created_at DESC"
    ).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@app.route("/api/conversations", methods=["POST"])
def create_conversation():
    conn = get_db()
    cur = conn.execute(
        "INSERT INTO conversations (title, created_at) VALUES (?, ?)",
        ("New chat", utcnow()),
    )
    conn.commit()
    conversation_id = cur.lastrowid
    conn.close()
    log_activity("chat", "New chat started")
    return jsonify({"id": conversation_id, "title": "New chat"}), 201


@app.route("/api/conversations/<int:conversation_id>", methods=["GET"])
def get_conversation(conversation_id):
    conn = get_db()
    if not conversation_exists(conn, conversation_id):
        conn.close()
        return jsonify({"error": "Conversation not found."}), 404
    rows = conn.execute(
        "SELECT id, role, content, created_at, reactions FROM messages "
        "WHERE conversation_id = ? ORDER BY id ASC",
        (conversation_id,),
    ).fetchall()
    conn.close()
    return jsonify({"messages": [dict(r) for r in rows]})


@app.route("/api/conversations/<int:conversation_id>", methods=["DELETE"])
def delete_conversation(conversation_id):
    conn = get_db()
    if not conversation_exists(conn, conversation_id):
        conn.close()
        return jsonify({"error": "Conversation not found."}), 404
    conn.execute("DELETE FROM conversations WHERE id = ?", (conversation_id,))
    conn.commit()
    conn.close()
    return jsonify({"deleted": True})


def classify_intent(backend, query, history):
    """Decide how to handle a message: 'web', 'team', or 'chat'.

    history is a list of {"role": ..., "content": ...} dicts for context.
    Falls back to 'chat' when unsure or when classification fails.
    """
    recent = "\n".join(
        "%s: %s" % (m["role"], m["content"][:300]) for m in history[-6:]
    )
    messages = [
        {"role": "system",
         "content": "You are a request router. "
                    "Reply with exactly one word: WEB, TEAM, or CHAT."},
        {"role": "user",
         "content": (
             "Classify the last user message given the conversation so far.\n\n"
             "Reply WEB if it needs current or external information "
             "(news, schedules, scores, prices, recent events, "
             "anything from 2025 or later).\n"
             "Reply TEAM only if it is a genuinely complex multi-part task "
             "that needs several specialists working in parallel "
             "(for example research plus code plus review in a single ask).\n"
             "Reply CHAT for everything else: greetings, opinions, general "
             "knowledge, coding help, follow-up chit-chat.\n\n"
             "Conversation so far:\n%s\n\nLast message: %s"
             % (recent or "(none)", query)
         )},
    ]
    try:
        result = "".join(backend.stream(messages)).strip().upper()
    except Exception:
        return "chat"
    if "WEB" in result:
        return "web"
    if "TEAM" in result:
        return "team"
    return "chat"


def team_response(backend, conversation_id, task, agents, user_message_id,
                  images=None):
    """Run the subagent team and stream the merged answer as SSE."""
    today = datetime.now().strftime("%A, %B %d, %Y")
    results, web_context = search_with_results(backend, task, today)
    try:
        result = run_subagents(backend, task, agents, CONFIG["system_prompt"],
                               web_context=web_context, images=images)
    except BackendError as exc:
        return jsonify({"error": str(exc)}), 500
    except Exception:
        return jsonify({"error": "Something went wrong running the subagents."}), 500

    text = (result.get("synthesis") or "").strip()
    if not text:
        text = "The team returned no merged answer."
    text = linkify_citations(text, results)

    conn = get_db()
    cur = conn.execute(
        "INSERT INTO messages (conversation_id, role, content, created_at) "
        "VALUES (?, ?, ?, ?)",
        (conversation_id, "assistant", text, utcnow()),
    )
    assistant_message_id = cur.lastrowid
    conn.commit()
    conn.close()

    def generate():
        try:
            # Stream in chunks so the frontend's SSE handling works unchanged.
            for i in range(0, len(text), 120):
                yield "data: %s\n\n" % json.dumps({"delta": text[i:i + 120]})
            yield stream_done_event(user_message_id, assistant_message_id)
        finally:
            yield "data: [DONE]\n\n"

    return Response(generate(), mimetype="text/event-stream")


@app.route("/api/messages/<int:message_id>/react", methods=["POST"])
def react_message(message_id):
    """Toggle an emoji reaction on a message (single user, one reaction
    per message; tapping the same emoji again removes it)."""
    data = request.get_json(force=True, silent=True) or {}
    emoji = (data.get("emoji") or "").strip()
    conn = get_db()
    row = conn.execute(
        "SELECT reactions FROM messages WHERE id = ?", (message_id,)
    ).fetchone()
    if not row:
        conn.close()
        return jsonify({"error": "Message not found."}), 404
    current = row["reactions"] or ""
    new_reaction = "" if current == emoji else emoji
    conn.execute(
        "UPDATE messages SET reactions = ? WHERE id = ?",
        (new_reaction, message_id),
    )
    conn.commit()
    conn.close()
    return jsonify({"reaction": new_reaction})


def stream_done_event(user_message_id, assistant_message_id):
    """Final SSE payload carrying the saved message IDs."""
    return "data: %s\n\n" % json.dumps({
        "done": True,
        "user_message_id": user_message_id,
        "assistant_message_id": assistant_message_id,
    })


def buffered_web_answer(backend, conversation_id, save_user_text, messages,
                        results, user_message_id, is_first=False, images=None):
    """Run a web-grounded answer to completion, linkify its citations,
    save the exchange, then stream the final text as SSE chunks.

    Buffering (instead of live token streaming) lets us rewrite stray
    [N] citation markers into clickable links before anything is shown.
    """
    try:
        raw = "".join(
            backend.stream(attach_images(messages, images, backend))
        ).strip()
    except BackendError as exc:
        return jsonify({"error": str(exc)}), 500
    except Exception:
        return jsonify(
            {"error": "Something went wrong while generating a response."}
        ), 500

    text = linkify_citations(raw, results) if raw else ""
    text = polish_text(text)
    assistant_message_id = None
    if conversation_id and text:
        conn = get_db()
        if save_user_text is not None:
            cur = conn.execute(
                "INSERT INTO messages (conversation_id, role, content, created_at) "
                "VALUES (?, ?, ?, ?)",
                (conversation_id, "user", save_user_text, utcnow()),
            )
            user_message_id = cur.lastrowid
            if is_first:
                conn.execute(
                    "UPDATE conversations SET title = ? WHERE id = ?",
                    (make_title(save_user_text), conversation_id),
                )
        cur = conn.execute(
            "INSERT INTO messages (conversation_id, role, content, created_at) "
            "VALUES (?, ?, ?, ?)",
            (conversation_id, "assistant", text, utcnow()),
        )
        assistant_message_id = cur.lastrowid
        conn.commit()
        conn.close()

    def generate():
        # Chunk on word boundaries, keeping each word's trailing space so
        # the frontend can reassemble the exact text.
        chunk = ""
        for word in text.split(" "):
            chunk += word + " "
            if len(chunk) >= 32:
                yield "data: %s\n\n" % json.dumps({"delta": chunk})
                chunk = ""
        if chunk:
            yield "data: %s\n\n" % json.dumps({"delta": chunk})
        yield stream_done_event(user_message_id, assistant_message_id)
        yield "data: [DONE]\n\n"

    return Response(generate(), mimetype="text/event-stream")


@app.route("/api/chat", methods=["POST"])
def chat():
    data = request.get_json(force=True, silent=True) or {}
    message = (data.get("message") or "").strip()
    conversation_id = data.get("conversation_id")
    text_blocks, images = parse_attachments(data)
    full_message = augment_message(message, text_blocks, images)

    if not message and not text_blocks and not images:
        return jsonify({"error": "A message is required."}), 400
    if not conversation_id:
        return jsonify({"error": "A conversation_id is required."}), 400

    conn = get_db()
    if not conversation_exists(conn, conversation_id):
        conn.close()
        return jsonify({"error": "Conversation not found."}), 404

    history_rows = conn.execute(
        "SELECT role, content FROM messages WHERE conversation_id = ? "
        "ORDER BY id ASC",
        (conversation_id,),
    ).fetchall()
    is_first = len(history_rows) == 0
    cur = conn.execute(
        "INSERT INTO messages (conversation_id, role, content, created_at) "
        "VALUES (?, ?, ?, ?)",
        (conversation_id, "user", full_message, utcnow()),
    )
    user_message_id = cur.lastrowid
    if is_first:
        conn.execute(
            "UPDATE conversations SET title = ? WHERE id = ?",
            (make_title(message), conversation_id),
        )
    conn.commit()
    conn.close()

    history_dicts = [dict(r) for r in history_rows][-20:]
    messages = [{"role": "system", "content": CONFIG["system_prompt"]}]
    messages += history_dicts
    messages.append({"role": "user", "content": full_message})

    try:
        backend = build_backend(CONFIG)
    except BackendError as exc:
        return jsonify({"error": str(exc)}), 500

    attach_images(messages, images, backend)

    # Smart routing: decide whether this needs the web, the team, or just
    # a normal reply. Short messages skip the extra model call.
    intent = "chat"
    if CONFIG.get("auto_route", True) and len(message) > 20:
        intent = classify_intent(backend, message, history_dicts)

    if intent == "web":
        today = datetime.now().strftime("%A, %B %d, %Y")
        log_activity("web", message, "Web search")
        try:
            messages, results = build_web_messages(
                backend, full_message, CONFIG["system_prompt"], today,
                history=history_dicts,
            )
        except WebError:
            # Web failed; fall back to a normal reply instead of erroring.
            pass
        else:
            return buffered_web_answer(
                backend, conversation_id, None, messages, results,
                user_message_id, images=images,
            )
    elif intent == "team":
        agents = CONFIG.get("subagents") or []
        if agents:
            log_activity("team", message, "Team run")
            return team_response(
                backend, conversation_id, full_message, agents,
                user_message_id, images=images,
            )
        # No team configured; fall through to a normal reply.

    def generate():
        parts = []
        try:
            for chunk in backend.stream(messages):
                chunk = polish_text(chunk)
                parts.append(chunk)
                yield "data: %s\n\n" % json.dumps({"delta": chunk})
        except BackendError as exc:
            yield "data: %s\n\n" % json.dumps({"error": str(exc)})
        except Exception:
            yield "data: %s\n\n" % json.dumps(
                {"error": "Something went wrong while generating a response."}
            )
        else:
            text = "".join(parts)
            assistant_message_id = None
            if text:
                conn = get_db()
                cur = conn.execute(
                    "INSERT INTO messages (conversation_id, role, content, created_at) "
                    "VALUES (?, ?, ?, ?)",
                    (conversation_id, "assistant", text, utcnow()),
                )
                assistant_message_id = cur.lastrowid
                conn.commit()
                conn.close()
            yield stream_done_event(user_message_id, assistant_message_id)
        finally:
            yield "data: [DONE]\n\n"

    return Response(generate(), mimetype="text/event-stream")


@app.route("/api/web", methods=["POST"])
def web():
    """Answer a question using live web search results.

    Request body: {"query": "...", "conversation_id": 123}
    Explicit version of what the chat router does automatically.
    Streams the answer like /api/chat and saves the exchange to the
    conversation when conversation_id is given.
    """
    data = request.get_json(force=True, silent=True) or {}
    query = (data.get("query") or "").strip()
    conversation_id = data.get("conversation_id")
    text_blocks, images = parse_attachments(data)
    full_query = augment_message(query, text_blocks, images)

    if not query and not text_blocks and not images:
        return jsonify({"error": "A query is required."}), 400

    log_activity("web", query or "File search", "Web search")

    history = []
    is_first = False
    if conversation_id:
        conn = get_db()
        if not conversation_exists(conn, conversation_id):
            conn.close()
            return jsonify({"error": "Conversation not found."}), 404
        history = [
            dict(r) for r in conn.execute(
                "SELECT role, content FROM messages WHERE conversation_id = ? "
                "ORDER BY id ASC",
                (conversation_id,),
            ).fetchall()
        ]
        is_first = len(history) == 0
        conn.close()

    try:
        backend = build_backend(CONFIG)
    except BackendError as exc:
        return jsonify({"error": str(exc)}), 500

    today = datetime.now().strftime("%A, %B %d, %Y")
    try:
        messages, results = build_web_messages(
            backend, full_query, CONFIG["system_prompt"], today, history=history
        )
    except WebError as exc:
        return jsonify({"error": str(exc)}), 500

    return buffered_web_answer(
        backend, conversation_id, full_query, messages, results, None,
        is_first=is_first, images=images,
    )


@app.route("/api/subagents", methods=["POST"])
def subagents():
    """Fan a task out to subagents in parallel and return merged results.

    Request body: {"task": "...", "agents": [{"name": ..., "system_prompt": ...}],
                   "synthesize": true, "max_workers": 3,
                   "conversation_id": 123}
    If "agents" is omitted, the presets under "subagents" in config.yaml
    are used. If "conversation_id" is given, the task and the merged
    answer are saved to that conversation like a normal chat exchange.
    """
    data = request.get_json(force=True, silent=True) or {}
    task = (data.get("task") or "").strip()
    agents = data.get("agents") or CONFIG.get("subagents") or []
    synthesize = data.get("synthesize", True)
    max_workers = data.get("max_workers", 3)
    conversation_id = data.get("conversation_id")
    text_blocks, images = parse_attachments(data)
    task = augment_message(task, text_blocks, images)

    if not task:
        return jsonify({"error": "A task is required."}), 400
    log_activity("team", task, "Team run")
    if not agents:
        return jsonify(
            {
                "error": "No subagents configured. Add some under "
                         "'subagents' in config.yaml."
            }
        ), 400

    conn = None
    is_first = False
    if conversation_id:
        conn = get_db()
        if not conversation_exists(conn, conversation_id):
            conn.close()
            return jsonify({"error": "Conversation not found."}), 404
        history_rows = conn.execute(
            "SELECT id FROM messages WHERE conversation_id = ?",
            (conversation_id,),
        ).fetchall()
        is_first = len(history_rows) == 0

    try:
        backend = build_backend(CONFIG)
    except BackendError as exc:
        if conn:
            conn.close()
        return jsonify({"error": str(exc)}), 500

    today = datetime.now().strftime("%A, %B %d, %Y")
    results, web_context = search_with_results(backend, task, today)
    try:
        result = run_subagents(
            backend,
            task,
            agents,
            CONFIG["system_prompt"],
            synthesize=bool(synthesize),
            max_workers=int(max_workers),
            web_context=web_context,
            images=images,
        )
    except BackendError as exc:
        if conn:
            conn.close()
        return jsonify({"error": str(exc)}), 500
    except Exception:
        if conn:
            conn.close()
        return jsonify(
            {"error": "Something went wrong running the subagents."}
        ), 500

    user_message_id = None
    assistant_message_id = None
    if conn:
        cur = conn.execute(
            "INSERT INTO messages (conversation_id, role, content, created_at) "
            "VALUES (?, ?, ?, ?)",
            (conversation_id, "user", task, utcnow()),
        )
        user_message_id = cur.lastrowid
        if is_first:
            conn.execute(
                "UPDATE conversations SET title = ? WHERE id = ?",
                (make_title(task), conversation_id),
            )
        synthesis = linkify_citations(
            (result.get("synthesis") or "").strip(), results
        )
        if synthesis:
            result["synthesis"] = synthesis
            cur = conn.execute(
                "INSERT INTO messages (conversation_id, role, content, created_at) "
                "VALUES (?, ?, ?, ?)",
                (conversation_id, "assistant", synthesis, utcnow()),
            )
            assistant_message_id = cur.lastrowid
        conn.commit()
        conn.close()

    result["user_message_id"] = user_message_id
    result["assistant_message_id"] = assistant_message_id
    return jsonify(result)


if __name__ == "__main__":
    init_db()
    # Hosts like Render/Railway/Fly set PORT; bind 0.0.0.0 there so their
    # port scan can reach the app. Local runs stay on localhost.
    default_host = "0.0.0.0" if "PORT" in os.environ else "127.0.0.1"
    app.run(
        host=os.environ.get("HOST", default_host),
        port=int(os.environ.get("PORT", CONFIG["port"])),
        debug=False,
    )
