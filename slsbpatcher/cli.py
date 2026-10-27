"""Command line for slsbPatcher; the work itself is in engine.py."""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from . import __version__, engine, install, runlog, scenebuilder
from . import i18n

_ = i18n._


def _parse_args(argv):
    # --lang decides the language, and argparse freezes every description= and help= when
    # the parser is built: read --lang once with a throwaway parser, install the catalog,
    # and only then build the real one.
    pre = argparse.ArgumentParser(add_help=False)
    pre.add_argument("--lang")
    known, _rest = pre.parse_known_args(argv)
    i18n.install_locale(i18n.resolve_language(known.lang))
    parser = argparse.ArgumentParser(
        prog="slsbpatcher",
        description=_("Patch existing SexLab P+ scene registries with SLATE action logs, and convert SLAL packs "
                      "that have no SLSB conversion yet. Without arguments it opens its window."))
    parser.add_argument("inputs", nargs="*", type=Path, metavar="INPUT",
                        help=_("an MO2 profile or instance folder, a Data folder, or a folder of extracted packs. "
                               "Several are layered, the last one winning. Installed as a mod in MO2, the default "
                               "is that MO2 with its selected profile."))
    parser.add_argument("-o", "--out", type=Path,
                        help=_("output folder, installed as a mod (default: the mod \"slsbPatcher Output\" when installed "
                               "in MO2, else \"slsbPatcher Output\" in the current folder)"))
    parser.add_argument("-l", "--logs", type=Path, action="append", default=[], metavar="PATH",
                        help=_("extra SLATE action log file or folder (e.g. Hentairim tag logs); repeatable. "
                               "Logs installed under SKSE/Plugins/SLATE are always read."))
    parser.add_argument("--no-installed-logs", action="store_true", help=_("ignore action logs found in the inputs"))
    parser.add_argument("--skip-log", action="append", default=[], metavar="NAME",
                        help=_("ignore the action log with this file name (e.g. \"SLATE_ActionLog - OsmelMC.json\"); "
                               "repeatable"))
    parser.add_argument("--official-tags", action="store_true",
                        help=_("when patching, also apply lines naming one of P+'s 55 official tags (skipped by "
                               "default, as SLATE NG does: P+ reads those per stage)"))
    parser.add_argument("--no-convert", action="store_true", help=_("only patch existing registries"))
    parser.add_argument("--no-patch", action="store_true", help=_("only convert SLAL packs that have no registry"))
    parser.add_argument("--skip-pack", action="append", default=[], metavar="NAME",
                        help=_("leave this SLAL json (file name or stem) alone: not converted, and the registry made "
                               "from it not patched; repeatable"))
    parser.add_argument("--skip-registry", action="append", default=[], metavar="NAME",
                        help=_("leave this registry (.slr file name or path) alone; repeatable"))
    parser.add_argument("--reconvert", action="append", default=[], metavar="NAME",
                        help=_("convert this SLAL json (file name or stem), pack or mod name even though a registry "
                               "covers it, reusing that registry's hash and ids; the new registry replaces it. "
                               "Repeatable."))
    parser.add_argument("-u", "--update", type=Path, action="append", default=[], metavar="DIR",
                        help=_("folder of *.slsb.json from an earlier conversion, to keep its pack hash and scene ids"))
    parser.add_argument("--author", help=_("pack author written into new conversions (slsb-convert.py -a)"))
    parser.add_argument("--stricter-futa", action="store_true", help=_("slsb-convert.py -sf"))
    parser.add_argument("--scene-builder", metavar="EXE", help=_("path to scene_builder_cli.exe"))
    parser.add_argument("--zip", action="store_true", help=_("also pack the output folder into a .zip beside it"))
    parser.add_argument("-n", "--dry-run", action="store_true", help=_("report what would change, write nothing"))
    parser.add_argument("-s", "--summary", action="store_true", help=_("print only the summary, not the details"))
    parser.add_argument("--lang", metavar="CODE",
                        help=_("language of this run, e.g. en or zh_CN (default: the setting, then Windows)"))
    parser.add_argument("-v", "--verbose", action="store_true")
    parser.add_argument("--version", action="version", version=f"slsbPatcher {__version__}")
    return parser.parse_args(argv)


def main(argv=None, explorer: bool = False) -> int:
    i18n.harden_console()
    args = _parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, format="%(message)s")
    runlog.start("command line")
    args.inputs = args.inputs or install.default_inputs()
    if not args.inputs:
        print(_("Name your load order: an MO2 profile folder, a Data folder or a folder of SLAL packs (see --help)."))
        return 2
    # Started from Explorer, the working folder is wherever Windows put it.
    if args.out:
        out = args.out
    elif install.mo2() or explorer:
        out = install.default_out()
    else:
        out = Path.cwd() / install.OUTPUT_MOD
    if not args.dry_run and not engine.out_is_usable(out):
        print(_("%(out)s is not empty and is not an slsbPatcher output; pick another --out") % {"out": out})
        return 2
    options = engine.Options(
        inputs=args.inputs, out=out, logs=args.logs, installed_logs=not args.no_installed_logs,
        skip_logs=args.skip_log, official_tags=args.official_tags, patch=not args.no_patch,
        convert=not args.no_convert, reconvert=args.reconvert, update=args.update, author=args.author,
        stricter_futa=args.stricter_futa, scene_builder=args.scene_builder, zip=args.zip, dry_run=args.dry_run,
        skip_packs=args.skip_pack, skip_registries=args.skip_registry)
    try:
        outcome = engine.execute(options)
    except scenebuilder.SceneBuilderError as error:
        print(error)
        return 2
    runlog.write(outcome.report)
    print("\n".join(outcome.summary if args.summary else outcome.report))
    return outcome.code


if __name__ == "__main__":
    sys.exit(main())
