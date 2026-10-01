"""autoreaper [serve] | install-bridge [REAPER resource folder]"""
import sys


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    command = argv[0] if argv else 'serve'
    if command == 'serve':
        from .server import main as serve
        serve()
        return 0
    if command == 'install-bridge' and len(argv) <= 2:
        from .bridge import install_bridge_script
        try:
            path = install_bridge_script(argv[1] if len(argv) > 1 else None)
        except OSError as error:
            print(error, file=sys.stderr)
            return 1
        print(f'Installed {path}\nIn REAPER: Actions > Show action list > New action > Load ReaScript, '
              'choose it, then Run.')
        return 0
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == '__main__':
    sys.exit(main())
