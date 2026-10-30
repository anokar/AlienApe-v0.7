"""SQLite-owned persistence; short independent transactions, no hidden chat archive."""
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
import json
import re
import sqlite3
from .security import redactor, transport_redactor, SecretRedactor

TYPES = ("USER_DECISION", "PROJECT_RULE", "FACT", "RESULT", "POINTER")


def now():
    return datetime.now(timezone.utc).isoformat()


def sensitive_memory_row(row):
    """Retrieval-only policy: inspect without registering values or changing stored rows."""
    value=' '.join(str(row.get(k,'') or '') for k in ('logical_key','content','source','metadata_json'))
    plain=SecretRedactor()
    return bool(plain.text(value)!=value or transport_redactor.text(value)!=value or
        re.search(r'(?i)\b(?:api[ _-]?key|access[ _-]?token|refresh[ _-]?token|bearer|password|passwort|passwd|private[ _-]?key|credential|zugangsdaten)\b',value) or
        re.search(r'(?i)\bsk-(?:proj-|svcacct-)?[A-Za-z0-9_-]{12,}',value))

def safe_memory_rows(rows):
    result=[]
    for row in rows:
        row=dict(row)
        if sensitive_memory_row(row):
            row.update(logical_key='[SENSIBEL]',content='Ein sensibler Memory-Eintrag wird nicht im Klartext ausgegeben.',source='[SENSIBEL]',metadata_json='{}',sensitivity='SENSITIVE')
        else:row['sensitivity']='SAFE'
        result.append(row)
    return result

class MemoryConnection(sqlite3.Connection):
    """Persist/retrieve with credential policy, independent of diagnostic-only registrations."""
    def execute(self,sql,parameters=()):return super().execute(sql,transport_redactor.clean(parameters))
    def executemany(self,sql,parameters):return super().executemany(sql,(transport_redactor.clean(p) for p in parameters))

class MemoryStore:
    def __init__(self, database, project_id):
        self.database, self.project_id = Path(database), project_id
        self.database.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS memory (
                id INTEGER PRIMARY KEY, project_id TEXT NOT NULL, logical_key TEXT NOT NULL,
                type TEXT NOT NULL, content TEXT NOT NULL, source TEXT NOT NULL,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL, last_accessed TEXT,
                access_count INTEGER NOT NULL DEFAULT 0, superseded INTEGER NOT NULL DEFAULT 0,
                reconstructable INTEGER NOT NULL DEFAULT 0, metadata_json TEXT NOT NULL DEFAULT '{}',
                active INTEGER NOT NULL DEFAULT 1, important INTEGER NOT NULL DEFAULT 0)""")
            db.execute("CREATE INDEX IF NOT EXISTS memory_project_key ON memory(project_id, logical_key)")

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.database, timeout=5, factory=MemoryConnection)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def put(self, logical_key, kind, content, source="Benutzer", reconstructable=False, metadata=None):
        if redactor.sensitive_memory(content) or redactor.sensitive_memory(logical_key):
            raise ValueError('SECRET_POLICY_BLOCK: Zugangsdaten werden nicht als Memory gespeichert.')
        key = logical_key.strip().casefold()
        content = content.strip()
        if kind not in TYPES or not key or not content:
            raise ValueError("Memory benötigt gültigen Typ, Schlüssel und Inhalt.")
        if len(key) > 200 or len(content) > 8000:
            raise ValueError("Memory zu lang: Schlüssel maximal 200, Inhalt maximal 8000 Zeichen.")
        timestamp = now()
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            old = db.execute("SELECT * FROM memory WHERE project_id=? AND logical_key=? AND superseded=0 AND active=1", (self.project_id, key)).fetchall()
            for row in old:
                if row["content"] == content and row["type"] == kind:
                    return {**dict(row), "changed": False, "superseded_ids": []}
            db.execute("UPDATE memory SET superseded=1, updated_at=? WHERE project_id=? AND logical_key=? AND superseded=0", (timestamp, self.project_id, key))
            cursor = db.execute("""INSERT INTO memory(project_id,logical_key,type,content,source,created_at,updated_at,reconstructable,metadata_json)
                VALUES(?,?,?,?,?,?,?,?,?)""", (self.project_id, key, kind, content, source, timestamp, timestamp, int(reconstructable), json.dumps(metadata or {})))
            return {"id": cursor.lastrowid, "changed": True, "superseded_ids": [r["id"] for r in old]}

    def all(self):
        with self.connection() as db:
            return redactor.clean(safe_memory_rows([dict(row) for row in db.execute("SELECT * FROM memory WHERE project_id=? ORDER BY important DESC, updated_at DESC, id DESC", (self.project_id,))]))

    def overview(self, limit=50):
        """Active current-project memories only; no keyword needed for 'show all'."""
        limit = max(1, min(int(limit), 50))
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            where = "project_id=? AND superseded=0 AND active=1"
            total = db.execute("SELECT count(*) FROM memory WHERE " + where, (self.project_id,)).fetchone()[0]
            rows = [dict(row) for row in db.execute(
                "SELECT * FROM memory WHERE " + where + " ORDER BY important DESC, updated_at DESC, id DESC LIMIT ?",
                (self.project_id, limit))]
            timestamp = now()
            for row in rows:
                if sensitive_memory_row(row):continue
                db.execute("UPDATE memory SET access_count=access_count+1,last_accessed=? WHERE id=? AND project_id=?",
                           (timestamp, row["id"], self.project_id))
            return redactor.clean(safe_memory_rows(rows)), total

    def retrieve(self, query, limit=5, kind=None):
        # Parameterized keyword retrieval: bounded, no embeddings or query-language surprises.
        stopwords = {"wir", "die", "der", "das", "eine", "einen", "welche", "welchen", "verwenden", "version", "haben", "und", "für", "mit"}
        words = [w for w in re.findall(r"\w+", query.casefold()) if len(w) > 2 and w not in stopwords][:16]
        if not words:
            return []
        clauses, args = [], [self.project_id]
        for word in words:
            escaped = word.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            clauses.append("(lower(logical_key) LIKE ? ESCAPE '\\' OR lower(content) LIKE ? ESCAPE '\\')")
            args.extend([f"%{escaped}%", f"%{escaped}%"])
        sql = "SELECT * FROM memory WHERE project_id=? AND superseded=0 AND active=1 AND (" + " OR ".join(clauses) + ")"
        if kind:
            sql += " AND type=?"
            args.append(kind)
        sql += " ORDER BY important DESC, updated_at DESC, access_count DESC, id DESC LIMIT ?"
        args.append(max(1, min(limit, 20)))
        with self.connection() as db:
            rows = [dict(row) for row in db.execute(sql, args)]
            for row in rows:
                if sensitive_memory_row(row):continue
                db.execute("UPDATE memory SET access_count=access_count+1,last_accessed=? WHERE id=? AND project_id=?", (now(), row["id"], self.project_id))
            return transport_redactor.clean(safe_memory_rows(rows))

    def update_flags(self, identifier, *, active=None, important=None):
        with self.connection() as db:
            if active is not None:
                db.execute("UPDATE memory SET active=?,updated_at=? WHERE id=? AND project_id=?", (int(active), now(), identifier, self.project_id))
            if important is not None:
                db.execute("UPDATE memory SET important=?,updated_at=? WHERE id=? AND project_id=?", (int(important), now(), identifier, self.project_id))

    def delete(self, identifier):
        with self.connection() as db:
            db.execute("DELETE FROM memory WHERE id=? AND project_id=?", (identifier, self.project_id))
