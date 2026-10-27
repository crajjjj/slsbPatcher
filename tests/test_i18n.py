"""Tests for the translation layer: parsing, lookup order, fallbacks. No window needed.

Run: py -m unittest discover -s tests
"""
import io
import os
import sys
import tempfile
import unittest
from pathlib import Path

from slsbpatcher import i18n

BS = chr(92)  # one backslash: .po escapes are written with it


def po(*parts: str) -> bytes:
    """A .po file from lines, with real newlines (a quoted text cannot hold them)."""
    return "".join(part + "\n" for part in parts).encode("utf-8")


class CatalogTests(unittest.TestCase):
    """The .po parser, through the public lookups."""

    def setUp(self):
        self._saved = (dict(i18n._catalog), i18n._plural, i18n._lang)
        i18n._catalog.clear()
        i18n._plural = None
        i18n._lang = "en"

    def tearDown(self):
        i18n._catalog.clear()
        i18n._catalog.update(self._saved[0])
        i18n._plural = self._saved[1]
        i18n._lang = self._saved[2]

    def test_english_survives_when_nothing_is_loaded(self):
        self.assertEqual(i18n._("Patched 1 of 1 registry."), "Patched 1 of 1 registry.")
        self.assertEqual(i18n._n("%(n)d tag", "%(n)d tags", 1), "%(n)d tag")
        self.assertEqual(i18n._n("%(n)d tag", "%(n)d tags", 4), "%(n)d tags")

    def test_single_entry(self):
        i18n._merge(po('msgid "Hello"', 'msgstr "Bonjour"'))
        self.assertEqual(i18n._("Hello"), "Bonjour")

    def test_empty_translation_falls_back_to_english(self):
        i18n._merge(po('msgid "Hello"', 'msgstr ""'))
        self.assertEqual(i18n._("Hello"), "Hello")

    def test_fuzzy_is_skipped_and_does_not_leak_to_the_next_entry(self):
        i18n._merge(po("#, fuzzy", 'msgid "Old"', 'msgstr "stale"', "",
                       'msgid "Next"', 'msgstr "fresh"'))
        self.assertEqual(i18n._("Old"), "Old")
        self.assertEqual(i18n._("Next"), "fresh")

    def test_a_later_file_overrides_single_entries(self):
        i18n._merge(po('msgid "A"', 'msgstr "builtin-A"', "", 'msgid "B"', 'msgstr "builtin-B"'))
        i18n._merge(po('msgid "A"', 'msgstr "user-A"'))
        self.assertEqual(i18n._("A"), "user-A")
        self.assertEqual(i18n._("B"), "builtin-B")

    def test_continuation_lines_are_joined(self):
        i18n._merge(po('msgid "multi"', 'msgstr ""',
                       '"one' + BS + 'n"', '"two ' + BS + '"q' + BS + '""'))
        self.assertEqual(i18n._("multi"), 'one\ntwo "q"')

    def test_plural_forms_come_from_the_header(self):
        i18n._merge(po('msgid ""', 'msgstr ""', '"Plural-Forms: nplurals=1; plural=0;' + BS + 'n"', "",
                       'msgid "%(n)d tag"', 'msgid_plural "%(n)d tags"', 'msgstr[0] "%(n)d 个标签"'))
        self.assertEqual(i18n._n("%(n)d tag", "%(n)d tags", 1), "%(n)d 个标签")
        self.assertEqual(i18n._n("%(n)d tag", "%(n)d tags", 9), "%(n)d 个标签")

    def test_two_plural_forms(self):
        i18n._merge(po('msgid ""', 'msgstr ""', '"Plural-Forms: nplurals=2; plural=(n != 1);' + BS + 'n"', "",
                       'msgid "%(n)d tag"', 'msgid_plural "%(n)d tags"',
                       'msgstr[0] "%(n)d Tag"', 'msgstr[1] "%(n)d Tags"'))
        self.assertEqual(i18n._n("%(n)d tag", "%(n)d tags", 1), "%(n)d Tag")
        self.assertEqual(i18n._n("%(n)d tag", "%(n)d tags", 2), "%(n)d Tags")

    def test_a_broken_file_never_raises(self):
        for data in (b"", b"@@@ not a po @@@\n", bytes(range(256)), b'msgid "T"\nmsgstr "un'):
            i18n._merge(data)
        self.assertEqual(i18n._("T"), "T")

    def test_gbk_and_bom_are_accepted(self):
        i18n._merge(("msgid \"gbk\"\nmsgstr \"中文\"\n").encode("gbk"))
        self.assertEqual(i18n._("gbk"), "中文")
        i18n._catalog.clear()
        i18n._merge(("msgid \"bom\"\nmsgstr \"bom text\"\n").encode("utf-8-sig"))
        self.assertEqual(i18n._("bom"), "bom text")


class FormatTests(unittest.TestCase):
    """_fmt must lose a translation rather than crash the window."""

    def setUp(self):
        self._saved = (dict(i18n._catalog), i18n._lang)

    def tearDown(self):
        i18n._catalog.clear()
        i18n._catalog.update(self._saved[0])
        i18n._lang = self._saved[1]

    def test_translated_formatting(self):
        i18n._merge(po('msgid "%(n)d scene"', 'msgstr "%(n)d 个场景"'))
        self.assertEqual(i18n._fmt("%(n)d scene", n=3), "3 个场景")

    def test_a_wrong_placeholder_name_falls_back_to_english(self):
        i18n._merge(po('msgid "%(n)d scene"', 'msgstr "%(count)d 个场景"'))
        self.assertEqual(i18n._fmt("%(n)d scene", n=3), "3 scene")

    def test_an_english_message_with_a_bare_percent_is_returned_as_it_is(self):
        self.assertEqual(i18n._fmt("100% done"), "100% done")


class LayerTests(unittest.TestCase):
    """The three folders, and that the later ones win."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.saved = (i18n._localedirs, dict(i18n._catalog), i18n._lang)
        self.builtin = self.root / "builtin"
        self.tool = self.root / "tool"
        self.appdata = self.root / "appdata"
        i18n._localedirs = lambda: [self.builtin, self.tool, self.appdata]

    def tearDown(self):
        i18n._localedirs, catalog, lang = self.saved
        i18n._catalog.update(catalog)
        i18n._lang = lang
        self._tmp.cleanup()

    def write(self, folder: Path, lines: list[str], lang: str = "zh_CN") -> None:
        path = folder / lang / "LC_MESSAGES" / (i18n.DOMAIN + ".po")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(po(*lines))

    def test_later_folders_override_earlier_ones_per_entry(self):
        self.write(self.builtin, ['msgid "A"', 'msgstr "builtin-A"', "", 'msgid "B"', 'msgstr "builtin-B"'])
        self.write(self.tool, ['msgid "A"', 'msgstr "tool-A"'])
        self.write(self.appdata, ['msgid "C"', 'msgstr "appdata-C"'])
        self.assertEqual(i18n.install_locale("zh_CN"), "zh_CN")
        self.assertEqual(i18n._("A"), "tool-A")
        self.assertEqual(i18n._("B"), "builtin-B")
        self.assertEqual(i18n._("C"), "appdata-C")

    def test_a_language_without_files_is_english(self):
        i18n.install_locale("ja_JP")
        self.assertEqual(i18n._("Hello"), "Hello")
        self.assertEqual(i18n.current(), "ja_JP")

    def test_available_lists_the_installed_languages(self):
        self.write(self.builtin, ['msgid "A"', 'msgstr "a"'])
        self.write(self.appdata, ['msgid "A"', 'msgstr "a"'], lang="de_DE")
        self.assertEqual(i18n.available(), ["de_DE", "en", "zh_CN"])

    def test_frozen_build_reads_the_locale_beside_the_exe(self):
        """PyInstaller unpacks into sys._MEIPASS; the built-in folder must follow it."""
        saved_meipass = getattr(sys, "_MEIPASS", None)
        sys._MEIPASS = str(self.root / "meipass")
        try:
            from slsbpatcher import install

            real_localedirs = self.saved[0]  # the function before this class replaced it
            saved = (install.tool_folder, install.settings_path)
            install.tool_folder = lambda: None
            install.settings_path = lambda: self.root / "elsewhere" / "settings.json"
            try:
                folders = real_localedirs()
            finally:
                install.tool_folder, install.settings_path = saved
        finally:
            if saved_meipass is None:
                del sys._MEIPASS
            else:
                sys._MEIPASS = saved_meipass
        self.assertEqual(folders[0], self.root / "meipass" / "locale")
        self.assertIn(self.root / "elsewhere" / "locale", folders)


class LanguageTests(unittest.TestCase):
    """Which language wins: --lang, the environment, the setting, Windows, English."""

    def setUp(self):
        self.saved = (i18n.available, i18n._settings_language, i18n._system_language,
                      os.environ.get("SLSBPATCHER_LANG"))
        i18n.available = lambda: ["en", "zh_CN"]
        i18n._settings_language = lambda: ""
        i18n._system_language = lambda: ""

    def tearDown(self):
        i18n.available, i18n._settings_language, i18n._system_language, saved = self.saved
        if saved is None:
            os.environ.pop("SLSBPATCHER_LANG", None)
        else:
            os.environ["SLSBPATCHER_LANG"] = saved

    def test_explicit_wins(self):
        os.environ["SLSBPATCHER_LANG"] = "zh_CN"
        self.assertEqual(i18n.resolve_language("en"), "en")

    def test_environment_beats_the_setting(self):
        os.environ["SLSBPATCHER_LANG"] = "zh_CN"
        i18n._settings_language = lambda: "en"
        self.assertEqual(i18n.resolve_language(), "zh_CN")

    def test_the_setting_is_used_when_the_environment_is_silent(self):
        os.environ.pop("SLSBPATCHER_LANG", None)
        i18n._settings_language = lambda: "zh_CN"
        self.assertEqual(i18n.resolve_language(), "zh_CN")

    def test_windows_uses_a_hyphen_and_still_matches(self):
        os.environ.pop("SLSBPATCHER_LANG", None)
        i18n._system_language = lambda: "zh-CN"
        self.assertEqual(i18n.resolve_language(), "zh_CN")

    def test_an_uninstalled_language_falls_back_to_english(self):
        os.environ.pop("SLSBPATCHER_LANG", None)
        i18n._system_language = lambda: "fr-FR"
        self.assertEqual(i18n.resolve_language(), "en")


class ExtractTests(unittest.TestCase):
    """The extractor only sees what is wrapped, which is what keeps data out of the .po."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "_i18n_script", Path(__file__).resolve().parent.parent / "scripts" / "i18n.py")
        self.script = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.script)

    def tearDown(self):
        import shutil

        shutil.rmtree(self.root, ignore_errors=True)

    def entries_of(self, source: str) -> list:
        path = self.root / "sample.py"
        path.write_text(source, encoding="utf-8")
        problems = []
        found = self.script.collect(path, problems)
        self.assertEqual(problems, [])
        return found

    def test_both_shapes_are_collected(self):
        found = self.entries_of('_("one")\n_n("%(n)d tag", "%(n)d tags", n)\n')
        self.assertEqual([(e.msgid, e.msgid_plural) for e in found],
                         [("one", None), ("%(n)d tag", "%(n)d tags")])

    def test_unwrapped_strings_are_invisible(self):
        """Pack names, action-log verbs and tags are plain strings, so they never appear."""
        found = self.entries_of('OFFICIAL_TAGS = {"anal", "leadin"}\nOUTPUT_MOD = "slsbPatcher Output"\n'
                                'log.info("converting %s: %s", a, b)\n')
        self.assertEqual(found, [])

    def test_a_variable_argument_is_reported_not_guessed(self):
        path = self.root / "sample.py"
        path.write_text('_(some_variable)\n', encoding="utf-8")
        problems = []
        self.script.collect(path, problems)
        self.assertEqual(len(problems), 1)
        self.assertIn("string literal", problems[0])

    def test_the_wrong_number_of_arguments_is_reported(self):
        path = self.root / "sample.py"
        path.write_text('_n("one", n)\n', encoding="utf-8")
        problems = []
        self.script.collect(path, problems)
        self.assertEqual(len(problems), 1)

    def test_fmt_is_collected_and_its_plural_argument_is_left_to_n(self):
        """_fmt() would be invisible without its own rule: its message is still a literal."""
        found = self.entries_of('_fmt("%(n)d pack", n=2)\n_fmt(_n("%(n)d tag", "%(n)d tags", n), n=n)\n')
        self.assertEqual([(e.msgid, e.msgid_plural) for e in found],
                         [("%(n)d pack", None), ("%(n)d tag", "%(n)d tags")])

    def test_framework_msgids_are_in_the_template(self):
        """argparse's own wording is not in our source, so gather() has to add it by hand."""
        for text in ("usage: ", "options", "positional arguments", "show this help message and exit"):
            self.assertIn(text, self.script.FRAMEWORK_MSGIDS)


class SourceIsCleanTests(unittest.TestCase):
    """Phase 0 gate: on untouched code nothing is extracted, so nothing can be mistranslated."""

    def test_extract_over_the_package_matches_the_checked_in_template(self):
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "_i18n_script", Path(__file__).resolve().parent.parent / "scripts" / "i18n.py")
        script = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(script)
        entries, problems = script.gather()
        self.assertEqual(problems, [])
        if not script.POT.is_file():
            self.skipTest("no template yet; run py scripts/i18n.py extract")
        on_disk = script.read_po(script.POT)
        self.assertEqual(set(on_disk), set(entries),
                         "the template is out of date; run py scripts/i18n.py extract")


if __name__ == "__main__":
    unittest.main()
