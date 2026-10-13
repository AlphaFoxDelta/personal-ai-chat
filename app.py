"""Personal AI chat app. Flask backend, SQLite history, swappable backends.

Run with: python app.py
Then open http://localhost:5000 in a browser.
"""

import copy
import json
import os
import sqlite3
from datetime import datetime, timezone

import yaml
from flask import Flask, Response, jsonify, render_template, request

from backends import BackendError, build_backend
from subagents import run_subagents
from webtools import WebError, web_fetch, web_search

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

DEFAULT_CONFIG = {
    "app_name": "Kitsune",
    "accent_color": "#f97316",
    "system_prompt": "You are a helpful assistant.",
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
    "subagents": [],
    "database": "chat.db",
    "port": 5000,
}


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
    conn.commit()
    conn.close()


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
    return jsonify({"id": conversation_id, "title": "New chat"}), 201


@app.route("/api/conversations/<int:conversation_id>", methods=["GET"])
def get_conversation(conversation_id):
    conn = get_db()
    if not conversation_exists(conn, conversation_id):
        conn.close()
        return jsonify({"error": "Conversation not found."}), 404
    rows = conn.execute(
        "SELECT role, content, created_at FROM messages "
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


@app.route("/api/chat", methods=["POST"])
def chat():
    data = request.get_json(force=True, silent=True) or {}
    message = (data.get("message") or "").strip()
    conversation_id = data.get("conversation_id")

    if not message:
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
    conn.execute(
        "INSERT INTO messages (conversation_id, role, content, created_at) "
        "VALUES (?, ?, ?, ?)",
        (conversation_id, "user", message, utcnow()),
    )
    if is_first:
        conn.execute(
            "UPDATE conversations SET title = ? WHERE id = ?",
            (make_title(message), conversation_id),
        )
    conn.commit()
    conn.close()

    messages = [{"role": "system", "content": CONFIG["system_prompt"]}]
    messages += [dict(r) for r in history_rows]
    messages.append({"role": "user", "content": message})

    try:
        backend = build_backend(CONFIG)
    except BackendError as exc:
        return jsonify({"error": str(exc)}), 500

    def generate():
        parts = []
        try:
            for chunk in backend.stream(messages):
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
            if text:
                conn = get_db()
                conn.execute(
                    "INSERT INTO messages (conversation_id, role, content, created_at) "
                    "VALUES (?, ?, ?, ?)",
                    (conversation_id, "assistant", text, utcnow()),
                )
                conn.commit()
                conn.close()
        finally:
            yield "data: [DONE]\n\n"

    return Response(generate(), mimetype="text/event-stream")


@app.route("/api/web", methods=["POST"])
def web():
    """Answer a question using live web search results.

    Request body: {"query": "...", "conversation_id": 123}
    Searches DuckDuckGo (free, no key), then asks the model to answer
    using the results. Streams the answer like /api/chat and saves the
    exchange to the conversation when conversation_id is given.
    """
    data = request.get_json(force=True, silent=True) or {}
    query = (data.get("query") or "").strip()
    conversation_id = data.get("conversation_id")

    if not query:
        return jsonify({"error": "A query is required."}), 400

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
        results = web_search(query)
    except WebError as exc:
        if conn:
            conn.close()
        return jsonify({"error": str(exc)}), 500

    context_parts = [
        "[%d] %s\n%s\n%s" % (i + 1, r["title"], r["url"], r["snippet"])
        for i, r in enumerate(results)
    ]
    # Read the top pages so the model works from real content,
    # not just snippets.
    for r in results[:3]:
        page_text = web_fetch(r["url"])
        if page_text:
            context_parts.append(
                "Page content from %s:\n%s" % (r["url"], page_text)
            )
    context = "\n\n".join(context_parts)

    today = datetime.now().strftime("%A, %B %d, %Y")
    messages = [
        {"role": "system", "content": CONFIG["system_prompt"]},
        {
            "role": "user",
            "content": (
                "Today is %s.\n\n"
                "Answer the question below using these web search results. "
                "Give the direct answer first with specific details "
                "(names, dates, times). Cite sources with [1], [2] markers. "
                "If the results do not contain the answer, say so plainly "
                "instead of guessing.\n\n"
                "Search results:\n%s\n\nQuestion: %s"
                % (today, context, query)
            ),
        },
    ]

    try:
        backend = build_backend(CONFIG)
    except BackendError as exc:
        if conn:
            conn.close()
        return jsonify({"error": str(exc)}), 500

    def generate():
        parts = []
        try:
            for chunk in backend.stream(messages):
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
            if conn and text:
                conn.execute(
                    "INSERT INTO messages (conversation_id, role, content, created_at) "
                    "VALUES (?, ?, ?, ?)",
                    (conversation_id, "user", query, utcnow()),
                )
                if is_first:
                    conn.execute(
                        "UPDATE conversations SET title = ? WHERE id = ?",
                        (make_title(query), conversation_id),
                    )
                conn.execute(
                    "INSERT INTO messages (conversation_id, role, content, created_at) "
                    "VALUES (?, ?, ?, ?)",
                    (conversation_id, "assistant", text, utcnow()),
                )
                conn.commit()
        finally:
            if conn:
                conn.close()
            yield "data: [DONE]\n\n"

    return Response(generate(), mimetype="text/event-stream")


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

    if not task:
        return jsonify({"error": "A task is required."}), 400
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

    try:
        result = run_subagents(
            backend,
            task,
            agents,
            CONFIG["system_prompt"],
            synthesize=bool(synthesize),
            max_workers=int(max_workers),
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

    if conn:
        conn.execute(
            "INSERT INTO messages (conversation_id, role, content, created_at) "
            "VALUES (?, ?, ?, ?)",
            (conversation_id, "user", task, utcnow()),
        )
        if is_first:
            conn.execute(
                "UPDATE conversations SET title = ? WHERE id = ?",
                (make_title(task), conversation_id),
            )
        synthesis = (result.get("synthesis") or "").strip()
        if synthesis:
            conn.execute(
                "INSERT INTO messages (conversation_id, role, content, created_at) "
                "VALUES (?, ?, ?, ?)",
                (conversation_id, "assistant", synthesis, utcnow()),
            )
        conn.commit()
        conn.close()

    return jsonify(result)


if __name__ == "__main__":
    init_db()
    app.run(host="127.0.0.1", port=int(CONFIG["port"]), debug=False)
