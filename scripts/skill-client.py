"""Standard-library client for skills running outside the platform process."""
import argparse
import json
import sys
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url', default='http://127.0.0.1:8000')
    parser.add_argument('--machine-id', default='local')
    parser.add_argument('--timeout', type=float, default=120)
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('list')
    sub.add_parser('tools')
    read = sub.add_parser('read')
    read.add_argument('skill')
    read.add_argument('--resource', default='SKILL.md')
    call = sub.add_parser('call')
    call.add_argument('skill')
    call.add_argument('tool')
    call.add_argument('--arguments', default='{}', help='JSON object; use - to read stdin')
    args = parser.parse_args()
    data = None
    if args.command == 'list':
        path = '/api/skills'
    elif args.command == 'tools':
        path = '/api/tools'
    elif args.command == 'read':
        path = '/api/skills/' + quote(args.skill, safe='') + '?resource=' + quote(args.resource, safe='')
    else:
        try:
            payload = json.loads(sys.stdin.read() if args.arguments == '-' else args.arguments)
        except ValueError:
            parser.error('--arguments must be valid JSON')
        if not isinstance(payload, dict):
            parser.error('--arguments must be a JSON object')
        data = json.dumps(payload).encode('utf-8')
        path = '/api/skills/' + quote(args.skill, safe='') + '/tools/' + quote(args.tool, safe='')
    request = Request(args.base_url.rstrip('/') + path, data=data,
                      headers={'Content-Type': 'application/json', 'X-Machine-ID': args.machine_id})
    try:
        with urlopen(request, timeout=args.timeout) as response:
            result = json.load(response)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 1 if isinstance(result, dict) and result.get('ok') is False else 0
    except HTTPError as exc:
        print(exc.read().decode('utf-8', errors='replace'), file=sys.stderr)
        return 1
    except URLError as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
