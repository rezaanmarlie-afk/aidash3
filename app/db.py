import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class Database:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.init()

    @contextmanager
    def connect(self):
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def init(self):
        with self.connect() as conn:
            conn.executescript(
                '''
                CREATE TABLE IF NOT EXISTS app_settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS signoffs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    initiative_key TEXT NOT NULL,
                    pi_value TEXT NOT NULL,
                    scrum_master_id TEXT NOT NULL,
                    decision TEXT NOT NULL,
                    signer_name TEXT NOT NULL,
                    signer_email TEXT,
                    comment TEXT,
                    snapshot_hash TEXT NOT NULL,
                    snapshot_json TEXT NOT NULL,
                    jira_writeback INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_signoffs_lookup
                ON signoffs(initiative_key, pi_value, scrum_master_id, created_at);
                CREATE TABLE IF NOT EXISTS audit_runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    jql TEXT NOT NULL,
                    pi_value TEXT NOT NULL,
                    scrum_master_id TEXT NOT NULL,
                    result_count INTEGER NOT NULL,
                    created_at TEXT NOT NULL
                );
                '''
            )

    def get_setting(self, key: str, default: Any = None) -> Any:
        with self.connect() as conn:
            row = conn.execute('SELECT value FROM app_settings WHERE key=?', (key,)).fetchone()
        if not row:
            return default
        try:
            return json.loads(row['value'])
        except json.JSONDecodeError:
            return row['value']

    def set_setting(self, key: str, value: Any):
        payload = json.dumps(value)
        with self.connect() as conn:
            conn.execute(
                'INSERT INTO app_settings(key,value) VALUES(?,?) '
                'ON CONFLICT(key) DO UPDATE SET value=excluded.value',
                (key, payload),
            )

    def save_signoff(self, data: dict[str, Any]) -> int:
        now = datetime.now(timezone.utc).isoformat()
        with self.connect() as conn:
            cur = conn.execute(
                '''INSERT INTO signoffs(
                    initiative_key, pi_value, scrum_master_id, decision,
                    signer_name, signer_email, comment, snapshot_hash,
                    snapshot_json, jira_writeback, created_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?)''',
                (
                    data['initiative_key'], data['pi_value'], data['scrum_master_id'],
                    data['decision'], data['signer_name'], data.get('signer_email', ''),
                    data.get('comment', ''), data['snapshot_hash'],
                    json.dumps(data['snapshot_json']), int(bool(data.get('jira_writeback'))), now,
                ),
            )
            return int(cur.lastrowid)

    def latest_signoff(self, initiative_key: str, pi_value: str, scrum_master_id: str):
        with self.connect() as conn:
            row = conn.execute(
                '''SELECT * FROM signoffs
                   WHERE initiative_key=? AND pi_value=? AND scrum_master_id=?
                   ORDER BY created_at DESC LIMIT 1''',
                (initiative_key, pi_value, scrum_master_id),
            ).fetchone()
        return dict(row) if row else None

    def list_signoffs(self, pi_value: str | None = None):
        sql = 'SELECT * FROM signoffs'
        params: tuple = ()
        if pi_value:
            sql += ' WHERE pi_value=?'
            params = (pi_value,)
        sql += ' ORDER BY created_at DESC'
        with self.connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    def log_run(self, jql: str, pi_value: str, scrum_master_id: str, result_count: int):
        with self.connect() as conn:
            conn.execute(
                'INSERT INTO audit_runs(jql,pi_value,scrum_master_id,result_count,created_at) VALUES(?,?,?,?,?)',
                (jql, pi_value, scrum_master_id, result_count, datetime.now(timezone.utc).isoformat()),
            )
