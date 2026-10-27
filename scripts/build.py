"""Build slsbPatcher: Scene Builder's CLI, the tests, slsbPatcher.exe (window), slsbPatcher-cli.exe and the zip.

    py scripts/build.py            everything
    py scripts/build.py --no-exe   only Scene Builder's CLI and the tests (enough for `py -m slsbpatcher`)

Needs Rust 1.95+ (rustup) for the CLI and the repo's .venv with PyInstaller for the exe:
    py -m venv .venv && .venv\\Scripts\\python -m pip install pyinstaller
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCENE_BUILDER = ROOT / "lib" / "SexLab-Scene-Builder"
EXE = "scene_builder_cli.exe" if os.name == "nt" else "scene_builder_cli"

sys.path.insert(0, str(ROOT))
from slsbpatcher import __version__  # noqa: E402


def cargo() -> str:
    found = shutil.which("cargo") or str(Path.home() / ".cargo" / "bin" / "cargo")
    if not Path(found).exists() and not shutil.which(found):
        sys.exit("cargo not found: install Rust with rustup (https://rustup.rs)")
    return found


def build_cli() -> Path:
    if not (SCENE_BUILDER / "Cargo.toml").exists():
        sys.exit("lib/SexLab-Scene-Builder is empty: run  git submodule update --init")
    subprocess.run([cargo(), "build", "--release", "-p", "scene_builder_cli"], cwd=SCENE_BUILDER, check=True)
    built = SCENE_BUILDER / "target" / "release" / EXE
    target = ROOT / "bin" / EXE
    target.parent.mkdir(exist_ok=True)
    shutil.copy2(built, target)
    return target


def check_translations() -> None:
    """Refuse to build a release whose translations do not match the source."""
    subprocess.run([sys.executable, str(ROOT / "scripts" / "i18n.py"), "check"], cwd=ROOT, check=True)


def run_tests() -> None:
    subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests"], cwd=ROOT, check=True)


def build_exes() -> list[Path]:
    """slsbPatcher.exe, the window without a console, and slsbPatcher-cli.exe, the command line."""
    python = ROOT / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if not python.exists():
        sys.exit("no .venv: py -m venv .venv && .venv\\Scripts\\python -m pip install pyinstaller")
    work = ROOT / "build"
    built = []
    for name, mode, entry, extra in (("slsbPatcher", "--windowed", "entry_gui.py", []),
                                     ("slsbPatcher-cli", "--console", "entry.py", ["--exclude-module", "tkinter"])):
        subprocess.run([str(python), "-m", "PyInstaller", "--noconfirm", "--clean", "--onefile", mode, *extra,
                        "--name", name, "--paths", str(ROOT),
                        # the translations are read at run time, so they travel inside the onefile
                        "--add-data", f"{ROOT / 'slsbpatcher' / 'locale'}{os.pathsep}slsbpatcher/locale",
                        "--distpath", str(work / "dist"), "--workpath", str(work / "work"), "--specpath", str(work),
                        str(ROOT / "scripts" / entry)], cwd=ROOT, check=True)
        built.append(work / "dist" / (f"{name}.exe" if os.name == "nt" else name))
    return built


def package(exes: list[Path], cli: Path) -> Path:
    commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=SCENE_BUILDER,
                            capture_output=True, text=True).stdout.strip()
    release = ROOT / "Release"
    release.mkdir(exist_ok=True)
    archive = release / f"slsbPatcher-{__version__}.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zf:
        for exe in exes:
            zf.write(exe, exe.name)
        zf.write(cli, cli.name)
        zf.write(ROOT / "README.md", "README.md")
        zf.write(ROOT / "LICENSE", "LICENSE.txt")
        zf.write(ROOT / "THIRD_PARTY_NOTICES.md", "THIRD_PARTY_NOTICES.md")
        zf.write(SCENE_BUILDER / "LICENSE", "licenses/SexLab-Scene-Builder-LICENSE.txt")
        zf.writestr("licenses/SexLab-Scene-Builder-COMMIT.txt",
                    f"scene_builder_cli.exe was built from https://github.com/SLP-Community/SexLab-Scene-Builder at {commit}\n")
        # A .po beside the exe overrides the built-in translation line by line (locale/ at
        # the top of the zip is read by slsbpatcher/i18n.py, after the copy inside the exe).
        for po in sorted((ROOT / "slsbpatcher" / "locale").glob("*/LC_MESSAGES/*.po")):
            zf.write(po, "locale/" + po.relative_to(ROOT / "slsbpatcher" / "locale").as_posix())
    return archive


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-exe", action="store_true", help="skip slsbPatcher.exe and the zip")
    args = parser.parse_args()
    cli = build_cli()
    check_translations()
    run_tests()
    if args.no_exe:
        print(f"built {cli}")
        return
    archive = package(build_exes(), cli)
    print(f"built {archive}")


if __name__ == "__main__":
    main()
