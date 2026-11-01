"""Fire drill: real searches on an empty deployment, then lose everything and restore it from a backup.

    python scripts/ops/backup_drill.py --secrets-directory deploy/secrets \
        --search "Whitney Zhang|Massachusetts Institute of Technology|Economics"

Runs as its own compose project on its own volumes, so a deployment on the same machine is not touched. Reports what
each search cost and took and the most memory any container used, then checks that after the restore the API key still
works and every search's result and files are byte for byte what they were. Exit status 1 when anything differs.
"""

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
COMPOSE = ['docker', 'compose', '-p', 'paper-search-drill', '-f', str(ROOT / 'deploy/compose.yaml')]
PORT = '18081'
SERVICES = ['api', 'worker', 'backup']
UNITS = {'B': 1, 'KiB': 2**10, 'MiB': 2**20, 'GiB': 2**30}


def compose(arguments: list[str], environment: dict[str, str], capture: bool = False) -> str:
    done = subprocess.run(
        [*COMPOSE, *arguments], env=environment, check=True, text=True, capture_output=capture, cwd=ROOT
    )
    return done.stdout if capture else ''


def call(path: str, key: str, body: dict | None = None) -> tuple[int, bytes, dict[str, str]]:
    request = urllib.request.Request(
        f'http://127.0.0.1:{PORT}{path}',
        data=json.dumps(body).encode() if body is not None else None,
        headers={'Authorization': f'Bearer {key}', 'Content-Type': 'application/json'},
    )
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            return response.status, response.read(), dict(response.headers)
    except urllib.error.HTTPError as error:
        return error.code, error.read(), dict(error.headers)


def wait_ready() -> None:
    for _ in range(120):
        try:
            with urllib.request.urlopen(f'http://127.0.0.1:{PORT}/v1/health', timeout=2):
                return
        except OSError:
            time.sleep(1)
    raise SystemExit('The API did not come up')


class MemorySampler(threading.Thread):
    """The largest memory use seen per service, from docker stats."""

    def __init__(self) -> None:
        super().__init__(daemon=True)
        self.peak: dict[str, int] = {}
        self.stopping = threading.Event()

    def run(self) -> None:
        while not self.stopping.wait(5):
            done = subprocess.run(
                ['docker', 'stats', '--no-stream', '--format', '{{.Name}} {{.MemUsage}}'],
                capture_output=True,
                text=True,
                check=False,
            )
            for line in done.stdout.splitlines():
                found = re.match(r'paper-search-drill-(\w+)-\d+ ([\d.]+)(\w+) /', line)
                if found:
                    used = int(float(found[2]) * UNITS.get(found[3], 1))
                    self.peak[found[1]] = max(self.peak.get(found[1], 0), used)


def fingerprint(job: str, key: str) -> dict[str, str]:
    """Hashes of the job's result and of every file it offers, for comparing before and after."""
    status, body, _ = call(f'/v1/searches/{job}', key)
    document = json.loads(body)
    # Timestamps of the job itself may move; what it found must not.
    result = document.get('result') or {}
    prints = {'result': f'{status}:{document["status"]}:{hashlib.sha256(json.dumps(result).encode()).hexdigest()}'}
    for link in downloads(result):
        code, content, _ = call(link, key)
        prints[link] = f'{code}:{hashlib.sha256(content).hexdigest()}'
    return prints


def downloads(value: object) -> list[str]:
    """Every value under a ``download`` key of the result."""
    if isinstance(value, dict):
        found = [value['download']] if isinstance(value.get('download'), str) else []
        return found + [link for item in value.values() for link in downloads(item)]
    if isinstance(value, list):
        return [link for item in value for link in downloads(item)]
    return []


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--search', action='append', required=True, help='name|university|department')
    parser.add_argument('--mode', default='full', choices=('metadata', 'pdf-urls', 'full'))
    parser.add_argument('--secrets-directory', required=True)
    parser.add_argument('--timeout', type=float, default=3600)
    parser.add_argument('--workers', type=int, default=1)
    args = parser.parse_args()
    scratch = Path(tempfile.mkdtemp(prefix='drill-', dir=os.environ.get('TMPDIR')))
    scratch.chmod(0o777)
    environment = {
        **os.environ,
        'API_LOCAL_PORT': PORT,
        'SECRETS_DIRECTORY': str(Path(args.secrets_directory).resolve()),
        'BACKUP_DIRECTORY': str(scratch),
        # The scheduled backup would race the drill's own; this one runs once a week.
        'BACKUP_EVERY_HOURS': '168',
    }
    sampler = MemorySampler()
    problems: list[str] = []
    compose(['down', '-v', '--remove-orphans'], environment)
    try:
        secret = compose(['run', '--rm', '--no-deps', 'api', 'create-key', '--name', 'drill'], environment, True)
        key = secret.strip().splitlines()[-1]
        compose(['up', '-d', '--scale', f'worker={args.workers}', *SERVICES], environment)
        wait_ready()
        sampler.start()
        jobs = {}
        for text in args.search:
            name, university, department = [*text.split('|'), ''][:3]
            body = {'name': name, 'university': university, 'mode': args.mode}
            if department:
                body['department'] = department
            _, answer, _ = call('/v1/searches', key, body)
            jobs[json.loads(answer)['id']] = (name, time.time())
        deadline = time.time() + args.timeout
        finished: dict[str, dict] = {}
        while len(finished) < len(jobs) and time.time() < deadline:
            for job, (name, began) in jobs.items():
                if job in finished:
                    continue
                document = json.loads(call(f'/v1/searches/{job}', key)[1])
                if document['status'] not in ('queued', 'running'):
                    finished[job] = document
                    print(f'{name}: {document["status"]} after {time.time() - began:.0f}s', flush=True)
            time.sleep(5)
        missing = set(jobs) - set(finished)
        if missing:
            problems.append(f'{len(missing)} searches did not finish in time')
        sampler.stopping.set()
        metrics = call('/metrics', key)[1].decode()
        print(
            *[line for line in metrics.splitlines() if line.startswith(('paper_search_cost', 'paper_search_llm'))],
            sep='\n',
        )
        for job, (name, _) in jobs.items():
            result = json.loads(call(f'/v1/searches/{job}', key)[1]).get('result') or {}
            print(name, 'papers:', len(result.get('papers') or []), 'files:', len(downloads(result)))
        print(*re.findall(r'Search \S+ finished as .*?\)', compose(['logs', 'worker'], environment, True)), sep='\n')
        print('Peak memory:', {service: f'{used / 2**20:.0f} MiB' for service, used in sampler.peak.items()})

        before = {job: fingerprint(job, key) for job in jobs}
        files = sum(len(prints) - 1 for prints in before.values())
        print(f'Before the loss: {len(before)} results, {files} files fingerprinted')
        written = compose(['run', '--rm', '--no-deps', 'backup', 'backup', '--output', '/backups'], environment, True)
        archive = Path(written.strip().splitlines()[-1]).name
        size = (scratch / archive).stat().st_size
        print(f'Backup {archive}: {size / 2**20:.1f} MiB')

        compose(['down', '-v'], environment)
        print('All containers and volumes deleted')
        restored = compose(['run', '--rm', '--no-deps', 'backup', 'restore', f'/backups/{archive}'], environment, True)
        print(restored.strip())
        compose(['up', '-d', *SERVICES], environment)
        wait_ready()
        after = {job: fingerprint(job, key) for job in jobs}
        if before != after:
            problems.append('Results or files differ after the restore')
        ready = call('/v1/ready', key)
        print('Ready after restore:', ready[0])
        print('Same results and files after the restore:', before == after)
    finally:
        sampler.stopping.set()
        compose(['down', '-v', '--remove-orphans'], environment)
    for problem in problems:
        print('PROBLEM', problem)
    return 1 if problems else 0


if __name__ == '__main__':
    sys.exit(main())
