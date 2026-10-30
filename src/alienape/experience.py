"""Compact empirical evidence. No prompts, files or model answers are stored here."""
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path
import hashlib
import json
import sqlite3
from .security import redactor, SafeConnection
import statistics


SUITE_VERSION = 3


def confidence(samples):
    return 'VERY_LOW' if samples<3 else 'LOW' if samples<5 else 'MEDIUM' if samples<10 else 'HIGHER'


class SuccessSignal(StrEnum):
    RESOURCE_FAILURE = 'RESOURCE_FAILURE'
    VERIFIED_SUCCESS = 'VERIFIED_SUCCESS'
    VERIFIED_FAILURE = 'VERIFIED_FAILURE'
    USER_CONFIRMED_SUCCESS = 'USER_CONFIRMED_SUCCESS'
    USER_CONFIRMED_FAILURE = 'USER_CONFIRMED_FAILURE'
    UNKNOWN = 'UNKNOWN'


def now():
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class ModelFingerprint:
    provider: str
    model: str
    endpoint: str
    tag: str | None = None
    model_version: str | None = None
    digest: str | None = None
    quantization: str | None = None
    backend_version: str | None = None
    temperature: float = 0
    max_output_tokens: int = 1024
    context_length: int | None = None
    context_budget: int = 16000
    tool_mode: str = 'none'
    attribution_version: str = 'v0.5'

    @classmethod
    def for_profile(cls, profile, metadata=None, context_budget=16000):
        meta = metadata or {}
        return cls(profile.provider, profile.model_name, profile.base_url,
                   profile.model_name.rsplit(':', 1)[1] if ':' in profile.model_name else None,
                   meta.get('model_version'), meta.get('digest'), meta.get('quantization'),
                   meta.get('backend_version'), context_length=profile.context_length or None,
                   context_budget=context_budget, tool_mode=profile.tool_mode)

    @property
    def json(self):
        return json.dumps(asdict(self), sort_keys=True, separators=(',', ':'), ensure_ascii=False)

    @property
    def id(self):
        return hashlib.sha256(self.json.encode()).hexdigest()


class ExperienceStore:
    def __init__(self, database):
        self.database = Path(database)
        self.database.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS model_fingerprints (
                    id TEXT PRIMARY KEY, identity_json TEXT NOT NULL, display_name TEXT NOT NULL,
                    stale INTEGER NOT NULL DEFAULT 0, stale_reason TEXT, created_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS model_profile_identity (
                    profile_id TEXT PRIMARY KEY, fingerprint_id TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS model_experience (
                    id INTEGER PRIMARY KEY, model_fingerprint_id TEXT NOT NULL,
                    task_category TEXT NOT NULL, task_subcategory TEXT, timestamp TEXT NOT NULL,
                    success_signal TEXT NOT NULL, success_value INTEGER, verified INTEGER NOT NULL,
                    latency_ms REAL, input_tokens INTEGER, output_tokens INTEGER, tokens_estimated INTEGER,
                    context_bytes INTEGER, model_calls INTEGER, tool_calls INTEGER, invalid_tool_calls INTEGER,
                    escalated INTEGER NOT NULL, source TEXT NOT NULL, benchmark_id TEXT,
                    suite_version INTEGER, session_id TEXT, parent_experience_id INTEGER UNIQUE,
                    energy_joules REAL, power_watts REAL, gpu_utilization REAL, vram_peak REAL);
                CREATE INDEX IF NOT EXISTS experience_lookup ON model_experience(model_fingerprint_id,task_category);
                CREATE TABLE IF NOT EXISTS model_performance_summary (
                    model_fingerprint TEXT NOT NULL, task_category TEXT NOT NULL,
                    sample_count INTEGER, hard_sample_count INTEGER, verified_success_count INTEGER,
                    verified_failure_count INTEGER, user_success_count INTEGER, user_failure_count INTEGER,
                    success_rate REAL, median_latency REAL, mean_latency REAL,
                    median_input_tokens REAL, median_output_tokens REAL, tokens_estimated INTEGER,
                    invalid_tool_rate REAL, escalation_rate REAL, last_updated TEXT,
                    PRIMARY KEY(model_fingerprint,task_category));
            ''')
            if 'validation_reason' not in {r[1] for r in db.execute('PRAGMA table_info(model_experience)')}:
                db.execute('ALTER TABLE model_experience ADD COLUMN validation_reason TEXT')
            if 'is_current' not in {r[1] for r in db.execute('PRAGMA table_info(model_experience)')}:
                db.execute('ALTER TABLE model_experience ADD COLUMN is_current INTEGER NOT NULL DEFAULT 1')
            if 'verification_json' not in {r[1] for r in db.execute('PRAGMA table_info(model_experience)')}:
                db.execute('ALTER TABLE model_experience ADD COLUMN verification_json TEXT')
            columns = {r[1] for r in db.execute('PRAGMA table_info(model_experience)')}
            for name,kind in {'failure_type':'TEXT','finish_reason':'TEXT','requested_output_limit':'INTEGER',
                'actual_output_tokens':'INTEGER','resource_retry_used':'INTEGER DEFAULT 0',
                'resource_retry_success':'INTEGER','required_output_budget_observed':'INTEGER'}.items():
                if name not in columns:
                    db.execute(f'ALTER TABLE model_experience ADD COLUMN {name} {kind}')
            columns={r[1] for r in db.execute('PRAGMA table_info(model_experience)')}
            for name,kind in {'task_domain':'TEXT','response_contract':'TEXT','information_complete':'INTEGER',
                              'protocol_normalized':'INTEGER','attribution_version':'TEXT'}.items():
                if name not in columns:db.execute(f'ALTER TABLE model_experience ADD COLUMN {name} {kind}')
            columns={r[1] for r in db.execute('PRAGMA table_info(model_experience)')}
            for name,kind in {'difficulty':'TEXT','verified_status':'TEXT','initial_requested_limit':'INTEGER',
                'successful_requested_limit':'INTEGER','actual_success_output_tokens':'INTEGER',
                'resource_retries':'INTEGER','escalations':'INTEGER','tool_observation_json':'TEXT','benchmark_task_id':'TEXT'}.items():
                if name not in columns:db.execute(f'ALTER TABLE model_experience ADD COLUMN {name} {kind}')
            columns={r[1] for r in db.execute('PRAGMA table_info(model_experience)')}
            for name in ['benchmark_level','benchmark_catalog_version','benchmark_run_id','capability_v2','language_v2','protocol_v2']:
                if name not in columns:db.execute(f'ALTER TABLE model_experience ADD COLUMN {name} TEXT')
            if 'protocol_status' not in columns:db.execute('ALTER TABLE model_experience ADD COLUMN protocol_status TEXT')
            db.execute('''CREATE TABLE IF NOT EXISTS benchmark_runs (
                id TEXT PRIMARY KEY,suite_version INTEGER,scope TEXT,status TEXT,started_at TEXT,finished_at TEXT,
                planned_tasks INTEGER,completed_tasks INTEGER,details_json TEXT)''')
            # Recompute on opening after a suite upgrade; routing only reads this small table.
            for row in db.execute('SELECT DISTINCT model_fingerprint_id,task_category FROM model_experience').fetchall():
                self._aggregate(db, *row)

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.database, timeout=5, factory=SafeConnection)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def register(self, profile, fingerprint):
        with self.connection() as db:
            previous = db.execute('SELECT fingerprint_id FROM model_profile_identity WHERE profile_id=?', (profile.id,)).fetchone()
            db.execute('INSERT OR IGNORE INTO model_fingerprints VALUES(?,?,?,0,NULL,?)',
                       (fingerprint.id, fingerprint.json, profile.display_name, now()))
            db.execute('UPDATE model_fingerprints SET display_name=? WHERE id=?', (profile.display_name, fingerprint.id))
            db.execute('INSERT OR REPLACE INTO model_profile_identity VALUES(?,?)', (profile.id, fingerprint.id))
            if previous and previous[0] != fingerprint.id:
                # Aliases still using the old concrete identity keep their own evidence current.
                aliases = db.execute('SELECT count(*) FROM model_profile_identity WHERE fingerprint_id=?', (previous[0],)).fetchone()[0]
                if not aliases:
                    db.execute('UPDATE model_fingerprints SET stale=1,stale_reason=? WHERE id=?', ('MODEL_OR_SETTINGS_CHANGED', previous[0]))
                    db.execute('UPDATE model_experience SET is_current=0 WHERE model_fingerprint_id=?', (previous[0],))
        return fingerprint.id

    def mark_stale(self, fingerprint_id, reason='MODEL_CHANGED'):
        with self.connection() as db:
            db.execute('UPDATE model_fingerprints SET stale=1,stale_reason=? WHERE id=?', (reason, fingerprint_id))
            db.execute('UPDATE model_experience SET is_current=0 WHERE model_fingerprint_id=?', (fingerprint_id,))

    def start_measurement(self, fingerprint_id):
        """Explicit new benchmark may renew a retired identity; prior evidence stays archived."""
        with self.connection() as db:
            db.execute('UPDATE model_fingerprints SET stale=0,stale_reason=NULL WHERE id=?', (fingerprint_id,))

    def record(self, fingerprint_id, category, signal=SuccessSignal.UNKNOWN, *, subcategory=None,
               latency_ms=None, input_tokens=None, output_tokens=None, tokens_estimated=None,
               context_bytes=None, model_calls=1, tool_calls=0, invalid_tool_calls=0, escalated=False,
               source='live', benchmark_id=None, suite_version=None, session_id=None, parent_experience_id=None,
               validation_reason=None, failure_type=None, finish_reason=None, requested_output_limit=None,
               actual_output_tokens=None, resource_retry_used=False, resource_retry_success=None,
               required_output_budget_observed=None,task_domain=None,response_contract=None,
               information_complete=None,protocol_normalized=None,attribution_version=None,difficulty=None,
               verified_status=None,initial_requested_limit=None,successful_requested_limit=None,verification=None,
               actual_success_output_tokens=None,resource_retries=None,escalations=None,tool_observation=None,benchmark_task_id=None,benchmark_level=None,benchmark_catalog_version=None,benchmark_run_id=None,capability_v2=None,language_v2=None,protocol_v2=None,protocol_status=None):
        if source in {'community','protocol_probe'}:
            raise ValueError('Community and probe evidence cannot be stored in local Experience.')
        if protocol_status not in {None,'VERIFIED_SUCCESS','VERIFIED_FAILURE'}:
            raise ValueError('Ungültige Protokollevidenz.')
        if protocol_status is not None:
            allowed=(source=='live' and response_contract=='FILE_CONTENT') or (source=='benchmark' and response_contract in {'FILE_CONTENT','TOOL_ACTION','STRUCTURED_JSON'})
            neutral=str(failure_type) in {'RESOURCE_OUTPUT_LIMIT','RESOURCE_CONTEXT_LIMIT','TIMEOUT','INFORMATION_MISSING','PERMISSION_FAILURE'} or information_complete is False or str(signal)=='RESOURCE_FAILURE'
            if not allowed or neutral:
                raise ValueError('Ungültige oder nicht zurechenbare Protokollevidenz.')
        signal = SuccessSignal(signal)
        if str(failure_type) in {'RESOURCE_OUTPUT_LIMIT','RESOURCE_CONTEXT_LIMIT'}:
            signal = SuccessSignal.RESOURCE_FAILURE
        elif attribution_version=='v0.5' and (information_complete is False or str(failure_type) in {'INFORMATION_MISSING','TOOL_FAILURE','PERMISSION_FAILURE','TIMEOUT'}):
            signal=SuccessSignal.UNKNOWN
            if information_complete is False and str(failure_type) not in {'TOOL_FAILURE','PERMISSION_FAILURE','TIMEOUT'}:
                failure_type='INFORMATION_MISSING'
        success = 1 if signal in {SuccessSignal.VERIFIED_SUCCESS, SuccessSignal.USER_CONFIRMED_SUCCESS} else 0 if signal in {SuccessSignal.VERIFIED_FAILURE, SuccessSignal.USER_CONFIRMED_FAILURE} else None
        verified = signal in {SuccessSignal.VERIFIED_SUCCESS, SuccessSignal.VERIFIED_FAILURE}
        with self.connection() as db:
            if not db.execute('SELECT 1 FROM model_fingerprints WHERE id=?', (fingerprint_id,)).fetchone():
                raise ValueError('Modellfingerprint fehlt.')
            cursor = db.execute('''INSERT INTO model_experience (
                model_fingerprint_id,task_category,task_subcategory,timestamp,success_signal,success_value,verified,
                latency_ms,input_tokens,output_tokens,tokens_estimated,context_bytes,model_calls,tool_calls,
                invalid_tool_calls,escalated,source,benchmark_id,suite_version,session_id,parent_experience_id)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                (fingerprint_id, str(category), subcategory, now(), str(signal), success, int(verified),
                 latency_ms, input_tokens, output_tokens, tokens_estimated, context_bytes, model_calls,
                 tool_calls, invalid_tool_calls, int(escalated), source, benchmark_id, suite_version,
                 session_id, parent_experience_id))
            identifier = cursor.lastrowid
            db.execute('UPDATE model_experience SET benchmark_level=?,benchmark_catalog_version=?,benchmark_run_id=?,capability_v2=?,language_v2=?,protocol_v2=? WHERE id=?',
                (benchmark_level,benchmark_catalog_version,benchmark_run_id,capability_v2,language_v2,protocol_v2,identifier))
            if protocol_status is not None:db.execute('UPDATE model_experience SET protocol_status=? WHERE id=?',(protocol_status,identifier))
            if verification is not None:
                db.execute('UPDATE model_experience SET verification_json=? WHERE id=?',(json.dumps(verification,ensure_ascii=False),identifier))
            db.execute('''UPDATE model_experience SET difficulty=?,verified_status=?,initial_requested_limit=?,successful_requested_limit=?,
                actual_success_output_tokens=?,resource_retries=?,escalations=?,tool_observation_json=?,benchmark_task_id=? WHERE id=?''',
                (difficulty,verified_status,initial_requested_limit,successful_requested_limit,actual_success_output_tokens,
                 resource_retries,escalations,json.dumps(tool_observation) if tool_observation is not None else None,benchmark_task_id,identifier))
            db.execute('UPDATE model_experience SET task_domain=?,response_contract=?,information_complete=?,protocol_normalized=?,attribution_version=? WHERE id=?',
                (task_domain,response_contract,information_complete,protocol_normalized,attribution_version,identifier))
            db.execute('UPDATE model_experience SET validation_reason=? WHERE id=?', (validation_reason, identifier))
            db.execute('''UPDATE model_experience SET failure_type=?,finish_reason=?,requested_output_limit=?,
                actual_output_tokens=?,resource_retry_used=?,resource_retry_success=?,required_output_budget_observed=? WHERE id=?''',
                (str(failure_type) if failure_type is not None else None,finish_reason,requested_output_limit,
                 actual_output_tokens,int(resource_retry_used),resource_retry_success,required_output_budget_observed,identifier))
            self._aggregate(db, fingerprint_id, str(category))
        return identifier

    def feedback(self, experience_id, success):
        with self.connection() as db:
            row = db.execute('SELECT * FROM model_experience WHERE id=? AND source=?', (experience_id, 'live')).fetchone()
            if not row:
                raise ValueError('Kein bewertbarer Modellversuch.')
            # One explicit opinion per attempt; changing it replaces that opinion.
            old = db.execute('SELECT id FROM model_experience WHERE parent_experience_id=?', (experience_id,)).fetchone()
            if old:
                db.execute('DELETE FROM model_experience WHERE id=?', (old[0],))
            signal = SuccessSignal.USER_CONFIRMED_SUCCESS if success else SuccessSignal.USER_CONFIRMED_FAILURE
            # User feedback supplements UNKNOWN. Objective validator evidence stays separate.
            if row['verified'] or row['failure_type'] in {'RESOURCE_OUTPUT_LIMIT','RESOURCE_CONTEXT_LIMIT'} or (row['attribution_version']=='v0.5' and (row['information_complete']==0 or row['failure_type'] in {'INFORMATION_MISSING','TOOL_FAILURE','PERMISSION_FAILURE','TIMEOUT'})):
                signal_value = None
            else:
                signal_value = int(success)
            cursor = db.execute('''INSERT INTO model_experience
                (model_fingerprint_id,task_category,timestamp,success_signal,success_value,verified,escalated,source,session_id,parent_experience_id)
                VALUES(?,?,?,?,?,0,0,'feedback',?,?)''',
                (row['model_fingerprint_id'], row['task_category'], now(), str(signal), signal_value, row['session_id'], experience_id))
            db.execute('UPDATE model_experience SET is_current=? WHERE id=?', (row['is_current'], cursor.lastrowid))
            db.execute('UPDATE model_experience SET task_domain=?,response_contract=?,information_complete=?,protocol_normalized=?,attribution_version=? WHERE id=?',
                (row['task_domain'],row['response_contract'],row['information_complete'],row['protocol_normalized'],row['attribution_version'],cursor.lastrowid))
            self._aggregate(db, row['model_fingerprint_id'], row['task_category'])
            return cursor.lastrowid

    def _aggregate(self, db, fingerprint_id, category):
        rows = db.execute('''SELECT * FROM model_experience WHERE model_fingerprint_id=? AND task_category=? AND is_current=1
            AND source NOT IN ('community','protocol_probe') AND (source!='benchmark' OR suite_version=?)''', (fingerprint_id, category, SUITE_VERSION)).fetchall()
        values = self._summary_values(fingerprint_id, category, rows)
        db.execute('INSERT OR REPLACE INTO model_performance_summary VALUES(' + ','.join('?' for _ in values) + ')', values)

    @staticmethod
    def _summary_values(fingerprint_id, category, rows,contract=False):
        attempts = [r for r in rows if r['source'] != 'feedback']
        neutral={'INFORMATION_MISSING','TOOL_FAILURE','PERMISSION_FAILURE','TIMEOUT','RESOURCE_OUTPUT_LIMIT','RESOURCE_CONTEXT_LIMIT'}
        if not contract:neutral.add('FORMAT_FAILURE')
        hard = [r for r in rows if r['success_value'] is not None and not (r['task_domain'] is not None and
            (r['information_complete']==0 or r['failure_type'] in neutral))]
        def median(key):
            values = [r[key] for r in attempts if r[key] is not None]
            return statistics.median(values) if values else None
        latency = [r['latency_ms'] for r in attempts if r['latency_ms'] is not None]
        tool_calls = sum(r['tool_calls'] or 0 for r in attempts)
        values = (fingerprint_id, category, len(attempts), len(hard),
            sum(r['success_signal'] == 'VERIFIED_SUCCESS' for r in hard),
            sum(r['success_signal'] == 'VERIFIED_FAILURE' for r in hard),
            sum(r['success_signal'] == 'USER_CONFIRMED_SUCCESS' for r in hard),
            sum(r['success_signal'] == 'USER_CONFIRMED_FAILURE' for r in hard),
            sum(r['success_value'] for r in hard) / len(hard) if hard else None,
            median('latency_ms'), statistics.mean(latency) if latency else None,
            median('input_tokens'), median('output_tokens'),
            int(any(r['tokens_estimated'] for r in attempts)),
            sum(r['invalid_tool_calls'] or 0 for r in attempts) / tool_calls if tool_calls else None,
            sum(r['escalated'] for r in attempts) / len(attempts) if attempts else None,
            max((r['timestamp'] for r in rows), default=now()))
        return values

    def summaries(self, include_stale=True):
        with self.connection() as db:
            # Rebuild also excludes prior suite versions after a software update.
            for row in db.execute('SELECT DISTINCT model_fingerprint_id,task_category FROM model_experience').fetchall():
                self._aggregate(db, *row)
            results = [dict(r) for r in db.execute('''SELECT s.*,f.display_name,f.stale,f.stale_reason,f.identity_json
                FROM model_performance_summary s JOIN model_fingerprints f ON f.id=s.model_fingerprint
                ''' + ('' if include_stale else 'WHERE f.stale=0 ') + 'ORDER BY f.display_name,s.task_category')]
            results = [r for r in results if r['sample_count']]
            if include_stale:
                columns = [r[1] for r in db.execute('PRAGMA table_info(model_performance_summary)')]
                groups = db.execute("SELECT DISTINCT model_fingerprint_id,task_category,suite_version,is_current FROM model_experience WHERE is_current=0 OR (source='benchmark' AND suite_version!=?)", (SUITE_VERSION,)).fetchall()
                for fp, category, version, current in groups:
                    rows = db.execute("SELECT * FROM model_experience WHERE model_fingerprint_id=? AND task_category=? AND suite_version IS ? AND is_current=?", (fp,category,version,current)).fetchall()
                    old = dict(zip(columns, self._summary_values(fp, category, rows)))
                    identity = dict(db.execute('SELECT display_name,identity_json FROM model_fingerprints WHERE id=?', (fp,)).fetchone())
                    reason = f'BENCHMARK_VERSION_CHANGED (Suite {version})' if version is not None and version != SUITE_VERSION else 'STALE_ARCHIVED'
                    results.append({**old, **identity, 'stale': 1, 'stale_reason': reason})
            return redactor.clean(results)

    def summary(self, fingerprint_id, category, response_contract=None, *, include_local=True):
        with self.connection() as db:
            if response_contract is not None or not include_local:
                if not db.execute('SELECT 1 FROM model_fingerprints WHERE id=? AND stale=0',(fingerprint_id,)).fetchone():return None
                rows=db.execute('''SELECT * FROM model_experience WHERE model_fingerprint_id=? AND task_category=? AND is_current=1
                    AND (? IS NULL OR response_contract=? OR response_contract IS NULL) AND source NOT IN ('community','protocol_probe') AND (source!='benchmark' OR suite_version=?)
                    AND (? OR source NOT IN ('live','feedback'))''',
                    (fingerprint_id,str(category),str(response_contract) if response_contract is not None else None,str(response_contract),SUITE_VERSION,int(include_local))).fetchall()
                if not rows:return None
                columns=[r[1] for r in db.execute('PRAGMA table_info(model_performance_summary)')]
                return dict(zip(columns,self._summary_values(fingerprint_id,str(category),rows,contract=True)))
            row = db.execute('''SELECT s.* FROM model_performance_summary s JOIN model_fingerprints f
                ON f.id=s.model_fingerprint WHERE f.stale=0 AND s.model_fingerprint=? AND s.task_category=?''',
                (fingerprint_id, str(category))).fetchone()
            return redactor.clean(dict(row)) if row else None

    def training_breakdown(self,fingerprint_id):
        """Independent domain/contract observations. No fabricated global score."""
        with self.connection() as db:
            rows=db.execute('''SELECT * FROM model_experience WHERE model_fingerprint_id=? AND is_current=1
                AND source NOT IN ('community','protocol_probe') AND (source!='benchmark' OR suite_version=?)''',(fingerprint_id,SUITE_VERSION)).fetchall()
        answer={'domains':{},'contracts':{}}
        for dimension,key in [('domains','task_domain'),('contracts','response_contract')]:
            for value in sorted({r[key] for r in rows if r[key]}):
                group=[r for r in rows if r[key]==value]
                neutral={'INFORMATION_MISSING','PERMISSION_FAILURE','TIMEOUT','RESOURCE_OUTPUT_LIMIT','RESOURCE_CONTEXT_LIMIT'}
                if dimension=='domains':neutral|={'FORMAT_FAILURE','TOOL_FAILURE'}
                hard=[r for r in group if r['information_complete']!=0 and r['failure_type'] not in neutral and
                    (r['verified_status'] or r['success_signal']) in {'VERIFIED_SUCCESS','VERIFIED_FAILURE','USER_CONFIRMED_SUCCESS','USER_CONFIRMED_FAILURE'}]
                success=sum((r['verified_status'] or r['success_signal']) in {'VERIFIED_SUCCESS','USER_CONFIRMED_SUCCESS'} for r in hard)
                answer[dimension][value]={'sample_count':len(group),'hard_sample_count':len(hard),'success_count':success,
                    'confidence':confidence(len(hard)),'sources':sorted({r['source'] for r in group}),
                    'format_failures':sum(r['failure_type']=='FORMAT_FAILURE' for r in group),
                    'content_failures':sum(r['failure_type']=='VALIDATION_FAILURE' for r in group)}
        return answer

    def benchmark_run(self,identifier,scope,status,planned,completed,details=None):
        with self.connection() as db:
            db.execute('''INSERT INTO benchmark_runs VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET
                status=excluded.status,finished_at=excluded.finished_at,completed_tasks=excluded.completed_tasks,details_json=excluded.details_json''',
                (identifier,SUITE_VERSION,scope,status,now(),None if status=='RUNNING' else now(),planned,completed,json.dumps(details or {})))
