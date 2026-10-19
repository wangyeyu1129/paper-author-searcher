"""Run the offline test suite; report totals and optionally fail when any test is skipped.

CI installs every extra the tests need, so a skipped test there means a missing dependency hiding untested
code. Usage: python scripts/ci/run_tests.py [--fail-on-skip]
"""

import argparse
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--fail-on-skip', action='store_true', help='exit non-zero when any test is skipped')
    parser.add_argument('--verbosity', type=int, default=1)
    args = parser.parse_args()
    sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'tests'), str(ROOT)]
    suite = unittest.defaultTestLoader.discover(str(ROOT / 'tests'), top_level_dir=str(ROOT / 'tests'))
    result = unittest.TextTestRunner(verbosity=args.verbosity).run(suite)
    counts = {
        'tests': result.testsRun,
        'failures': len(result.failures),
        'errors': len(result.errors),
        'skipped': len(result.skipped),
    }
    print(' '.join(f'{name}={value}' for name, value in counts.items()))
    for test, reason in result.skipped:
        print(f'SKIPPED {test.id()}: {reason}')
    if not result.wasSuccessful():
        return 1
    if args.fail_on_skip and result.skipped:
        print('Skipped tests are not allowed here: install the missing extras.', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
