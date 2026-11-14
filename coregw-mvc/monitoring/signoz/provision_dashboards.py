#!/usr/bin/env python3
"""Validate and idempotently provision the local SigNoz dashboards; back up existing JSON."""
import argparse
import datetime
import json
import os
import time
from pathlib import Path
import urllib.error
import urllib.request

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
CACHE = ROOT / '.cache/signoz'


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--url', default='http://localhost:3301')
    p.add_argument('--validate-only', action='store_true')
    args = p.parse_args()
    login_file = CACHE / 'admin-login.json'
    credentials = json.loads(login_file.read_text()) if login_file.exists() else {}
    email = os.environ.get('SIGNOZ_EMAIL', credentials.get('email'))
    password = os.environ.get('SIGNOZ_PASSWORD', credentials.get('password'))
    org = os.environ.get('SIGNOZ_ORG_ID', credentials.get('user', {}).get('data', {}).get('orgId'))

    retry_count = 0

    def request(path, data=None, method=None, token=None):
        nonlocal retry_count
        headers = {'Content-Type': 'application/json'}
        if token:
            headers['Authorization'] = 'Bearer ' + token
        body = None if data is None else json.dumps(data).encode()
        req = urllib.request.Request(args.url + path, data=body, method=method, headers=headers)
        for attempt in range(3):
            try:
                return json.load(urllib.request.urlopen(req, timeout=60))
            except urllib.error.HTTPError as error:
                detail = error.read().decode()
                # Retry read-only validation queries on transient backend overload; never retry mutations.
                if path == '/api/v5/query_range' and error.code in (500, 502, 503, 504) and attempt < 2:
                    retry_count += 1
                    time.sleep(0.5 * (attempt + 1))
                    continue
                raise RuntimeError(f'{method or ("POST" if data is not None else "GET")} {path}: HTTP {error.code}: {detail}') from error

    session_path = CACHE / 'provision-session.json'
    session = json.loads(session_path.read_text()) if session_path.exists() else {}
    config = request('/api/v1/global/config')['data']
    if config.get('identN', {}).get('impersonation', {}).get('enabled'):
        token = None
    elif not email or not password:
        p.error('Provide SIGNOZ_EMAIL / SIGNOZ_PASSWORD (and SIGNOZ_ORG_ID), or enable local impersonation mode')
    elif session.get('email') == email and session.get('expiresAt', 0) > time.time() + 30:
        token = session['accessToken']
    else:
        auth = request('/api/v2/sessions/email_password', {'email': email, 'password': password, 'orgId': org})['data']
        token = auth['accessToken']
        CACHE.mkdir(parents=True, exist_ok=True)
        session_path.write_text(json.dumps({'email': email, 'accessToken': token,
                                            'expiresAt': time.time() + auth['expiresIn']}))
        session_path.chmod(0o600)
    end = int(datetime.datetime.now().timestamp() * 1000)
    variables = {k: {'type': 'custom' if k == 'window' else 'dynamic', 'value': v} for k, v in {
        'service': 'coregw-mvc', 'instance': '__all__', 'mode': '__all__', 'environment': '__all__', 'redis_instance': '__all__'}.items()}
    cache_state = CACHE / 'dashboard-instances.json'
    state = json.loads(cache_state.read_text()) if cache_state.exists() else {}
    legacy = CACHE / 'dashboard-instance.json'
    if 'dashboard.json' not in state and legacy.exists():
        old = json.loads(legacy.read_text())['data']
        state['dashboard.json'] = {'id': old['id'], 'name': old['name']}
    report = []
    backup = CACHE / 'backups' / datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    for filename in ['dashboard.json', 'redis-dashboard.json']:
        dashboard = json.loads((HERE / filename).read_text())
        for key, panel in dashboard['spec']['panels'].items():
            query = panel['spec']['queries'][0]
            spec = query['spec']['plugin']['spec']
            payload = {'schemaVersion': 'v1', 'start': end - 300000, 'end': end, 'requestType': query['kind'],
                       'variables': variables, 'compositeQuery': {'queries': [{'type': 'promql', 'spec': {**spec, 'step': 5}}]}}
            retries_before = retry_count
            try:
                response = request('/api/v5/query_range', payload, token=token)
            except RuntimeError as error:
                report.append({'dashboard': filename, 'panel': key, 'status': 'error', 'error': str(error)})
                CACHE.mkdir(parents=True, exist_ok=True)
                (CACHE / 'dashboard-validation.json').write_text(json.dumps(report, indent=2))
                raise
            results = response.get('data', {}).get('data', {}).get('results') or []
            values = sum(len(r.get('data') or []) for r in results)
            series = sum(len(aggregation.get('series') or []) for r in results for aggregation in (r.get('aggregations') or []))
            report.append({'dashboard': filename, 'panel': key, 'title': panel['spec']['display']['name'],
                           'status': response.get('status'), 'series': series, 'scalarRows': values,
                           'transientRetries': retry_count - retries_before})
        if not args.validate_only:
            previous = state.get(filename)
            if previous:
                existing = request('/api/v2/dashboards/' + previous['id'], token=token)['data']
                backup.mkdir(parents=True, exist_ok=True)
                (backup / filename).write_text(json.dumps(existing, indent=2))
                update = dict(dashboard)
                update.pop('generateName', None)
                update['name'] = existing['name']
                saved = request('/api/v2/dashboards/' + previous['id'], update, 'PUT', token)['data']
            else:
                saved = request('/api/v2/dashboards', dashboard, 'POST', token)['data']
            state[filename] = {'id': saved['id'], 'name': saved['name'],
                               'url': args.url + '/dashboard/' + saved['id']}
            CACHE.mkdir(parents=True, exist_ok=True)
            cache_state.write_text(json.dumps(state, indent=2))
            print(filename, state[filename]['url'])
    CACHE.mkdir(parents=True, exist_ok=True)
    (CACHE / 'dashboard-validation.json').write_text(json.dumps(report, indent=2))
    print(f'Validated {len(report)} queries; transient backend retries: {retry_count}. Missing series are not fake zeros.')


if __name__ == '__main__':
    main()
