import argparse
import json
import math
import re
import sys
from datetime import date
from pathlib import Path

from .runner import anonymize_request, compare_reports, evaluate, load_cases, markdown, run_suite, SOURCE_KINDS


def positive(value):
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise argparse.ArgumentTypeError('must be finite and positive')
    return number


def main():
    if sys.argv[1:] == ['_worker']:
        payload = json.load(sys.stdin)
        try:
            result = evaluate(payload['request'], payload['budget_s'])
        except Exception as exc:
            # Do not leak names, keys, or raw model validation input through error text.
            result = {'status':'error', 'error':type(exc).__name__, 'profiles':{}}
        print(json.dumps(result))
        return 0
    parser = argparse.ArgumentParser(description='Offline packing evaluation (no production writes or AI calls)')
    sub = parser.add_subparsers(dest='command', required=True)
    run = sub.add_parser('run')
    run.add_argument('--cases', type=Path, default=Path(__file__).parent / 'cases')
    run.add_argument('--output', type=Path, required=True)
    run.add_argument('--baseline', type=Path)
    run.add_argument('--budget', type=positive, default=15)
    run.add_argument('--timeout', type=positive, default=45)
    run.add_argument('--repeats', type=int, choices=range(2, 6), default=2)
    imp = sub.add_parser('import-request')
    imp.add_argument('input', type=Path)
    imp.add_argument('--output', type=Path, required=True)
    imp.add_argument('--id', required=True)
    imp.add_argument('--source-kind', choices=sorted(SOURCE_KINDS), required=True)
    imp.add_argument('--evidence', required=True, help='Non-sensitive provenance description, never names or keys')
    imp.add_argument('--limitation', action='append', default=[])
    imp.add_argument('--verified-on', help='Required for real_order; date actual physical inputs were verified')
    args = parser.parse_args()
    if args.output.suffix != '.json':
        parser.error('--output must end with .json (a separate Markdown report is generated)')
    if args.command == 'import-request':
        if not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,79}', args.id):
            parser.error('id must be an anonymous lowercase slug')
        if args.source_kind == 'real_order' and not args.verified_on:
            parser.error('real_order requires --verified-on and verified physical inputs')
        if args.verified_on:
            try:
                date.fromisoformat(args.verified_on)
            except ValueError:
                parser.error('--verified-on must be an ISO date')
        raw = json.loads(args.input.read_text(encoding='utf-8-sig'))
        source = {'kind':args.source_kind, 'evidence':args.evidence, 'limitations':args.limitation}
        if args.verified_on:
            source['verified_on'] = args.verified_on
        case = {'id':args.id, 'source':source, 'acceptance':'unreviewed', 'request':anonymize_request(raw)}
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open('x', encoding='utf-8') as file:
            json.dump(case, file, indent=2, ensure_ascii=False)
            file.write('\n')
        return 0
    if args.baseline and args.output.resolve() == args.baseline.resolve():
        parser.error('output must not overwrite the baseline')
    baseline = json.loads(args.baseline.read_text(encoding='utf-8-sig')) if args.baseline else None
    report = run_suite(load_cases(args.cases), args.budget, args.timeout, args.repeats)
    if args.baseline:
        report['comparison'] = compare_reports(report, baseline)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    args.output.with_suffix('.md').write_text(markdown(report), encoding='utf-8')
    return 0 if all(c['status'] == 'ok' and c['layout_repeatable'] for c in report['cases']) and report.get('comparison', {}).get('complete', True) else 1


if __name__ == '__main__':
    raise SystemExit(main())
