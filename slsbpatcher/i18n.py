"""Translations: one .po per language, read at run time, with no build step.

A translation lives in locale/<lang>/LC_MESSAGES/slsbpatcher.po and is parsed by this
module; there is no .mo, so the file a translator edits is the file the program reads.

Three places are searched, later ones overriding single entries of earlier ones (so a user
can correct one line without restating the rest):

1. locale/ inside the package (or in PyInstaller's _MEIPASS): the shipped text;
2. <folder of the exe>/locale/: for a user who wants to change a few lines;
3. %APPDATA%/slsbPatcher/locale/: the same, when the install folder is read-only.

With no file for the chosen language every call returns its English argument unchanged, so
the program behaves exactly as it did before translations existed.
"""
from __future__ import annotations

import gettext
import logging
import os
import re
import sys
from pathlib import Path

DOMAIN = "slsbpatcher"
DEFAULT = "en"

_catalog: dict[str, object] = {}
_plural = None  # compiled from the header's Plural-Forms, or None
_lang = DEFAULT

_ESCAPES = {"n": "\n", "t": "\t", "r": "\r", '"': '"', "\\": "\\"}
_FIELD = re.compile(r'(msgid_plural|msgid|msgstr(?:\[(\d+)\])?)\s+"(.*)"$')
_PART = re.compile(r'"(.*)"$')


def _unescape(text: str) -> str:
    """Undo the four escapes a .po string uses; anything else is kept as written."""
    out, index = [], 0
    while len(text) > index:
        char = text[index]
        if char == "\\" and len(text) > index + 1:
            out.append(_ESCAPES.get(text[index + 1], text[index + 1]))
            index += 2
        else:
            out.append(char)
            index += 1
    return "".join(out)


def _records(text: str):
    """Yield (msgid, msgid_plural, {form: text}, fuzzy) for each entry in a .po file.

    A field may continue over several lines: the field line is followed by any number of
    quoted lines whose contents are joined without a separator. Comments belong to the entry
    that follows them, which is why they are only consumed once an entry exists.
    """
    entry, fuzzy, target = None, False, None

    def take():
        nonlocal entry, fuzzy, target
        if entry is None:
            return None
        done = (entry["msgid"], entry["plural"], entry["forms"], fuzzy)
        entry, fuzzy, target = None, False, None
        return done

    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("#"):
            done = take()
            if done:
                yield done
            fuzzy = "fuzzy" in line
            continue
        if not line:
            done = take()
            if done:
                yield done
            continue
        part = _PART.match(line)
        if part is not None and target is not None:
            value = _unescape(part.group(1))
            kind, slot = target
            if kind == "msgid":
                entry["msgid"] += value
            elif kind == "plural":
                entry["plural"] += value
            else:
                entry["forms"][slot] = entry["forms"].get(slot, "") + value
            continue
        found = _FIELD.match(line)
        if not found:
            continue  # a line this parser does not know is skipped, never fatal
        field, index, value = found.group(1), found.group(2), _unescape(found.group(3))
        if field == "msgid":
            done = take()
            if done:
                yield done
            entry = {"msgid": value, "plural": None, "forms": {}}
            target = ("msgid", None)
        elif entry is None:
            continue  # a msgstr before its msgid: an entry cannot be built from it
        elif field == "msgid_plural":
            entry["plural"] = value
            target = ("plural", None)
        else:
            slot = int(index) if index else 0
            entry["forms"][slot] = value
            target = ("msgstr", slot)
    done = take()
    if done:
        yield done


def _merge(data: bytes) -> None:
    """Add one .po file to the catalog, an entry at a time (a later file wins per entry)."""
    global _plural
    text = None
    for encoding in ("utf-8-sig", "gbk", "latin-1"):  # utf-8-sig also eats a BOM
        try:
            text = data.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        return
    for msgid, plural_msgid, forms, fuzzy in _records(text):
        if fuzzy:
            continue  # marked as "the source changed, this text is stale"
        if not msgid:  # the header: only Plural-Forms is of use here
            found = re.search(r"plural\s*=\s*([^;]+)", forms.get(0) or "")
            if found:
                try:
                    _plural = gettext.c2py(found.group(1).strip())
                except (ValueError, SyntaxError):
                    _plural = None
            continue
        if plural_msgid is None:
            _catalog[msgid] = forms.get(0) or ""
        else:
            _catalog[msgid] = (tuple(forms[i] for i in sorted(forms)),)


def _localedirs() -> list[Path]:
    """The three places a .po can live, the ones that override last (see the module doc)."""
    from . import install

    meipass = getattr(sys, "_MEIPASS", None)
    builtin = (Path(meipass) if meipass else Path(__file__).resolve().parent) / "locale"
    here = install.tool_folder()
    candidates = [builtin, (here / "locale") if here else None,
                  Path(install.settings_path()).parent / "locale"]
    seen, kept = set(), []
    for path in candidates:
        if path is None:
            continue
        key = str(path).lower()
        if key not in seen:
            seen.add(key)
            kept.append(path)
    return kept


def _po_path(folder: Path, lang: str) -> Path:
    return folder / lang / "LC_MESSAGES" / (DOMAIN + ".po")


def normalize(lang: str | None) -> str:
    """zh-CN, zh-cn and zh_CN all become zh_CN; an empty value stays empty."""
    return (lang or "").strip().replace("-", "_")


def _system_language() -> str:
    """The language Windows is in, e.g. zh-CN, or "" when that cannot be asked."""
    if os.name != "nt":
        return ""
    try:
        import ctypes

        size = 85  # LOCALE_NAME_MAX_LENGTH
        buffer = ctypes.create_unicode_buffer(size)
        if ctypes.windll.kernel32.GetUserDefaultLocaleName(buffer, size):
            return buffer.value
    except (AttributeError, OSError):
        pass
    return ""


def available() -> list[str]:
    """Every language a .po is installed for, plus English, sorted."""
    found = {DEFAULT}
    for folder in _localedirs():
        try:
            children = list(folder.iterdir())
        except OSError:
            continue
        for child in children:
            if _po_path(folder, child.name).is_file():
                found.add(child.name)
    return sorted(found)


def _settings_language() -> str:
    """The language chosen in the window's Options, without building the window."""
    try:
        import json

        from . import install

        data = json.loads(Path(install.settings_path()).read_text(encoding="utf-8"))
    except (OSError, ValueError, ImportError):
        return ""
    value = data.get("language") if isinstance(data, dict) else None
    return value if isinstance(value, str) else ""


def resolve_language(explicit: str | None = None) -> str:
    """Pick the language: --lang, SLSBPATCHER_LANG, the setting, Windows, then English."""
    codes = {code.lower(): code for code in available()}
    wanted = [normalize(explicit), normalize(os.environ.get("SLSBPATCHER_LANG")),
              normalize(_settings_language()), normalize(_system_language())]
    for candidate in wanted:
        if candidate and candidate.lower() in codes:  # zh_CN as installed
            return codes[candidate.lower()]
    for candidate in wanted:  # Windows says zh-CN where the folder is zh_CN
        if candidate and candidate.split("_")[0].lower() in codes:
            return codes[candidate.split("_")[0].lower()]
    return DEFAULT


def install_locale(lang: str | None = None) -> str:
    """Load the translations of one language; anything unreadable means English."""
    global _catalog, _plural, _lang
    chosen = normalize(lang) or DEFAULT
    _catalog, _plural, _lang = {}, None, chosen
    for folder in _localedirs():
        path = _po_path(folder, chosen)
        if not path.is_file():
            continue
        try:
            with path.open("rb") as handle:
                _merge(handle.read())
        except OSError as error:  # a broken file must never stop the program
            logging.getLogger(__name__).warning("unreadable translation %s: %s", path, error)
    _hook_argparse()
    return chosen


def _hook_argparse() -> None:
    """Let argparse translate its own wording (usage:, options:, show this help...)."""
    try:
        import argparse
    except ImportError:
        return
    argparse._ = _  # the documented way to localize argparse
    argparse.ngettext = _n


def current() -> str:
    return _lang


def _(message: str) -> str:
    """The translation of one whole sentence, or the sentence itself."""
    entry = _catalog.get(message)
    if entry is None:
        return message
    if isinstance(entry, str):
        return entry or message
    forms = entry[0]
    return (forms[0] or message) if forms else message


def _n(singular: str, plural: str, n: int) -> str:
    """The translation of one of two English forms, chosen by n in this language."""
    entry = _catalog.get(singular)
    if entry is None:
        return singular if n == 1 else plural
    if isinstance(entry, str):
        return entry or (singular if n == 1 else plural)
    forms = entry[0]
    index = _plural(n) if _plural else (0 if n == 1 else 1)
    if forms:
        picked = forms[index] if index < len(forms) else forms[-1]
        return picked or (singular if n == 1 else plural)
    return singular if n == 1 else plural


def _fmt(message: str, **values) -> str:
    """Translate and fill in %(name)s placeholders, falling back rather than raising."""
    try:
        return _(message) % values
    except (KeyError, ValueError, TypeError) as error:
        logging.getLogger(__name__).warning("bad translation for %r: %s", message, error)
    try:  # the English text itself may hold a stray % (100% done)
        return message % values
    except (KeyError, ValueError, TypeError):
        return message


def harden_console() -> None:
    """Never die over a character the console cannot encode: show ? instead."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(errors="replace")
            except (OSError, ValueError):
                pass
