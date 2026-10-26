"""Offline tests: registry format, action logs, patch rules. Run: py -m unittest discover tests"""
import json
import tempfile
import unittest
from pathlib import Path

from slsbpatcher import actionlog, convert, gamedata, patcher, slr


def _str(text: str) -> bytes:
    raw = text.encode("utf-8")
    return len(raw).to_bytes(8, "big") + raw


def _strs(values) -> bytes:
    return len(values).to_bytes(8, "big") + b"".join(_str(v) for v in values)


def make_slr(scenes) -> bytes:
    """A format-4 registry. scenes: [(id, name, [(stage id, [events], [tags])])]"""
    out = bytearray([4]) + _str("Pack") + _str("Author") + b"abcd" + len(scenes).to_bytes(8, "big")
    for scene_id, name, stages in scenes:
        out += scene_id.encode() + _str(name)
        positions = len(stages[0][1])
        out += positions.to_bytes(8, "big")
        for _ in range(positions):  # race, sex, scale, flags
            out += bytes([0, 2]) + (1000).to_bytes(4, "big") + bytes([0])
        out += len(stages).to_bytes(8, "big")
        for stage_id, events, tags in stages:
            out += stage_id.encode() + len(events).to_bytes(8, "big")
            for event in events:
                out += _str(event) + bytes([0]) + bytes(16) + bytes([0x80]) + _strs([])
            out += (0).to_bytes(4, "big") + _str("") + _strs(tags)
        out += len(stages).to_bytes(8, "big")
        for i, (stage_id, _, _) in enumerate(stages):
            nxt = [stages[i + 1][0]] if i + 1 < len(stages) else []
            out += stage_id.encode() + len(nxt).to_bytes(8, "big") + b"".join(n.encode() for n in nxt)
        out += bytes(4) + bytes([0]) + bytes(16) + bytes([0])
    return bytes(out)


SAMPLE = make_slr([
    ("scene001", "Billyy Doggy", [("stage001", ["B_Doggy_A1_S1", "B_Doggy_A2_S1"], ["billyy", "rough"]),
                                  ("stage002", ["B_Doggy_A1_S2", "B_Doggy_A2_S2"], ["billyy", "rough"])]),
    ("scene002", "Other", [("stage003", ["O_A1_S1"], ["solo"])]),
])


def ops(*lines, official=False):
    log = actionlog.LogFile(Path("x.json"), "x", list(lines))
    return actionlog.build_logs([log], official).ops_by_scene()


class SlrTests(unittest.TestCase):
    def test_parse(self):
        f = slr.parse_slr(SAMPLE)
        self.assertEqual((f.version, f.name, f.author, f.prefix_hash), (4, "Pack", "Author", "abcd"))
        self.assertEqual([s.name for s in f.scenes], ["Billyy Doggy", "Other"])
        self.assertEqual(f.scenes[0].stages[1].events, ["B_Doggy_A1_S2", "B_Doggy_A2_S2"])
        self.assertIn("b_doggy_a2_s1", f.events())

    def test_unchanged_round_trip(self):
        f = slr.parse_slr(SAMPLE)
        self.assertEqual(slr.build_slr(SAMPLE, f, [[s.tags for s in sc.stages] for sc in f.scenes]), SAMPLE)

    def test_trailing_bytes_rejected(self):
        with self.assertRaises(slr.SlrError):
            slr.parse_slr(SAMPLE + b"\0")


class ActionLogTests(unittest.TestCase):
    def test_extract_with_bom_and_comments(self):
        text = '﻿{ /* c */ "stringList": { // x\n "slate.actionlog": ["AddTag,A,b", "Comment,hi"] } }'
        self.assertEqual(actionlog.extract_action_log(text), ["AddTag,A,b", "Comment,hi"])
        self.assertIsNone(actionlog.extract_action_log('{"stringList": {"other": []}}'))

    def test_semantics(self):
        log = actionlog.LogFile(Path("x"), "x", ["AddTag, Scene One ,Leg Up", "RemoveTag,Scene One,agressive",
                                                 "AddTag,Scene One,anal", "AddTag,Scene One,2asvp", "Disable,Scene One",
                                                 "nonsense"])
        logs = actionlog.build_logs([log])
        self.assertEqual([(o.remove, o.scene, o.tag, o.stage) for o in logs.ops],
                         [(False, "scene one", "legup", 0), (True, "scene one", "aggressive", 0), (False, "scene one", "2asvp", 2)])
        self.assertEqual((log.official_tags, log.disables, log.invalid, log.stage_labels), (1, 1, 1, 1))
        self.assertEqual(logs.converter_entries[0], {"action": "addtag", "anim": "Scene One", "tag": "Leg Up"})
        self.assertEqual(len(logs.converter_entries), 4)  # 2-part lines are left out, not a crash

    def test_stage_labels(self):
        self.assertEqual(actionlog.stage_of_label("3fv"), 3)
        self.assertEqual(actionlog.stage_of_label("12bsvp"), 12)
        self.assertEqual(actionlog.stage_of_label("3xyz"), 0)
        self.assertEqual(actionlog.stage_of_label("1234fv"), 0)
        self.assertEqual(actionlog.stage_of_label("fv"), 0)


class PatchTests(unittest.TestCase):
    def test_add_remove_label(self):
        data, applied = patcher.patch_slr(SAMPLE, ops("AddTag,billyy doggy,NymTag", "RemoveTag,Billyy Doggy,rough",
                                                       "AddTag,Billyy Doggy,2bsvp", "AddTag,Billyy Doggy,BILLYY"))
        f = slr.parse_slr(data)
        self.assertEqual([s.tags for s in f.scenes[0].stages], [["billyy", "nymtag"], ["billyy", "nymtag", "2bsvp"]])
        self.assertEqual([s.tags for s in f.scenes[1].stages], [["solo"]])
        self.assertEqual((applied.added, applied.removed), (2, 1))
        self.assertEqual([s.id for s in f.scenes[0].stages], ["stage001", "stage002"])

    def test_idempotent_and_untouched(self):
        once, _ = patcher.patch_slr(SAMPLE, ops("AddTag,Billyy Doggy,x"))
        twice, applied = patcher.patch_slr(once, ops("AddTag,Billyy Doggy,x"))
        self.assertEqual(once, twice)
        self.assertEqual(applied.added, 0)
        same, _ = patcher.patch_slr(SAMPLE, ops("AddTag,Nobody,x"))
        self.assertIs(same, SAMPLE)

    def test_label_for_missing_stage_goes_everywhere(self):
        data, _ = patcher.patch_slr(SAMPLE, ops("AddTag,Other,5fv"))
        self.assertEqual(slr.parse_slr(data).scenes[1].stages[0].tags, ["solo", "5fv"])

    def test_official_tags(self):
        self.assertIs(patcher.patch_slr(SAMPLE, ops("AddTag,Other,anal"))[0], SAMPLE)
        data, _ = patcher.patch_slr(SAMPLE, ops("AddTag,Other,anal", official=True))
        self.assertEqual(slr.parse_slr(data).scenes[1].stages[0].tags, ["solo", "anal"])

    def test_project_mirror(self):
        project = {"scenes": {"scene001": {"id": "scene001", "name": "Billyy Doggy",
                                           "stages": [{"tags": ["Billyy", "Rough"]}, {"tags": ["Billyy", "Rough"]}]}}}
        self.assertTrue(patcher.patch_project(project, ops("RemoveTag,Billyy Doggy,rough", "AddTag,Billyy Doggy,Leg Up")))
        self.assertEqual(project["scenes"]["scene001"]["stages"][0]["tags"], ["Billyy", "legup"])
        self.assertIn("\r\n", patcher.dump_project(project))


class ConvertHelperTests(unittest.TestCase):
    def test_slr_stem(self):
        self.assertEqual(convert._slr_stem({"pack_name": " Billyy ", "pack_version": ""}), "Billyy")
        self.assertEqual(convert._slr_stem({"pack_name": "P", "pack_version": " v1.2 beta! "}), "P_v1.2_beta")
        self.assertEqual(convert._slr_stem({"pack_name": "", "prefix_hash": "ab12"}), "ab12")

    def test_drop_md(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp, "meshes", "FNIS_X_List.txt")
            path.parent.mkdir(parents=True)
            path.write_bytes(b"b -md ab12E1 E1.hkx\r\nb -o,md,a,Tn ab12E2 E2.hkx AO\r\n")
            convert._drop_md(Path(tmp))
            self.assertEqual(path.read_bytes(), b"b ab12E1 E1.hkx\r\nb -o,a,Tn ab12E2 E2.hkx AO\r\n")

    def test_keep_stage_ids(self):
        def scene(stage_ids, events):
            stages = [{"id": s, "positions": [{"event": [e]}]} for s, e in zip(stage_ids, events)]
            graph = {s: {"dest": [stage_ids[i + 1]] if i + 1 < len(stage_ids) else [], "x": 0, "y": 0}
                     for i, s in enumerate(stage_ids)}
            return {"id": "scene001", "name": "S", "stages": stages, "root": stage_ids[0], "graph": graph}
        old = {"pack_name": "P", "scenes": {"scene001": scene(["old00001", "old00002"], ["E1", "E2"])}}
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp, "P.slsb.json")
            path.write_text(json.dumps({"pack_name": "P", "scenes": {"scene001": scene(["new00001", "new00002"], ["E1", "E2"])}}))
            self.assertEqual(convert._keep_stage_ids(Path(tmp), {"P": old}), 2)
            kept = json.loads(path.read_text())["scenes"]["scene001"]
            self.assertEqual([s["id"] for s in kept["stages"]], ["old00001", "old00002"])
            self.assertEqual(kept["root"], "old00001")
            self.assertEqual(kept["graph"]["old00001"]["dest"], ["old00002"])


class EngineTests(unittest.TestCase):
    def test_patch_run_and_summary(self):
        from slsbpatcher import engine
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp, "Data")
            registry = data / "SKSE" / "SexLab" / "Registry" / "Pack.slr"
            registry.parent.mkdir(parents=True)
            registry.write_bytes(SAMPLE)
            log = data / "SKSE" / "Plugins" / "SLATE" / "SLATE_ActionLog - Test.json"
            log.parent.mkdir(parents=True)
            log.write_text(json.dumps({"stringList": {"slate.actionlog": [
                "AddTag,Billyy Doggy,NymTag", "RemoveTag,Billyy Doggy,rough", "AddTag,Nobody Here,x"]}}))
            out = Path(tmp, "out")
            options = engine.Options(inputs=[data], out=out, convert=False)
            outcome = engine.execute(options)
            self.assertEqual(outcome.written, 1)
            [patched] = outcome.patched
            self.assertEqual((patched.added, patched.removed, patched.scenes), (1, 1, {"Billyy Doggy"}))
            self.assertEqual(outcome.unmatched, ["nobody here"])
            summary = "\n".join(outcome.summary)
            self.assertIn("Patched 1 of 1 registry: 1 tag added, 1 removed, in 1 scene.", summary)
            self.assertIn("SLATE_ActionLog - Test.json: +1 -1", summary)
            self.assertEqual(slr.parse_slr((out / "SKSE/SexLab/Registry/Pack.slr").read_bytes()).scenes[0].stages[0].tags,
                             ["billyy", "nymtag"])
            # the output is skipped as an input next time, and an untouched rerun writes nothing
            again = engine.execute(engine.Options(inputs=[data, out], out=out, convert=False, skip_logs=["SLATE_ActionLog - Test"]))
            self.assertEqual((again.written, again.patched), (0, []))


class SkipTests(unittest.TestCase):
    def test_unticked_pack_and_registry_are_left_alone(self):
        from slsbpatcher import engine
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp, "Data")
            registry = data / "SKSE" / "SexLab" / "Registry" / "Pack.slr"
            registry.parent.mkdir(parents=True)
            registry.write_bytes(SAMPLE)
            slal = data / "SLAnims" / "json" / "Pack.json"
            slal.parent.mkdir(parents=True)
            slal.write_text(json.dumps({"name": "Pack", "animations": [
                {"name": "Billyy Doggy", "tags": "billyy", "actors": [
                    {"type": "female", "stages": [{"id": "B_Doggy_A1_S1"}, {"id": "B_Doggy_A1_S2"}]},
                    {"type": "male", "stages": [{"id": "B_Doggy_A2_S1"}, {"id": "B_Doggy_A2_S2"}]}]}]}))
            log = data / "SKSE" / "Plugins" / "SLATE" / "SLATE_ActionLog - Test.json"
            log.parent.mkdir(parents=True)
            log.write_text(json.dumps({"stringList": {"slate.actionlog": ["AddTag,Billyy Doggy,NymTag"]}}))
            found = engine.scan([data], [])
            self.assertEqual(found.registry_sources(), {0: [0]})

            base = dict(inputs=[data], out=Path(tmp, "out"), convert=False, dry_run=True)
            self.assertEqual(len(engine.execute(engine.Options(**base), found).patched), 1)
            skipped = engine.execute(engine.Options(**base, skip_packs=["Pack.json"]), found)
            self.assertEqual((skipped.patched, skipped.left_alone_packs, skipped.left_alone_registries),
                             ([], ["Pack.json"], ["SKSE/SexLab/Registry/Pack.slr"]))
            self.assertIn("Left alone (unticked): 1 SLAL pack, 1 registry.", "\n".join(skipped.summary))
            by_registry = engine.execute(engine.Options(**base, skip_registries=["pack.slr"]), found)
            self.assertEqual((by_registry.patched, by_registry.left_alone_registries), ([], ["SKSE/SexLab/Registry/Pack.slr"]))


class Mo2Tests(unittest.TestCase):
    def test_installed_as_a_mod_and_vfs_merged_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            mo2, game = root / "MO2", root / "Game Root"
            mo2.mkdir()
            (mo2 / "ModOrganizer.ini").write_text(
                "[General]\ngamePath=@ByteArray(" + str(game).replace("\\", "\\\\") + ")\n"
                "selected_profile=@ByteArray(Main)\n", encoding="utf-8")
            (mo2 / "profiles" / "Main").mkdir(parents=True)
            # modlist.txt lists the highest priority first
            (mo2 / "profiles" / "Main" / "modlist.txt").write_text(
                "+slsbPatcher Output\n+PackA\n-Disabled\n+Tools_separator\n", encoding="utf-8")
            exe = mo2 / "mods" / "slsbPatcher" / "slsbPatcher.exe"
            exe.parent.mkdir(parents=True)
            exe.write_bytes(b"")

            def put(folder, rel, data=b"x"):
                path = folder / rel
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
            put(mo2 / "mods" / "PackA", "SKSE/SexLab/Registry/A.slr", b"pack")
            out = mo2 / "mods" / "slsbPatcher Output"
            put(out, "SKSE/SexLab/Registry/A.slr", b"patched")
            put(out, "SKSE/SexLab/Registry/New.slr", b"converted")
            put(out, "slsbPatcher/manifest.json", b"{}")
            # The game's Data folder as MO2's VFS shows it: every enabled mod merged, plus a real file.
            data = game / "Data"
            put(data, "SKSE/SexLab/Registry/A.slr", b"patched")
            put(data, "SKSE/SexLab/Registry/New.slr", b"converted")
            put(data, "SKSE/SexLab/Registry/Real.slr", b"real")
            put(data, "slsbPatcher/manifest.json", b"{}")

            instance = gamedata.mo2_instance_of(exe)
            self.assertEqual((instance.folder, instance.selected_profile()), (mo2, "Main"))
            self.assertIsNone(gamedata.mo2_instance_of(root))

            loaded = gamedata.load([mo2])
            self.assertEqual([layer.name for layer in loaded.layers], ["(game Data)", "PackA"])
            self.assertEqual([layer.name for layer in loaded.skipped_outputs], ["slsbPatcher Output"])
            winners = {w.rel: (w.layer.name, w.path.read_bytes()) for w in loaded.files("skse/sexlab/registry/", ".slr")}
            self.assertEqual(winners, {"SKSE/SexLab/Registry/A.slr": ("PackA", b"pack"),
                                       "SKSE/SexLab/Registry/Real.slr": ("(game Data)", b"real")})


class GameDataTests(unittest.TestCase):
    def test_layers_and_winners(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for pack, content in (("A", b"1"), ("B", b"2")):
                p = root / "packs" / pack / "SKSE" / "SexLab" / "Registry" / "X.slr"
                p.parent.mkdir(parents=True)
                p.write_bytes(content)
            (root / "packs" / "!parked" / "SLAnims" / "json").mkdir(parents=True)
            data = gamedata.load([root / "packs"])
            self.assertEqual([layer.name for layer in data.layers], ["A", "B"])
            [winner] = data.files("skse/sexlab/registry/", ".slr")
            self.assertEqual(winner.path.read_bytes(), b"2")
            marker = root / "packs" / "B" / gamedata.OUTPUT_MARKER[0] / gamedata.OUTPUT_MARKER[1]
            marker.parent.mkdir()
            marker.write_text("{}")
            data = gamedata.load([root / "packs"])
            self.assertEqual([layer.name for layer in data.layers], ["A"])
            self.assertEqual(data.files("skse/sexlab/registry/", ".slr")[0].path.read_bytes(), b"1")


if __name__ == "__main__":
    unittest.main()
