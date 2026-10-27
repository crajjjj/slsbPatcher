"""Build the translation template and check translations. Run: py scripts/i18n.py <command>

  extract   scan the source for _() and _n() calls, write slsbpatcher/locale/slsbpatcher.pot
  check     compare the translations with the template; exit code 1 when something is off
  stats     print how much of each language is translated

Only _("...."), _n("....", "....", n) and _fmt("....", ...) with literal arguments are
collected. A string that is not wrapped in one of those is never extracted, which is what
keeps pack names, action-log verbs, official tags and on-disk paths out of the translators'
hands. FRAMEWORK_MSGIDS adds the wording argparse itself prints (usage:, options:, ...),
which reaches the translators through argparse._ set by slsbpatcher.i18n.
"""
from __future__ import annotations

import argparse
import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PACKAGE = ROOT / "slsbpatcher"
LOCALE = PACKAGE / "locale"
POT = LOCALE / "slsbpatcher.pot"
# Third-party code (vendored under GPL) and the test suite are not translated.
SKIP_DIRS = {"vendor", "__pycache__"}
SKIP_FILES = {"i18n.py"}

PLURAL_FORMS = {
    "en": "nplurals=2; plural=(n != 1);",
    "zh_CN": "nplurals=1; plural=0;",
}
# argparse looks its own wording up through the module-level argparse._, which
# slsbpatcher/i18n.py points at _(); these are the messages this CLI can reach (usage line,
# group headings, the two help lines, the nargs and error wording). Kept here by hand
# because they are not written in our source, so nothing else would extract them.
FRAMEWORK_MSGIDS = [
    "usage: ",
    "%(heading)s:",
    "positional arguments",
    "options",
    "show this help message and exit",
    "show program's version number and exit",
    " (default: %(default)s)",
    "expected one argument",
    "expected at most one argument",
    "expected at least one argument",
    "argument %(argument_name)s: %(message)s",
    "unrecognized arguments: %s",
    "%(prog)s: error: %(message)s\n",
]
HEADER_FIELDS = [
    ("Project-Id-Version", "slsbPatcher {version}"),
    ("MIME-Version", "1.0"),
    ("Content-Type", "text/plain; charset=UTF-8"),
    ("Content-Transfer-Encoding", "8bit"),
]


class Entry:
    __slots__ = ("msgid", "msgid_plural", "locations")

    def __init__(self, msgid: str, msgid_plural: str | None = None, location: str | None = None):
        self.msgid = msgid
        self.msgid_plural = msgid_plural
        self.locations: list[str] = [location] if location else []


def version() -> str:
    for line in (PACKAGE / "__init__.py").read_text(encoding="utf-8").splitlines():
        if line.startswith("__version__"):
            return line.split("=", 1)[1].strip().strip("\"'")
    return "0"


def sources() -> list[Path]:
    files = []
    for path in sorted(PACKAGE.rglob("*.py")):
        if path.name in SKIP_FILES or any(part in SKIP_DIRS for part in path.relative_to(PACKAGE).parts):
            continue
        files.append(path)
    return files


def _literal(node: ast.AST, where: str, problems: list[str]) -> str | None:
    """A str constant, or None after recording why the call cannot be extracted."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    problems.append("%s: the argument of _() / _n() must be a string literal" % where)
    return None


def collect(path: Path, problems: list[str]) -> list[Entry]:
    """Every _() / _n() / _fmt() call in one file, in source order."""
    try:
        rel = path.relative_to(ROOT).as_posix()
    except ValueError:  # a file outside the checkout (the tests make their own)
        rel = path.name
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=rel)
    except SyntaxError as error:
        problems.append("%s: %s" % (rel, error))
        return []
    found: list[Entry] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = node.func.id if isinstance(node.func, ast.Name) else None
        where = "%s:%d" % (rel, node.lineno)
        if name == "_":
            if len(node.args) != 1 or node.keywords:
                problems.append("%s: _() takes exactly one argument" % where)
                continue
            text = _literal(node.args[0], where, problems)
            if text is not None:
                found.append(Entry(text, location=where))
        elif name == "_n":
            if len(node.args) != 3 or node.keywords:
                problems.append("%s: _n() takes three arguments: singular, plural, n" % where)
                continue
            singular = _literal(node.args[0], where, problems)
            plural = _literal(node.args[1], where, problems)
            if singular is not None and plural is not None:
                found.append(Entry(singular, plural, location=where))
        elif name == "_fmt":
            # _fmt("....", name=value) is _() plus % substitution; the first argument is the
            # msgid. _fmt(_n(one, many, n), ...) needs nothing new: ast.walk reaches the inner
            # _n() call on its own.
            if not node.args:
                problems.append("%s: _fmt() needs the message as its first argument" % where)
                continue
            first = node.args[0]
            if (isinstance(first, ast.Call) and isinstance(first.func, ast.Name)
                    and first.func.id == "_n"):
                continue
            text = _literal(first, where, problems)
            if text is not None:
                found.append(Entry(text, location=where))
    return found


def gather() -> tuple[dict[tuple[str, str | None], Entry], list[str]]:
    """All entries by (msgid, msgid_plural), and any problem that stops a clean extract."""
    entries: dict[tuple[str, str | None], Entry] = {}
    problems: list[str] = []
    # Wording argparse prints itself, reaching _() through argparse._ (see FRAMEWORK_MSGIDS).
    for text in FRAMEWORK_MSGIDS:
        entries.setdefault((text, None), Entry(text)).locations.append("argparse")
    for path in sources():
        for entry in collect(path, problems):
            key = (entry.msgid, entry.msgid_plural)
            if key in entries:
                entries[key].locations += entry.locations
            else:
                entries[key] = entry
    return entries, problems


def _quote(text: str) -> str:
    """One .po string, escaped and broken over lines the way msgfmt would."""
    escaped = text.replace("\\", "\\\\").replace('"', '\\"').replace("\t", "\\t").replace("\r", "\\r")
    lines = escaped.split("\n")
    if len(lines) == 1:
        return '"%s"' % lines[0]
    body = ['""']
    for index, line in enumerate(lines):
        tail = "\\n" if index < len(lines) - 1 else ""
        body.append('"%s%s"' % (line, tail))
    return "\n".join(body)


def header(lang: str) -> list[str]:
    fields = [(name, value.format(version=version())) for name, value in HEADER_FIELDS]
    fields.insert(1, ("Language", lang))
    body = ['msgid ""', 'msgstr ""'] + ['"%s: %s\\n"' % pair for pair in fields]
    body.append('"Plural-Forms: %s\\n"' % PLURAL_FORMS[lang])
    return body


def render(entries: dict[tuple[str, str | None], Entry], lang: str) -> str:
    lines = header(lang) + [""]
    for key in sorted(entries, key=lambda k: (k[0].lower(), k[0])):
        entry = entries[key]
        for location in dict.fromkeys(entry.locations):
            lines.append("#: %s" % location)
        lines.append("#, python-format")
        lines.append("msgid " + _quote(entry.msgid))
        if entry.msgid_plural is None:
            lines.append('msgstr ""')
        else:
            lines.append("msgid_plural " + _quote(entry.msgid_plural))
            for index in range(_nplurals(lang)):
                lines.append('msgstr[%d] ""' % index)
        lines.append("")
    return "\n".join(lines)


def _nplurals(lang: str) -> int:
    return 1 if "nplurals=1" in PLURAL_FORMS.get(lang, "") else 2


def cmd_extract(_args) -> int:
    entries, problems = gather()
    for problem in problems:
        print("  " + problem, file=sys.stderr)
    if problems:
        print("%d call(s) cannot be extracted; fix them first." % len(problems), file=sys.stderr)
        return 1
    LOCALE.mkdir(parents=True, exist_ok=True)
    POT.write_text(render(entries, "en"), encoding="utf-8")
    templates = sum(1 for entry in entries.values() if entry.msgid_plural is not None)
    print("wrote %s: %d messages (%d of them plural)" % (POT.relative_to(ROOT).as_posix(), len(entries), templates))
    return 0


def read_po(path: Path) -> dict[tuple[str, str | None], dict]:
    """The entries of a .po file, enough to check them (the program parses its own)."""
    from importlib import util

    spec = util.spec_from_file_location("_slsb_i18n", PACKAGE / "i18n.py")
    module = util.module_from_spec(spec)
    spec.loader.exec_module(module)
    found: dict[tuple[str, str | None], dict] = {}
    text = None
    for encoding in ("utf-8-sig", "gbk", "latin-1"):
        try:
            text = path.read_bytes().decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        return found
    for msgid, plural, forms, fuzzy in module._records(text):
        if not msgid:
            continue
        found[(msgid, plural)] = {"forms": forms, "fuzzy": fuzzy}
    return found


def _placeholders(text: str) -> set[str]:
    import re

    return set(re.findall(r"%\(([^)]*)\)", text)) | set(re.findall(r"%[sdifuxX]", text))


def _bare_percent(text: str) -> bool:
    """True for a msgid holding a % that is not a placeholder (100% done)."""
    import re

    # A conversion may carry flags, a width and a precision: %(seconds).1f, %-6s, %05d.
    spec = r"[-+ 0#]*\d*(?:\.\d*)?[sdifruxXeEgGc%]"
    stripped = re.sub(r"%\([^)]*\)" + spec, "", text)
    stripped = re.sub(r"%" + spec, "", stripped)
    return "%" in stripped


def cmd_check(args) -> int:
    entries, problems = gather()
    for problem in problems:
        print("  " + problem, file=sys.stderr)
    failed = list(problems)

    if not POT.is_file():
        print("%s is missing; run extract first." % POT.relative_to(ROOT), file=sys.stderr)
        return 1
    on_disk = read_po(POT)
    stale = sorted(set(on_disk) - set(entries))
    missing = sorted(set(entries) - set(on_disk))
    for key in missing:
        failed.append("not in the template: %r (run extract)" % (key[0],))
    for key in stale:
        failed.append("no longer in the source: %r (run extract)" % (key[0],))
    for key in sorted(entries):
        if _bare_percent(key[0]):
            failed.append("msgid holds a stray %% : %r" % (key[0],))
        if key[1] is not None and _placeholders(key[0]) != _placeholders(key[1]):
            failed.append("msgid and msgid_plural differ in placeholders: %r" % (key[0],))

    for po in sorted(LOCALE.glob("*/LC_MESSAGES/slsbpatcher.po")):
        lang = po.parent.parent.name
        translated = read_po(po)
        wanted = _nplurals(lang)
        for key in sorted(set(entries) - set(translated)):
            failed.append("%s: not translated: %r" % (lang, key[0]))
        for key in sorted(set(translated) - set(entries)):
            failed.append("%s: no such msgid in the source: %r" % (lang, key[0]))
        for key, entry in sorted(translated.items()):
            label = "%s: %r" % (lang, key[0])
            if entry["fuzzy"]:
                failed.append(label + " is marked fuzzy")
            forms = entry["forms"]
            if key[1] is None:
                text = forms.get(0) or ""
                if not text:
                    failed.append(label + " has no translation")
                    continue
                try:
                    text.encode("gbk")
                except UnicodeEncodeError as error:
                    failed.append(label + " cannot be shown in a console: %s" % error)
                if _placeholders(key[0]) != _placeholders(text):
                    failed.append("%s: placeholders differ: %s vs %s"
                                  % (label, sorted(_placeholders(key[0])), sorted(_placeholders(text))))
            else:
                if len(forms) != wanted:
                    failed.append("%s: %d plural form(s), this language declares %d"
                                  % (label, len(forms), wanted))
                for index, text in sorted(forms.items()):
                    if not text:
                        failed.append("%s: form %d has no translation" % (label, index))
                        continue
                    try:
                        text.encode("gbk")
                    except UnicodeEncodeError as error:
                        failed.append("%s: form %d cannot be shown in a console: %s" % (label, index, error))
                    if _placeholders(key[0]) != _placeholders(text):
                        failed.append("%s: form %d placeholders differ: %s vs %s"
                                      % (label, index, sorted(_placeholders(key[0])), sorted(_placeholders(text))))

    if failed:
        print("\n".join("  " + line for line in failed), file=sys.stderr)
        print("%d problem(s)." % len(failed), file=sys.stderr)
        return 1
    if not args.quiet:
        print("translations are consistent with the source")
    return 0


def cmd_stats(_args) -> int:
    entries, _problems = gather()
    total = len(entries)
    print("%-10s %6s %6s %s" % ("language", "done", "total", "coverage"))
    for po in sorted(LOCALE.glob("*/LC_MESSAGES/slsbpatcher.po")):
        lang = po.parent.parent.name
        translated = read_po(po)
        done = sum(1 for key, entry in translated.items()
                   if any((text or "").strip() for text in entry["forms"].values()) and not entry["fuzzy"])
        share = (100.0 * done / total) if total else 0.0
        print("%-10s %6d %6d %8.1f%%" % (lang, min(done, total), total, share))
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="i18n", description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("extract", help="write slsbpatcher/locale/slsbpatcher.pot from the source").set_defaults(func=cmd_extract)
    check = sub.add_parser("check", help="fail when the translations do not match the source")
    check.add_argument("-q", "--quiet", action="store_true", help="say nothing when all is well")
    check.set_defaults(func=cmd_check)
    sub.add_parser("stats", help="how much of each language is translated").set_defaults(func=cmd_stats)
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
