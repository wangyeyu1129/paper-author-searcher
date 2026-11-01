"""Do more workers send more traffic to a source? Runs the real image against fake sources and measures.

    python scripts/loadtest/run.py --workers 1 4 --searches 16

For each worker count it starts the stack, queues the searches, waits, and reports per source the requests, the
smallest gap between two requests against the configured interval, and the requests per second. Exit status 1 when a
gap breaks its interval by more than the tolerance, or when workers kept calling a source that answered 429.
"""

import argparse
import itertools
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
COMPOSE = ['docker', 'compose', '-p', 'paper-search-loadtest', '-f', str(ROOT / 'deploy/compose.yaml')]
COMPOSE += ['-f', str(ROOT / 'scripts/loadtest/compose.yaml')]
# The intervals of service.shared_host_intervals this test watches.
INTERVALS = {'api.openalex.org': 0.1, 'api.semanticscholar.org': 1.0, 'export.arxiv.org': 3.0, 'api.crossref.org': 0.1}
# The clock is read before the slot's sleep ends and the request crosses a network; a slot can look this much early.
TOLERANCE_SECONDS = 0.05
IN_FLIGHT_SECONDS = 0.2
PORT = '18080'


def compose(arguments: list[str], environment: dict[str, str], capture: bool = False) -> str:
    done = subprocess.run(
        [*COMPOSE, *arguments], env=environment, check=True, text=True, capture_output=capture, cwd=ROOT
    )
    return done.stdout if capture else ''


def call(path: str, key: str, body: dict | None = None) -> dict:
    request = urllib.request.Request(
        f'http://127.0.0.1:{PORT}{path}',
        data=json.dumps(body).encode() if body is not None else None,
        headers={'Authorization': f'Bearer {key}', 'Content-Type': 'application/json'},
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        return json.load(response)


def wait_ready() -> None:
    for _ in range(90):
        try:
            with urllib.request.urlopen(f'http://127.0.0.1:{PORT}/v1/health', timeout=2):
                return
        except OSError:
            time.sleep(1)
    raise SystemExit('The API did not come up')


def run(workers: int, searches: int, timeout: float, environment: dict[str, str], output: Path) -> list[dict]:
    log = output / 'requests.jsonl'
    log.unlink(missing_ok=True)
    compose(['down', '-v', '--remove-orphans'], environment)
    key = compose(['run', '--rm', '--no-deps', 'api', 'create-key', '--name', 'loadtest'], environment, True)
    secret = key.strip().splitlines()[-1]
    compose(['up', '-d', '--scale', f'worker={workers}', 'api', 'worker', 'sources'], environment)
    try:
        wait_ready()
        ids = [
            call('/v1/searches', secret, {'name': f'Load Tester{i}', 'university': 'Example University'})['id']
            for i in range(searches)
        ]
        deadline = time.time() + timeout
        statuses: dict[str, str] = {}
        while time.time() < deadline:
            statuses = {job: call(f'/v1/searches/{job}', secret)['status'] for job in ids}
            if not {'queued', 'running'} & set(statuses.values()):
                break
            time.sleep(3)
        print(f'workers={workers}: {sorted(statuses.values())}')
    finally:
        compose(['down', '-v', '--remove-orphans'], environment)
    return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []


def report(workers: int, requests: list[dict], throttle_host: str, throttle_seconds: float) -> list[str]:
    problems = []
    for host, interval in INTERVALS.items():
        times = sorted(item['t'] for item in requests if item['host'] == host)
        if len(times) < 2:
            print(f'  {host}: {len(times)} requests')
            continue
        gaps = [after - before for before, after in itertools.pairwise(times)]
        span = times[-1] - times[0]
        print(
            f'  {host}: {len(times)} requests in {span:.1f}s = {len(gaps) / span:.2f}/s, smallest gap {min(gaps):.3f}s '
            f'(interval {interval}s)'
        )
        early = [gap for gap in gaps if gap < interval - TOLERANCE_SECONDS]
        if early:
            problems.append(f'workers={workers} {host}: {len(early)} of {len(gaps)} gaps under {interval}s')
    limited = [item['t'] for item in requests if item['host'] == throttle_host]
    moment = next((item['t'] for item in requests if item.get('throttled')), None)
    if moment is not None:
        # A request sent before the 429 reached its worker cannot know about it; after this it can.
        early = [t for t in limited if moment + IN_FLIGHT_SECONDS < t < moment + throttle_seconds - TOLERANCE_SECONDS]
        after = [t for t in limited if t >= moment + throttle_seconds - TOLERANCE_SECONDS]
        wait = round(min(after) - moment, 1) if after else None
        print(
            f'  429 from {throttle_host}: {len(early)} requests inside the {throttle_seconds}s hold, next at +{wait}s'
        )
        if early:
            problems.append(f'workers={workers}: {len(early)} calls to {throttle_host} inside the hold after its 429')
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workers', type=int, nargs='+', default=[1, 4])
    parser.add_argument('--searches', type=int, default=16)
    parser.add_argument('--timeout', type=float, default=1200)
    parser.add_argument('--throttle-host', default='api.openalex.org')
    parser.add_argument('--throttle-after', type=int, default=20)
    parser.add_argument('--throttle-seconds', type=float, default=20)
    parser.add_argument(
        '--daily-cost-usd', type=float, help='service.maximum_daily_cost_usd; reports what it let through'
    )
    args = parser.parse_args()
    output = Path(tempfile.mkdtemp(prefix='loadtest-', dir=os.environ.get('TMPDIR')))
    output.chmod(0o777)
    environment = {
        **os.environ,
        'API_LOCAL_PORT': PORT,
        'LOADTEST_OUTPUT': str(output),
        'LOADTEST_THROTTLE_HOST': args.throttle_host,
        'LOADTEST_THROTTLE_AFTER': str(args.throttle_after),
        'LOADTEST_THROTTLE_SECONDS': str(int(args.throttle_seconds)),
        'SECRETS_DIRECTORY': str(output),
        **({'LOADTEST_DAILY_COST_USD': str(args.daily_cost_usd)} if args.daily_cost_usd else {}),
    }
    problems = []
    try:
        for workers in args.workers:
            requests = run(workers, args.searches, args.timeout, environment, output)
            problems += report(workers, requests, args.throttle_host, args.throttle_seconds)
            if args.daily_cost_usd:
                searches = [r for r in requests if r['host'] == 'api.openalex.org' and 'search=' in r['path']]
                print(f'  daily cap ${args.daily_cost_usd}: {len(searches)} OpenAlex search requests got through')
    finally:
        shutil.rmtree(output, ignore_errors=True)
    for problem in problems:
        print('PROBLEM', problem)
    return 1 if problems else 0


if __name__ == '__main__':
    sys.exit(main())
