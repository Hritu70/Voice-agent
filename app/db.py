import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

from . import config

LEAD_COLS = ["name", "need", "budget", "timeline", "authority", "company_size", "score", "sentiment"]


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@contextmanager
def connect():
    """Open a connection, commit on success / roll back on error, and always close it."""
    config.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(config.DB_PATH, timeout=15, check_same_thread=False)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    try:
        yield c
        c.commit()
    except Exception:
        c.rollback()
        raise
    finally:
        c.close()


def init_db():
    with connect() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS leads (
          session_id TEXT PRIMARY KEY, name TEXT, need TEXT, budget TEXT, timeline TEXT, authority TEXT, company_size TEXT,
          score INTEGER NOT NULL DEFAULT 0, sentiment TEXT NOT NULL DEFAULT 'neutral', status TEXT NOT NULL DEFAULT 'new', updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS calls (
          call_id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL, transcript TEXT, summary TEXT,
          action_items TEXT, final_lead_score INTEGER, sentiment TEXT, created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS slots (
          slot_id INTEGER PRIMARY KEY AUTOINCREMENT, slot_date TEXT NOT NULL, slot_time TEXT NOT NULL,
          booked_by TEXT, UNIQUE(slot_date, slot_time)
        );
        CREATE TABLE IF NOT EXISTS appointments (
          appointment_id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL, slot_id INTEGER NOT NULL UNIQUE,
          customer_name TEXT, appointment_type TEXT NOT NULL DEFAULT 'demo', created_at TEXT NOT NULL,
          FOREIGN KEY(slot_id) REFERENCES slots(slot_id)
        );
        """)
        # Seed demo slots for the next 7 days. Re-seeds when all seeded slots are in the past,
        # so an old database doesn't end up with no bookable slots.
        today = datetime.now().strftime("%Y-%m-%d")
        if c.execute("SELECT COUNT(*) FROM slots WHERE slot_date > ?", (today,)).fetchone()[0] == 0:
            now = datetime.now()
            seed = [((now + timedelta(days=d)).strftime("%Y-%m-%d"), t)
                    for d in range(1, 8) for t in ("10:00", "14:00", "16:00")]
            c.executemany("INSERT OR IGNORE INTO slots(slot_date,slot_time) VALUES(?,?)", seed)


def _lead_values(lead):
    vals = []
    for k in LEAD_COLS:
        v = lead.get(k)
        if isinstance(v, (list, dict)):
            v = json.dumps(v, ensure_ascii=False)
        vals.append(v)
    vals[LEAD_COLS.index("score")] = int(lead.get("score") or 0)
    vals[LEAD_COLS.index("sentiment")] = lead.get("sentiment") or "neutral"
    return vals


def _upsert_lead(c, session_id, lead):
    c.execute(
        f"INSERT INTO leads(session_id,{','.join(LEAD_COLS)},updated_at) "
        f"VALUES(?,{','.join(['?'] * len(LEAD_COLS))},?) "
        f"ON CONFLICT(session_id) DO UPDATE SET {','.join(f'{k}=excluded.{k}' for k in LEAD_COLS)},"
        f"updated_at=excluded.updated_at",
        [session_id, *_lead_values(lead), _now()])


def upsert_lead(session_id, lead):
    with connect() as c:
        _upsert_lead(c, session_id, lead)


def list_slots(only_available=True, upcoming_only=True):
    q = "SELECT slot_id,slot_date,slot_time,booked_by FROM slots WHERE 1=1"
    params = []
    if only_available:
        q += " AND booked_by IS NULL"
    if upcoming_only:
        q += " AND slot_date >= ?"
        params.append(datetime.now().strftime("%Y-%m-%d"))
    q += " ORDER BY slot_date,slot_time"
    with connect() as c:
        return [dict(r) for r in c.execute(q, params).fetchall()]


def book_slot(slot_id, session_id, customer_name):
    """Atomically claim a free slot and create the appointment. Returns None if unavailable."""
    with connect() as c:
        c.execute("BEGIN IMMEDIATE")
        cur = c.execute("UPDATE slots SET booked_by=? WHERE slot_id=? AND booked_by IS NULL", (session_id, slot_id))
        if cur.rowcount != 1:
            c.rollback()
            return None
        c.execute("INSERT INTO appointments(session_id,slot_id,customer_name,created_at) VALUES(?,?,?,?)",
                  (session_id, slot_id, customer_name, _now()))
        c.execute("UPDATE leads SET status='booked',updated_at=? WHERE session_id=?", (_now(), session_id))
        r = c.execute("SELECT slot_id,slot_date,slot_time FROM slots WHERE slot_id=?", (slot_id,)).fetchone()
        return dict(r)


def save_call(session_id, transcript, summary, action_items, lead):
    with connect() as c:
        # Make sure the lead row exists even if background extraction never finished.
        _upsert_lead(c, session_id, lead)
        c.execute(
            "INSERT INTO calls(session_id,transcript,summary,action_items,final_lead_score,sentiment,created_at) "
            "VALUES(?,?,?,?,?,?,?)",
            (session_id, transcript, summary, json.dumps(action_items, ensure_ascii=False),
             int(lead.get("score") or 0), lead.get("sentiment") or "neutral", _now()))
        c.execute(
            "UPDATE leads SET status=CASE WHEN EXISTS(SELECT 1 FROM appointments a WHERE a.session_id=leads.session_id) "
            "THEN 'booked' ELSE 'completed' END WHERE session_id=?", (session_id,))


def dashboard_data():
    with connect() as c:
        calls = [dict(r) for r in c.execute("SELECT * FROM calls ORDER BY created_at DESC").fetchall()]
        for call in calls:
            try:
                call["action_items"] = json.loads(call["action_items"] or "[]")
            except (TypeError, ValueError):
                pass
        data = {
            "leads": [dict(r) for r in c.execute("SELECT * FROM leads ORDER BY updated_at DESC").fetchall()],
            "calls": calls,
            "appointments": [dict(r) for r in c.execute(
                "SELECT a.*,s.slot_date,s.slot_time FROM appointments a JOIN slots s ON a.slot_id=s.slot_id "
                "ORDER BY s.slot_date,s.slot_time").fetchall()],
        }
    data["available_slots"] = list_slots(True)
    return data


def set_lead_status(session_id, status):
    with connect() as c:
        c.execute("UPDATE leads SET status=?,updated_at=? WHERE session_id=?", (status, _now(), session_id))
