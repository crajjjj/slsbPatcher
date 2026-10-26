"""Build slsbPatcher: Scene Builder's CLI, the tests, slsbPatcher.exe and Release/slsbPatcher-<version>.zip.

    py scripts/build.py            everything
    py scripts/build.py --no-exe   only the CLI and the tests (enough for `py -m slsbpatcher`)

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


def run_tests() -> None:
    subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests"], cwd=ROOT, check=True)


def build_exe() -> Path:
    python = ROOT / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if not python.exists():
        sys.exit("no .venv: py -m venv .venv && .venv\\Scripts\\python -m pip install pyinstaller")
    work = ROOT / "build"
    subprocess.run([str(python), "-m", "PyInstaller", "--noconfirm", "--clean", "--onefile", "--console",
                    "--name", "slsbPatcher", "--paths", str(ROOT),
                    "--distpath", str(work / "dist"), "--workpath", str(work / "work"), "--specpath", str(work),
                    str(ROOT / "scripts" / "entry.py")], cwd=ROOT, check=True)
    return work / "dist" / ("slsbPatcher.exe" if os.name == "nt" else "slsbPatcher")


def package(exe: Path, cli: Path) -> Path:
    commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=SCENE_BUILDER,
                            capture_output=True, text=True).stdout.strip()
    release = ROOT / "Release"
    release.mkdir(exist_ok=True)
    archive = release / f"slsbPatcher-{__version__}.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.write(exe, exe.name)
        zf.write(cli, cli.name)
        zf.write(ROOT / "README.md", "README.md")
        zf.write(ROOT / "LICENSE", "LICENSE.txt")
        zf.write(ROOT / "THIRD_PARTY_NOTICES.md", "THIRD_PARTY_NOTICES.md")
        zf.write(SCENE_BUILDER / "LICENSE", "licenses/SexLab-Scene-Builder-LICENSE.txt")
        zf.writestr("licenses/SexLab-Scene-Builder-COMMIT.txt",
                    f"scene_builder_cli.exe was built from https://github.com/SLP-Community/SexLab-Scene-Builder at {commit}\n")
    return archive


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-exe", action="store_true", help="skip slsbPatcher.exe and the zip")
    args = parser.parse_args()
    cli = build_cli()
    run_tests()
    if args.no_exe:
        print(f"built {cli}")
        return
    archive = package(build_exe(), cli)
    print(f"built {archive}")


if __name__ == "__main__":
    main()
