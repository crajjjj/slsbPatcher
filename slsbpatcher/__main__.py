import sys
from pathlib import Path

from .cli import main
from .console import pause, started_from_explorer

CLI_HINT = """slsbPatcher-cli.exe is slsbPatcher's command line. For the window, start slsbPatcher.exe.

    slsbPatcher-cli "C:\\Modding\\MO2\\profiles\\Default"
    slsbPatcher-cli --help
"""


def run(window_without_args: bool = True) -> int:
    """`py -m slsbpatcher` opens the window without arguments; slsbPatcher-cli.exe never does."""
    args = sys.argv[1:]
    if "--gui" in args or (not args and window_without_args):
        try:
            from .gui import launch
        except ImportError:  # slsbPatcher-cli.exe is built without Tk
            print("The window is slsbPatcher.exe.")
            return 2
        return launch([Path(arg) for arg in args if arg != "--gui"])
    explorer = started_from_explorer()
    if not args:
        print(CLI_HINT)
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
