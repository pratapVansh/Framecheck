"""Assert the real-image HTTP flow and reopen saved evidence in a new process."""
from contextlib import contextmanager
import hashlib
import http.client
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
from uuid import uuid4

from inspector.benchmark import read_manifest
from inspector.images import decode_image
from inspector.web import FORM_FIELDS, available_profiles

ROOT = Path(__file__).resolve().parents[1]
CHILD = """
import sys, threading
from inspector.web import make_server
server = make_server(0, sys.argv[1])
thread = threading.Thread(target=server.serve_forever, daemon=True)
thread.start()
print(server.server_port, flush=True)
sys.stdin.readline()
server.shutdown()
thread.join()
server.server_close()
"""


@contextmanager
def server(database, log):
    with log.open('a', encoding='utf-8') as errors:
        process = subprocess.Popen([sys.executable, '-u', '-c', CHILD, str(database)], cwd=ROOT,
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=errors,
                                   text=True, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        try:
            port = int(process.stdout.readline().strip())
            yield port, process.pid
        finally:
            process.communicate('\n', timeout=15)
            assert process.returncode == 0


def request(port, method, path, body=None, headers=None):
    connection = http.client.HTTPConnection('127.0.0.1', port, timeout=15)
    try:
        connection.request(method, path, body, headers or {})
        response = connection.getresponse()
        return response.status, dict(response.getheaders()), response.read()
    finally:
        connection.close()


def multipart(fields, payload):
    boundary = 'framecheck-final-' + uuid4().hex
    parts = [f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n{value}\r\n'.encode()
             for key, value in fields.items()]
    parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="image"; filename="verification.png"\r\nContent-Type: image/png\r\n\r\n'.encode() + payload + b'\r\n')
    parts.append(f'--{boundary}--\r\n'.encode())
    return b''.join(parts), {'Content-Type': 'multipart/form-data; boundary=' + boundary}


def main():
    report_path = ROOT / 'reports/phase6-app-verification.json'
    database = ROOT / 'reports/phase6-app-check.sqlite3'
    if report_path.exists() or database.exists():
        raise ValueError('Verification artifacts already exist; preserve this completed run.')
    database.parent.mkdir(exist_ok=True)
    protected = [ROOT / name for name in ('data/inspections.sqlite3', 'data/benchmark.jsonl', 'data/calibrated-profile.json')]
    hashes = {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in protected if path.exists()}
    rows = read_manifest(ROOT / 'data/benchmark.jsonl')
    sources = {row['id']: row for row in rows if row['kind'] == 'original'}
    evaluation = json.loads((ROOT / 'reports/test-evaluation.json').read_text())
    predicted = {row['id']: row['status'] for row in evaluation['predictions']}
    cases = [('38954', 'isolated_product', 'Demo White Background', 'Pass'),
             ('5003', 'isolated_product', 'Demo White Background', 'Fail'),
             ('22608', 'isolated_product', 'Demo White Background', 'Manual Review'),
             ('44892', 'model_worn', 'Demo White Background', 'Manual Review'),
             ('44892', 'uncertain', 'Demo White Background', 'Manual Review')]
    sample = next(row for row in rows if row['split'] == 'test' and row['kind'] == 'synthetic' and row['transforms'][0]['defect'] == 'low_resolution')
    cases.append((sample['id'], sample['photo_context'], 'calibrated-profile', predicted[sample['id']]))
    by_id = {row['id']: row for row in rows}
    saved, pids = [], []
    with server(database, ROOT / 'reports/phase6-http.log') as (port, pid):
        pids.append(pid)
        code, _, home = request(port, 'GET', '/?profile=calibrated-profile')
        assert code == 200 and b'calibrated-profile' in home and b'step="any"' in home
        for image_id, context, profile_name, expected in cases:
            identity = str(uuid4())
            profile = available_profiles()[profile_name]
            fields = dict(submission_id=identity, profile=profile_name, photo_context=context)
            fields.update({key: str(getattr(profile, key)) for key in FORM_FIELDS})
            body, headers = multipart(fields, Path(by_id[image_id]['image']).read_bytes())
            for _ in range(2):
                code, response_headers, _ = request(port, 'POST', '/inspect', body, headers)
                assert code == 303 and response_headers['Location'] == '/inspection/' + identity
            code, _, raw = request(port, 'GET', '/report/' + identity + '.json')
            result = json.loads(raw)
            assert code == 200 and result['status'] == expected and len(result['checks']) == 12
            assert result['photo_context'] == context and result['profile']['calibrated'] == profile.calibrated
            assert all(check['explanation'] for check in result['checks'])
            assert request(port, 'GET', '/inspection/' + identity)[0] == 200
            evidence = {}
            for kind in ('original', 'mask', 'overlay'):
                code, _, blob = request(port, 'GET', f'/evidence/{identity}/{kind}.png')
                pixels, _ = decode_image(blob)
                assert code == 200 and pixels.shape[:2] == (result['height'], result['width'])
                evidence[kind] = hashlib.sha256(blob).hexdigest()
            saved.append(dict(id=identity, image_id=image_id, context=context, profile=profile_name,
                              status=result['status'], evidence_sha256=evidence, report_sha256=hashlib.sha256(raw).hexdigest()))
        body, headers = multipart(dict(submission_id=str(uuid4()), profile='Demo White Background'), b'not an image')
        assert request(port, 'POST', '/inspect', body, headers)[0] == 400
    with server(database, ROOT / 'reports/phase6-http.log') as (port, pid):
        pids.append(pid)
        code, _, history = request(port, 'GET', '/history')
        assert code == 200
        for item in saved:
            assert item['id'].encode() in history
            code, _, raw = request(port, 'GET', '/report/' + item['id'] + '.json')
            assert code == 200 and hashlib.sha256(raw).hexdigest() == item['report_sha256']
            for kind, digest in item['evidence_sha256'].items():
                code, _, blob = request(port, 'GET', f"/evidence/{item['id']}/{kind}.png")
                assert code == 200 and hashlib.sha256(blob).hexdigest() == digest
    with sqlite3.connect(database) as db:
        assert db.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
        assert db.execute('SELECT COUNT(*) FROM inspections').fetchone()[0] == 6
        assert db.execute('SELECT COUNT(*) FROM check_results').fetchone()[0] == 72
    assert pids[0] != pids[1]
    assert all(hashlib.sha256(Path(path).read_bytes()).hexdigest() == digest for path, digest in hashes.items())
    report = dict(status='passed', inspections=6, check_records=72, process_ids=pids,
                  process_restart_verified=True, duplicate_submissions_deduplicated=True,
                  invalid_upload_rejected=True, sqlite_integrity='ok', saved=saved,
                  original_user_database_and_benchmark_unchanged=True, browser_rendering_checked=False)
    report_path.write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps({key: value for key, value in report.items() if key != 'saved'}, indent=2))


if __name__ == '__main__':
    main()
