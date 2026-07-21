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
                CREATE TABLE IF NOT EXISTS art_configurations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    project TEXT NOT NULL,
                    pi_value TEXT NOT NULL,
                    priority TEXT NOT NULL,
                    scrum_masters_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS pi_baselines (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    art_id INTEGER NOT NULL,
                    pi_value TEXT NOT NULL,
                    snapshot_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS pi_performance_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    art_id INTEGER NOT NULL,
                    pi_value TEXT NOT NULL,
                    committed_count INTEGER NOT NULL,
                    completed_count INTEGER NOT NULL,
                    yield_percent REAL NOT NULL,
                    total_story_points REAL NOT NULL DEFAULT 0,
                    completed_story_points REAL NOT NULL DEFAULT 0,
                    snapshot_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_pi_perf_art_pi ON pi_performance_snapshots(art_id,pi_value,created_at);
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

    def list_arts(self):
        with self.connect() as conn:
            rows = conn.execute('SELECT * FROM art_configurations ORDER BY name').fetchall()
        result=[]
        for row in rows:
            item=dict(row)
            item['scrum_masters']=json.loads(item.pop('scrum_masters_json') or '[]')
            result.append(item)
        return result

    def get_art(self, art_id: int):
        with self.connect() as conn:
            row=conn.execute('SELECT * FROM art_configurations WHERE id=?',(art_id,)).fetchone()
        if not row: return None
        item=dict(row); item['scrum_masters']=json.loads(item.pop('scrum_masters_json') or '[]')
        return item

    def save_art(self, data):
        now=datetime.now(timezone.utc).isoformat()
        payload=json.dumps(data.get('scrum_masters',[]))
        with self.connect() as conn:
            if data.get('id'):
                conn.execute('UPDATE art_configurations SET name=?,project=?,pi_value=?,priority=?,scrum_masters_json=?,updated_at=? WHERE id=?',
                    (data['name'],data['project'],data['pi_value'],data['priority'],payload,now,int(data['id'])))
                return int(data['id'])
            cur=conn.execute('INSERT INTO art_configurations(name,project,pi_value,priority,scrum_masters_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?)',
                (data['name'],data['project'],data['pi_value'],data['priority'],payload,now,now))
            return int(cur.lastrowid)

    def delete_art(self, art_id:int):
        with self.connect() as conn:
            conn.execute('DELETE FROM art_configurations WHERE id=?',(art_id,))

    def save_baseline(self, art_id:int, pi_value:str, snapshot):
        now=datetime.now(timezone.utc).isoformat()
        with self.connect() as conn:
            cur=conn.execute('INSERT INTO pi_baselines(art_id,pi_value,snapshot_json,created_at) VALUES(?,?,?,?)',
                (art_id,pi_value,json.dumps(snapshot),now))
            return int(cur.lastrowid)

    def latest_baseline(self, art_id:int, pi_value:str):
        with self.connect() as conn:
            row=conn.execute('SELECT * FROM pi_baselines WHERE art_id=? AND pi_value=? ORDER BY created_at DESC LIMIT 1',(art_id,pi_value)).fetchone()
        if not row:return None
        item=dict(row); item['snapshot']=json.loads(item.pop('snapshot_json'))
        return item


    def save_pi_performance_snapshot(self, art_id: int, pi_value: str, metrics: dict):
        now = datetime.now(timezone.utc).isoformat()
        sql = "INSERT INTO pi_performance_snapshots(art_id,pi_value,committed_count,completed_count,yield_percent,total_story_points,completed_story_points,snapshot_json,created_at) VALUES(?,?,?,?,?,?,?,?,?)"
        values = (art_id, pi_value, int(metrics.get('committed_count', 0)), int(metrics.get('completed_count', 0)), float(metrics.get('yield_percent', 0)), float(metrics.get('total_story_points', 0)), float(metrics.get('completed_story_points', 0)), json.dumps(metrics), now)
        with self.connect() as conn:
            cur = conn.execute(sql, values)
            return int(cur.lastrowid)

    def list_pi_performance_snapshots(self, art_id: int | None = None):
        sql = 'SELECT * FROM pi_performance_snapshots'
        params = ()
        if art_id is not None:
            sql += ' WHERE art_id=?'
            params = (art_id,)
        sql += ' ORDER BY created_at ASC'
        with self.connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item['snapshot'] = json.loads(item.pop('snapshot_json'))
            result.append(item)
        return result

    def latest_pi_performance_by_pi(self, art_id: int):
        latest = {}
        for row in self.list_pi_performance_snapshots(art_id):
            latest[row['pi_value']] = row
        return list(latest.values())
