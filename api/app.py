from datetime import datetime, timezone
from pathlib import Path
import html
import json
import sqlite3

from flask import Flask, jsonify, request


APP_DIR = Path(__file__).resolve().parent
DATABASE = APP_DIR / "kyle_it.db"

app = Flask(__name__)

TICKET_CATEGORIES = {"Hardware", "Software", "Network", "Account Access", "Other"}
TICKET_PRIORITIES = {"Low", "Medium", "High"}
TICKET_STATUSES = {"Open", "In Progress", "Resolved"}
HARDWARE_TYPES = {"Laptop", "Desktop", "Monitor", "Printer", "Network Equipment", "Other"}
HARDWARE_STATUSES = {"Available", "In Use", "Repair", "Retired"}
AGENT_ROLES = {"Admin", "Agent"}
LANDING_PAGES = {"Dashboard", "All Tickets"}
ACTIVITY_FEED_LIMIT = 20
ACTIVITY_KEEP = 200

DEFAULT_SETTINGS = {
    "workspace_name": "Kyle IT Support Desk",
    "support_email": "support@kyle-it.com",
    "timezone": "Pacific Time (PT)",
    "landing_page": "Dashboard",
    "notify_new_ticket": True,
    "notify_escalation": True,
    "notify_resolved": True,
    "notify_weekly": False,
    "default_priority": "Medium",
    "default_assignee": "Kyle",
    "sla_target": 60,
    "auto_close": "7 days",
}

TICKET_SELECT = """
    SELECT t.*, h.name AS hardware_name, h.tag AS hardware_tag
    FROM tickets t
    LEFT JOIN hardware h ON h.id = t.hardware_id
"""

HARDWARE_SELECT = """
    SELECT h.*, (
        SELECT COUNT(*) FROM tickets t
        WHERE t.hardware_id = h.id AND t.status != 'Resolved'
    ) AS open_ticket_count
    FROM hardware h
"""


def get_db():
    connection = sqlite3.connect(DATABASE)
    connection.row_factory = sqlite3.Row
    return connection


def column_names(db, table):
    return {row[1] for row in db.execute(f"PRAGMA table_info({table})")}


def read_settings(db):
    stored = {row["key"]: json.loads(row["value"]) for row in db.execute("SELECT key, value FROM settings")}
    merged = dict(DEFAULT_SETTINGS)
    merged.update({key: value for key, value in stored.items() if key in DEFAULT_SETTINGS})
    return merged


def init_db():
    with get_db() as db:
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS tickets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                subject TEXT NOT NULL,
                requester TEXT NOT NULL,
                category TEXT NOT NULL,
                priority TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'Open',
                assignee TEXT NOT NULL DEFAULT 'Unassigned',
                description TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL
            )
            """
        )
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS hardware (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                tag TEXT NOT NULL,
                type TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'Available',
                owner TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL
            )
            """
        )
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS agents (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                email TEXT NOT NULL DEFAULT '',
                role TEXT NOT NULL DEFAULT 'Agent',
                created_at TEXT NOT NULL
            )
            """
        )
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
            """
        )
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS activity (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                icon TEXT NOT NULL DEFAULT '•',
                text TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )

        if "hardware_id" not in column_names(db, "tickets"):
            db.execute("ALTER TABLE tickets ADD COLUMN hardware_id INTEGER REFERENCES hardware(id)")

        now = datetime.now(timezone.utc).isoformat()

        if db.execute("SELECT COUNT(*) FROM tickets").fetchone()[0] == 0:
            db.executemany(
                """
                INSERT INTO tickets
                    (subject, requester, category, priority, status, assignee, description, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    ("Unable to connect to VPN", "Maya Chen", "Network", "High", "Open", "Kyle", "VPN connection times out after authentication.", now),
                    ("Laptop running unusually slow", "Daniel Brooks", "Hardware", "Medium", "In Progress", "Marcus", "Laptop performance declined after the latest update.", now),
                    ("Password reset request", "Sofia Martinez", "Account Access", "Low", "Resolved", "Ana", "User was unable to access their company account.", now),
                    ("Outlook will not sync email", "Ethan Wilson", "Software", "Medium", "Open", "Kyle", "New emails are not appearing in the desktop application.", now),
                    ("Conference room display offline", "Olivia Patel", "Hardware", "High", "In Progress", "Marcus", "Display shows no signal from the conference room computer.", now),
                ],
            )

        # Hardware inventory is not seeded — it starts empty and is populated
        # entirely through the app (Inventory page or the New Ticket form).

        if db.execute("SELECT COUNT(*) FROM agents").fetchone()[0] == 0:
            db.executemany(
                "INSERT INTO agents (name, email, role, created_at) VALUES (?, ?, ?, ?)",
                [
                    ("Kyle", "kyle@kyle-it.com", "Admin", now),
                    ("Marcus", "marcus@kyle-it.com", "Agent", now),
                    ("Ana", "ana@kyle-it.com", "Agent", now),
                ],
            )

        existing_keys = {row[0] for row in db.execute("SELECT key FROM settings")}
        for key, value in DEFAULT_SETTINGS.items():
            if key not in existing_keys:
                db.execute("INSERT INTO settings (key, value) VALUES (?, ?)", (key, json.dumps(value)))

        if db.execute("SELECT COUNT(*) FROM activity").fetchone()[0] == 0:
            recent_tickets = db.execute(
                "SELECT id, requester, created_at FROM tickets ORDER BY id DESC LIMIT 5"
            ).fetchall()
            db.executemany(
                "INSERT INTO activity (icon, text, created_at) VALUES (?, ?, ?)",
                [
                    ("+", f"<strong>{html.escape(row['requester'])}</strong> submitted ticket #{row['id']}", row["created_at"])
                    for row in reversed(recent_tickets)
                ],
            )


@app.get("/api/health")
def health():
    return jsonify({"service": "Kyle IT API", "status": "healthy"})


# --- Tickets ----------------------------------------------------------------

@app.get("/api/tickets")
def list_tickets():
    with get_db() as db:
        rows = db.execute(TICKET_SELECT + " ORDER BY t.id DESC").fetchall()
    return jsonify([dict(row) for row in rows])


@app.post("/api/tickets")
def create_ticket():
    data = request.get_json(silent=True) or {}

    subject = str(data.get("subject", "")).strip()
    requester = str(data.get("requester", "")).strip()
    category = str(data.get("category", "Other")).strip()
    priority = str(data.get("priority", "Medium")).strip()
    assignee = str(data.get("assignee", "Unassigned")).strip() or "Unassigned"
    description = str(data.get("description", "")).strip()
    hardware_id = data.get("hardware_id")

    if not subject or not requester:
        return jsonify({"error": "Subject and requester are required."}), 400
    if category not in TICKET_CATEGORIES:
        return jsonify({"error": "Invalid category."}), 400
    if priority not in TICKET_PRIORITIES:
        return jsonify({"error": "Invalid priority."}), 400

    with get_db() as db:
        valid_assignees = {row[0] for row in db.execute("SELECT name FROM agents")} | {"Unassigned"}
        if assignee not in valid_assignees:
            return jsonify({"error": "Unknown assignee."}), 400

        if hardware_id in (None, "", "None"):
            hardware_id = None
        else:
            try:
                hardware_id = int(hardware_id)
            except (TypeError, ValueError):
                return jsonify({"error": "Invalid hardware reference."}), 400
            if db.execute("SELECT 1 FROM hardware WHERE id = ?", (hardware_id,)).fetchone() is None:
                return jsonify({"error": "Related hardware not found."}), 400

        created_at = datetime.now(timezone.utc).isoformat()
        cursor = db.execute(
            """
            INSERT INTO tickets
                (subject, requester, category, priority, status, assignee, description, created_at, hardware_id)
            VALUES (?, ?, ?, ?, 'Open', ?, ?, ?, ?)
            """,
            (subject, requester, category, priority, assignee, description, created_at, hardware_id),
        )
        row = db.execute(TICKET_SELECT + " WHERE t.id = ?", (cursor.lastrowid,)).fetchone()

    return jsonify(dict(row)), 201


@app.patch("/api/tickets/<int:ticket_id>")
def update_ticket(ticket_id):
    data = request.get_json(silent=True) or {}
    status = str(data.get("status", "")).strip()

    if status not in TICKET_STATUSES:
        return jsonify({"error": "Status must be Open, In Progress, or Resolved."}), 400

    with get_db() as db:
        cursor = db.execute("UPDATE tickets SET status = ? WHERE id = ?", (status, ticket_id))
        if cursor.rowcount == 0:
            return jsonify({"error": "Ticket not found."}), 404
        row = db.execute(TICKET_SELECT + " WHERE t.id = ?", (ticket_id,)).fetchone()

    return jsonify(dict(row))


@app.delete("/api/tickets/<int:ticket_id>")
def delete_ticket(ticket_id):
    with get_db() as db:
        cursor = db.execute("DELETE FROM tickets WHERE id = ?", (ticket_id,))
        if cursor.rowcount == 0:
            return jsonify({"error": "Ticket not found."}), 404

    return "", 204


# --- Hardware / Inventory --------------------------------------------------

@app.get("/api/hardware")
def list_hardware():
    with get_db() as db:
        rows = db.execute(HARDWARE_SELECT + " ORDER BY h.id DESC").fetchall()
    return jsonify([dict(row) for row in rows])


@app.post("/api/hardware")
def create_hardware():
    data = request.get_json(silent=True) or {}

    name = str(data.get("name", "")).strip()
    tag = str(data.get("tag", "")).strip()
    hw_type = str(data.get("type", "Other")).strip()
    status = str(data.get("status", "Available")).strip()
    owner = str(data.get("owner", "")).strip()

    if not name or not tag:
        return jsonify({"error": "Device name and asset tag are required."}), 400
    if hw_type not in HARDWARE_TYPES:
        return jsonify({"error": "Invalid hardware type."}), 400
    if status not in HARDWARE_STATUSES:
        return jsonify({"error": "Invalid hardware status."}), 400

    created_at = datetime.now(timezone.utc).isoformat()
    with get_db() as db:
        cursor = db.execute(
            "INSERT INTO hardware (name, tag, type, status, owner, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (name, tag, hw_type, status, owner, created_at),
        )
        row = db.execute(HARDWARE_SELECT + " WHERE h.id = ?", (cursor.lastrowid,)).fetchone()

    return jsonify(dict(row)), 201


@app.patch("/api/hardware/<int:hardware_id>")
def update_hardware(hardware_id):
    data = request.get_json(silent=True) or {}
    fields = {}

    if "name" in data:
        name = str(data["name"]).strip()
        if not name:
            return jsonify({"error": "Device name cannot be empty."}), 400
        fields["name"] = name
    if "tag" in data:
        tag = str(data["tag"]).strip()
        if not tag:
            return jsonify({"error": "Asset tag cannot be empty."}), 400
        fields["tag"] = tag
    if "type" in data:
        if data["type"] not in HARDWARE_TYPES:
            return jsonify({"error": "Invalid hardware type."}), 400
        fields["type"] = data["type"]
    if "status" in data:
        if data["status"] not in HARDWARE_STATUSES:
            return jsonify({"error": "Invalid hardware status."}), 400
        fields["status"] = data["status"]
    if "owner" in data:
        fields["owner"] = str(data["owner"]).strip()

    if not fields:
        return jsonify({"error": "No valid fields to update."}), 400

    assignments = ", ".join(f"{key} = ?" for key in fields)
    with get_db() as db:
        cursor = db.execute(
            f"UPDATE hardware SET {assignments} WHERE id = ?",
            (*fields.values(), hardware_id),
        )
        if cursor.rowcount == 0:
            return jsonify({"error": "Hardware not found."}), 404
        row = db.execute(HARDWARE_SELECT + " WHERE h.id = ?", (hardware_id,)).fetchone()

    return jsonify(dict(row))


@app.delete("/api/hardware/<int:hardware_id>")
def delete_hardware(hardware_id):
    with get_db() as db:
        if db.execute("SELECT 1 FROM hardware WHERE id = ?", (hardware_id,)).fetchone() is None:
            return jsonify({"error": "Hardware not found."}), 404
        db.execute("UPDATE tickets SET hardware_id = NULL WHERE hardware_id = ?", (hardware_id,))
        db.execute("DELETE FROM hardware WHERE id = ?", (hardware_id,))

    return "", 204


# --- Agents ---------------------------------------------------------------

@app.get("/api/agents")
def list_agents():
    with get_db() as db:
        rows = db.execute("SELECT * FROM agents ORDER BY id").fetchall()
    return jsonify([dict(row) for row in rows])


@app.post("/api/agents")
def create_agent():
    data = request.get_json(silent=True) or {}

    name = str(data.get("name", "")).strip()
    email = str(data.get("email", "")).strip()
    role = str(data.get("role", "Agent")).strip()

    if not name:
        return jsonify({"error": "Agent name is required."}), 400
    if role not in AGENT_ROLES:
        return jsonify({"error": "Role must be Admin or Agent."}), 400

    created_at = datetime.now(timezone.utc).isoformat()
    with get_db() as db:
        if db.execute("SELECT 1 FROM agents WHERE name = ?", (name,)).fetchone():
            return jsonify({"error": "An agent with that name already exists."}), 400
        cursor = db.execute(
            "INSERT INTO agents (name, email, role, created_at) VALUES (?, ?, ?, ?)",
            (name, email, role, created_at),
        )
        row = db.execute("SELECT * FROM agents WHERE id = ?", (cursor.lastrowid,)).fetchone()

    return jsonify(dict(row)), 201


@app.delete("/api/agents/<int:agent_id>")
def delete_agent(agent_id):
    with get_db() as db:
        cursor = db.execute("DELETE FROM agents WHERE id = ?", (agent_id,))
        if cursor.rowcount == 0:
            return jsonify({"error": "Agent not found."}), 404

    return "", 204


# --- Settings -------------------------------------------------------------

@app.get("/api/settings")
def get_settings():
    with get_db() as db:
        return jsonify(read_settings(db))


@app.put("/api/settings")
def put_settings():
    data = request.get_json(silent=True) or {}
    updates = {}

    for key, default in DEFAULT_SETTINGS.items():
        if key not in data:
            continue
        value = data[key]
        if isinstance(default, bool):
            value = bool(value)
        elif isinstance(default, int):
            try:
                value = int(value)
            except (TypeError, ValueError):
                return jsonify({"error": f"{key.replace('_', ' ')} must be a number."}), 400
        else:
            value = str(value).strip()
        updates[key] = value

    if updates.get("landing_page") not in (None, *LANDING_PAGES):
        return jsonify({"error": "Invalid landing page."}), 400
    if updates.get("default_priority") not in (None, *TICKET_PRIORITIES):
        return jsonify({"error": "Invalid default priority."}), 400
    if "sla_target" in updates and updates["sla_target"] < 1:
        return jsonify({"error": "SLA target must be at least 1 minute."}), 400

    with get_db() as db:
        for key, value in updates.items():
            db.execute(
                "INSERT INTO settings (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, json.dumps(value)),
            )
        result = read_settings(db)

    return jsonify(result)


# --- Activity feed ------------------------------------------------------

@app.get("/api/activity")
def list_activity():
    with get_db() as db:
        rows = db.execute(
            "SELECT * FROM activity ORDER BY id DESC LIMIT ?", (ACTIVITY_FEED_LIMIT,)
        ).fetchall()
    return jsonify([dict(row) for row in rows])


@app.post("/api/activity")
def create_activity():
    data = request.get_json(silent=True) or {}
    icon = (str(data.get("icon", "")).strip() or "•")[:8]
    text = str(data.get("text", "")).strip()[:400]

    if not text:
        return jsonify({"error": "Activity text is required."}), 400

    created_at = datetime.now(timezone.utc).isoformat()
    with get_db() as db:
        cursor = db.execute(
            "INSERT INTO activity (icon, text, created_at) VALUES (?, ?, ?)",
            (icon, text, created_at),
        )
        db.execute(
            "DELETE FROM activity WHERE id NOT IN (SELECT id FROM activity ORDER BY id DESC LIMIT ?)",
            (ACTIVITY_KEEP,),
        )
        row = db.execute("SELECT * FROM activity WHERE id = ?", (cursor.lastrowid,)).fetchone()

    return jsonify(dict(row)), 201


@app.delete("/api/activity/<int:activity_id>")
def delete_activity(activity_id):
    with get_db() as db:
        cursor = db.execute("DELETE FROM activity WHERE id = ?", (activity_id,))
        if cursor.rowcount == 0:
            return jsonify({"error": "Activity entry not found."}), 404

    return "", 204


init_db()


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=False)
