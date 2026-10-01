"""Audit staged blobs before publishing; never print matching secret values."""
from pathlib import Path, PurePosixPath
import re
import subprocess

ROOT = Path(__file__).resolve().parents[1]
BLOCKED = {'headlessmc', '.runtime', '.gradle', '.gradle-user-home', 'logs', 'run', 'data', '__pycache__', '.venv'}
PATTERNS = [re.compile(rb'sk-[A-Za-z0-9_-]{20,}'),
            re.compile(rb'gh[pousr]_[A-Za-z0-9]{30,}'),
            re.compile(rb'github_pat_[A-Za-z0-9_]{30,}'),
            re.compile(rb'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----')]

def main():
    paths = subprocess.check_output(['git', 'diff', '--cached', '--name-only', '--diff-filter=ACM', '-z'], cwd=ROOT).decode().split('\0')
    failures = []
    count = 0
    for name in filter(None, paths):
        path = PurePosixPath(name)
        count += 1
        if BLOCKED.intersection(path.parts) or path.name == '.env' or 'API_KEY' in path.name.upper() or path.suffix == '.log':
            failures.append((name, 'private/runtime path'))
            continue
        content = subprocess.check_output(['git', 'show', ':' + name], cwd=ROOT)
        if len(content) > 10 * 1024 * 1024:
            failures.append((name, 'unexpected large file'))
        if path.suffix != '.jar' and any(pattern.search(content) for pattern in PATTERNS):
            failures.append((name, 'possible credential'))
        if re.search(rb'[A-Za-z]:[/\\]Users[/\\][^/\\\s]+', content):
            failures.append((name, 'developer-specific absolute path'))
    for name, reason in failures:
        print(reason + ': ' + name)
    if failures:
        raise SystemExit(1)
    print(f'Public staged audit passed: {count} files; no credential or runtime-path matches')

if __name__ == '__main__':
    main()
