import sys

from .cli import main
from .console import HOW_TO, pause, started_from_explorer


def run() -> int:
    explorer = started_from_explorer()
    if explorer and len(sys.argv) == 1:
        print(HOW_TO)
        pause()
        return 0
    try:
        return main(explorer=explorer)
    finally:
        if explorer:
            pause()


if __name__ == "__main__":
    sys.exit(run())
