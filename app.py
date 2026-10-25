#!/usr/bin/env python3
"""Local firmware catalog and durable requests for the T3 controller."""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import uuid

ROOT = Path(__file__).resolve().parent
TERMINAL = {'verified_complete', 'cancelled'}


def now():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


class Problem(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


class ClosingConnection(sqlite3.Connection):
    def __exit__(self, *args):
        try:
            return super().__exit__(*args)
        finally:
            self.close()


class Store:
    def __init__(self, root=ROOT, firmware=None, projects=None):
        self.root = Path(root)
        self.data = self.root / 'data'
        self.data.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.firmware = Path(firmware or '/home/vesper/firmware_downloads').resolve()
        self.projects = Path(projects or '/home/vesper/RE').resolve()
        self.db = self.data / 'launcher.sqlite'
        with self.connect() as c:
            c.execute('''CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY, batch TEXT NOT NULL, slug TEXT UNIQUE NOT NULL,
                name TEXT NOT NULL, state TEXT NOT NULL, created TEXT NOT NULL,
                updated TEXT NOT NULL, request TEXT NOT NULL, project_id TEXT,
                thread_id TEXT UNIQUE, launch_result TEXT, observation TEXT,
                review TEXT, dispatch TEXT, error TEXT, port INTEGER UNIQUE NOT NULL)''')
            c.execute('''CREATE TABLE IF NOT EXISTS batches (
                id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, created TEXT NOT NULL)''')
            c.execute('''CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY, job TEXT, time TEXT, kind TEXT, detail TEXT)''')
            c.execute('''CREATE TABLE IF NOT EXISTS checks (
                id TEXT PRIMARY KEY, job TEXT, time TEXT, command TEXT,
                exit_code INTEGER, log_path TEXT, log_sha256 TEXT)''')
        os.chmod(self.db, 0o600)
        self.catalog = self.load_catalog()
        from engine import initialize
        initialize(self)

    def connect(self):
        c = sqlite3.connect(self.db, timeout=15, factory=ClosingConnection)
        c.row_factory = sqlite3.Row
        return c

    def config(self):
        p = self.data / 'config.json'
        return json.loads(p.read_text()) if p.exists() else {}

    def capabilities(self):
        p = self.data / 'capabilities.json'
        if not p.exists():
            raise Problem('The controller has not loaded the T3 model catalog yet.', 503)
        return json.loads(p.read_text())

    def load_catalog(self):
        sources = {}
        source_file = self.firmware / 'SOURCES.tsv'
        if source_file.exists():
            with source_file.open() as f:
                for row in csv.DictReader(f, delimiter='\t'):
                    sources[row['path'].removeprefix('./')] = row.get('source_url', '')
        entries = {}
        manifest = self.firmware / 'MANIFEST.sha256'
        if manifest.exists():
            for line in manifest.read_text().splitlines():
                if not line.strip():
                    continue
                digest, relative = line.split(maxsplit=1)
                relative = relative.lstrip('*').removeprefix('./')
                p = (self.firmware / relative).resolve()
                if not p.is_relative_to(self.firmware) or not p.is_file():
                    continue
                entries[relative] = dict(path=relative, name=p.name, vendor=relative.split('/')[0],
                    bytes=p.stat().st_size, sha256=digest, source=sources.get(relative, ''),
                    kind='reference' if p.suffix.lower() in {'.pdf', '.txt', '.md', '.html'} else 'firmware')
        return entries

    def model_selection(self, value):
        if not isinstance(value, dict):
            raise Problem('Choose a provider and model.')
        provider = next((p for p in self.capabilities()['providers']
                         if p['providerInstanceId'] == value.get('instanceId')), None)
        if not provider or provider.get('constraints'):
            raise Problem('; '.join(provider.get('constraints', [])) if provider else 'Unknown provider.')
        model = next((m for m in provider['models'] if m['id'] == value.get('model')), None)
        if not model:
            raise Problem('The selected model is not in the current T3 catalog.')
        selected = value.get('options', {})
        if not isinstance(selected, dict):
            raise Problem('Model settings must be an object.')
        descriptors = {d['id']: d for d in model.get('options') or []}
        if set(selected) != set(descriptors):
            raise Problem('Choose an explicit value for every model setting.')
        for key, v in selected.items():
            d = descriptors[key]
            if d['type'] == 'boolean':
                valid = type(v) is bool
            else:
                valid = isinstance(v, str) and v in {x['id'] for x in d['options']}
            if not valid:
                raise Problem('Unsupported value for model setting ' + key)
        return {'instanceId': provider['providerInstanceId'], 'model': model['id'], 'options': selected}

    def validate_group(self, group, selection):
        name = str(group.get('name', '')).strip()
        slug = str(group.get('slug', '')).strip()
        if not name or len(name) > 100 or not re.fullmatch(r'[a-z0-9][a-z0-9_-]{1,63}', slug):
            raise Problem('Supply a device name and a 2–64 character lowercase directory name.')
        files = group.get('files', [])
        if not isinstance(files, list) or not 1 <= len(files) <= 150:
            raise Problem('Each device needs 1–150 selected files.')
        chosen, seen = [], set()
        for entry in files:
            if not isinstance(entry, dict) or entry.get('path') not in self.catalog:
                raise Problem('A selected path is outside the firmware catalog.')
            path = entry['path']
            if path in seen:
                raise Problem('A file was selected twice in one device group.')
            seen.add(path)
            source = (self.firmware / path).resolve()
            if not source.is_relative_to(self.firmware) or not source.is_file():
                raise Problem('A selected source file is no longer available.')
            role = entry.get('kind', 'firmware')
            if role not in ('firmware', 'reference'):
                raise Problem('File role must be firmware or reference.')
            chosen.append({**self.catalog[path], 'kind': role})
        if not any(f['kind'] == 'firmware' for f in chosen):
            raise Problem('Each device group needs at least one firmware candidate.')
        if (self.projects / slug).exists():
            raise Problem('The destination already exists: ' + str(self.projects / slug), 409)
        policy = group.get('subagents', 'none')
        cap = group.get('subagentCap', 0)
        if policy not in ('none', 'integration', 'capped') or type(cap) is not int or not 0 <= cap <= 8:
            raise Problem('Invalid subagent policy or cap (0–8).')
        if policy == 'capped' and cap < 1:
            raise Problem('Capped subagents require a positive cap.')
        pool = group.get('quotaPool', 'claude' if selection['instanceId']=='claudeAgent' else 'codex')
        if pool not in ('claude','codex'):
            raise Problem('Choose the actual quota pool: claude or codex.')
        return dict(name=name, slug=slug, files=chosen, selection=selection,
                    subagents=policy, subagentCap=cap, quotaPool=pool,
                    targetHost=str(group.get('targetHost', '')).strip()[:500])

    def enqueue(self, payload):
        if not isinstance(payload, dict):
            raise Problem('Expected a launch request.')
        try:
            batch = str(uuid.UUID(payload['requestId']))
        except (KeyError, ValueError, TypeError, AttributeError):
            raise Problem('A valid request ID is required.')
        fingerprint = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
        # Resolve idempotent retries before filesystem checks: the first request may have prepared it.
        with self.connect() as c:
            previous = c.execute('SELECT fingerprint FROM batches WHERE id=?', (batch,)).fetchone()
            if previous:
                if previous[0] != fingerprint:
                    raise Problem('This request ID was already used for different inputs.', 409)
                return [self.decode(r) for r in c.execute('SELECT * FROM jobs WHERE batch=?', (batch,))]
        selection = self.model_selection(payload.get('selection'))
        groups = payload.get('groups', [])
        if not isinstance(groups, list) or not 1 <= len(groups) <= 10:
            raise Problem('Launch between 1 and 10 device groups at a time.')
        maximum = payload.get('maxParallel', 2)
        if type(maximum) is not int or not 1 <= maximum <= 4:
            raise Problem('Concurrent runs must be between 1 and 4.')
        validated = [self.validate_group(g, selection) for g in groups]
        if len({g['slug'] for g in validated}) != len(validated):
            raise Problem('Device directories must be different.')
        try:
            with self.connect() as c:
                c.execute('BEGIN IMMEDIATE')
                # The transaction also handles two simultaneous HTTP retries.
                previous = c.execute('SELECT fingerprint FROM batches WHERE id=?', (batch,)).fetchone()
                if previous:
                    if previous[0] != fingerprint:
                        raise Problem('Request ID conflict.', 409)
                    return [self.decode(r) for r in c.execute('SELECT * FROM jobs WHERE batch=?', (batch,))]
                c.execute('INSERT INTO batches VALUES (?,?,?)', (batch, fingerprint, now()))
                used = {r[0] for r in c.execute('SELECT port FROM jobs')}
                for group in validated:
                    port = next(p for p in range(19100, 20000) if p not in used)
                    used.add(port)
                    identifier = str(uuid.uuid4())
                    request = {**group, 'id': identifier, 'maxParallel': maximum, 'port': port,
                               'destination': str(self.projects / group['slug']), 'createdAt': now()}
                    c.execute('''INSERT INTO jobs
                        (id,batch,slug,name,state,created,updated,request,port)
                        VALUES (?,?,?,?,?,?,?,?,?)''',
                        (identifier,batch,group['slug'],group['name'],'queued',now(),now(),json.dumps(request),port))
                return [self.decode(r) for r in c.execute('SELECT * FROM jobs WHERE batch=?', (batch,))]
        except sqlite3.IntegrityError:
            raise Problem('This device already has a launcher job. Open its existing run.', 409)

    @staticmethod
    def decode(row):
        result = dict(row)
        for k in ['request', 'launch_result', 'observation', 'review']:
            if result.get(k):
                result[k] = json.loads(result[k])
        if result.get('thread_id'):
            result['threadUrl'] = 't3-thread://v1/' + result['thread_id']
        return result

    def jobs(self):
        with self.connect() as c:
            return [self.decode(r) for r in c.execute('SELECT * FROM jobs ORDER BY created DESC')]

    def job(self, identifier):
        with self.connect() as c:
            r = c.execute('SELECT * FROM jobs WHERE id=?', (identifier,)).fetchone()
        if not r:
            raise Problem('Unknown launcher job.', 404)
        return self.decode(r)

    def patch(self, identifier, **values):
        allowed = {'state','project_id','thread_id','launch_result','observation','review','dispatch','error'}
        if not set(values) <= allowed:
            raise Problem('Invalid update fields.')
        encoded = {k: json.dumps(v) if isinstance(v,(dict,list)) else v for k,v in values.items()}
        encoded['updated'] = now()
        with self.connect() as c:
            c.execute('UPDATE jobs SET '+','.join(k+'=?' for k in encoded)+' WHERE id=?', [*encoded.values(),identifier])
            c.execute('INSERT INTO events(job,time,kind,detail) VALUES (?,?,?,?)',
                      (identifier,now(),'update',json.dumps(values)))
        return self.job(identifier)

    def cli(self, *args, input=None):
        binary = self.config().get('t3Binary')
        if not binary or not Path(binary).is_file():
            raise Problem('The T3 CLI path is unavailable.', 503)
        try:
            r = subprocess.run([binary,*args],input=input,text=True,capture_output=True,timeout=45)
        except subprocess.TimeoutExpired:
            raise Problem('T3 did not acknowledge the request. Delivery is uncertain; the controller will reconcile the saved job.', 503)
        if r.returncode:
            raise Problem('T3 could not accept the request: '+r.stderr.strip()[:500],503)
        return r.stdout

    def dispatch(self, batch=None, refresh=False):
        from engine import wake
        return wake(self, reason='refresh catalog only' if refresh else 'approved queue changed')


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--port',type=int,default=8787)
    args=parser.parse_args()
    from dashboard import serve
    serve(Store(),args.port)


if __name__=='__main__':main()
