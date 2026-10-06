# /// script
# requires-python = ">=3.10"
# ///
"""The Faust manual pages for one Faust version, downloaded once, then read offline.

    uv run --script faust_docs.py [--version 2.70.3]

Prints the folder holding syntax.md (the language), midi.md (MIDI metadata) and errors.md
(compiler errors). Without --version it asks `faust --version`; pass the faust_version that
read_faust_fx reports, since that is the libfaust the plugin compiles with.

The manual (github.com/grame-cncm/faustdoc, CC0) has no versions, so the pages are taken
from its last commit before the next Faust release after that version: they describe
that version and nothing newer. The folder is ~/.autoreaper/faust-docs/<version>
(AUTOREAPER_HOME moves it); later runs only print it. Needs git and network the first time.
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

FAUST = 'https://github.com/grame-cncm/faust.git'
FAUSTDOC = 'https://github.com/grame-cncm/faustdoc.git'
PAGES = ('syntax', 'midi', 'errors')


def version_key(text):
    return tuple(int(part) for part in text.split('.'))


def installed_version():
    try:
        output = subprocess.run(['faust', '--version'], capture_output=True, text=True, timeout=30).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    match = re.search(r'(\d+\.\d+\.\d+)', output)
    return match.group(1) if match else None


def git(*args, cwd=None):
    return subprocess.run(['git', *args], cwd=cwd, capture_output=True, text=True, timeout=300, check=True).stdout


def next_release_date(version, work):
    """Commit date of the first Faust release tag after version; None if there is none."""
    tags = re.findall(r'refs/tags/(\d+\.\d+\.\d+)$', git('ls-remote', '--tags', FAUST), re.M)
    later = sorted((t for t in set(tags) if version_key(t) > version_key(version)), key=version_key)
    if not later:
        return None, None
    repository = work / 'faust'
    git('init', '-q', str(repository))
    git('fetch', '-q', '--depth', '1', '--filter=tree:0', FAUST, f'refs/tags/{later[0]}', cwd=repository)
    return later[0], git('log', '-1', '--format=%cI', 'FETCH_HEAD', cwd=repository).strip()


def download(version, destination):
    with tempfile.TemporaryDirectory() as temporary:
        work = Path(temporary)
        release, cutoff = next_release_date(version, work)
        manual = work / 'faustdoc'
        # Commits only; git fetches the few files read below on demand.
        git('clone', '-q', '--bare', '--filter=tree:0', FAUSTDOC, str(manual))
        commit = git('rev-list', '-1', *([f'--before={cutoff}'] if cutoff else []), 'HEAD', cwd=manual).strip()
        if not commit:
            raise RuntimeError(f'faustdoc has no commit before {cutoff}')
        date = git('log', '-1', '--format=%cs', commit, cwd=manual).strip()
        staging = work / 'pages'
        staging.mkdir()
        for page in PAGES:
            (staging / f'{page}.md').write_text(git('show', f'{commit}:src/manual/{page}.md', cwd=manual),
                                                encoding='utf-8')
        rule = (f'last faustdoc commit before Faust {release} ({cutoff[:10]})' if release
                else 'latest faustdoc commit (no newer Faust release)')
        (staging / 'SOURCE.md').write_text(
            f'# Faust {version} manual pages\n\n'
            f'From https://github.com/grame-cncm/faustdoc (CC0), commit {commit[:10]} of {date}: the {rule}.\n'
            'syntax.md: the language. midi.md: MIDI metadata. errors.md: compiler errors.\n'
            'The standard library documents itself in its .lib files.\n', encoding='utf-8')
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(staging), str(destination))


def main():
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('--version', help='Faust version, e.g. "2.70.3" or faust_version as read_faust_fx reports it '
                                          '(default: faust --version)')
    args = parser.parse_args()
    # faust_version reads like "2.70.3 (LLVM 17.0.6)".
    match = re.search(r'\d+\.\d+\.\d+', args.version or installed_version() or '')
    if not match:
        sys.exit('Give the Faust version with --version (read_faust_fx reports faust_version).')
    version = match.group(0)
    home = Path(os.environ.get('AUTOREAPER_HOME') or Path.home() / '.autoreaper')
    folder = home / 'faust-docs' / version
    if not all((folder / name).is_file() for name in ('SOURCE.md', *(f'{p}.md' for p in PAGES))):
        try:
            if folder.exists():
                shutil.rmtree(folder)
            download(version, folder)
        except (OSError, subprocess.SubprocessError, RuntimeError) as error:
            detail = getattr(error, 'stderr', '') or error
            sys.exit(f'Could not download the Faust {version} manual ({str(detail).strip()}). Without it, use the '
                     'standard library documentation in the .lib files.')
    print(folder)


if __name__ == '__main__':
    main()
