import sys
from pathlib import Path

from . import i18n, install
from .cli import main
from .console import pause, started_from_explorer

_ = i18n._


def cli_hint() -> str:
    """Kept a function: a module-level `_()` would be evaluated before install_locale()."""
    return _("""slsbPatcher-cli.exe is slsbPatcher's command line. For the window, start slsbPatcher.exe.

    slsbPatcher-cli "C:\\Modding\\MO2\\profiles\\Default"
    slsbPatcher-cli --help
""")


def run(window_without_args: bool = True) -> int:
    """`py -m slsbpatcher` opens the window without arguments; slsbPatcher-cli.exe never does."""
    args = sys.argv[1:]
    # Before anything is printed and before cli's parser is built; cli.py installs again
    # when a --lang is on the command line, which then wins.
    i18n.install_locale(i18n.resolve_language(None))
    if "--gui" in args or (not args and window_without_args):
        try:
            from .gui import launch
        except ImportError:  # slsbPatcher-cli.exe is built without Tk
            print(_("The window is slsbPatcher.exe."))
            return 2
        return launch([Path(arg) for arg in args if arg != "--gui"])
    explorer = started_from_explorer()
    if not args and not install.mo2():  # installed in MO2, no arguments means "that MO2"
        print(cli_hint())
        if explorer:
            pause()
        return 0
    try:
        return main(explorer=explorer)
    finally:
        if explorer:
            pause()


if __name__ == "__main__":
    sys.exit(run())
