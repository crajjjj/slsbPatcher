# slsbPatcher

Keeps your SexLab P+ scene registries in step with your SLAL animation packs and your
SLATE action logs.

SexLab P+ does not read SLAL packs or SLATE action logs. It reads scene registries
(`.slr` files) built by SexLab Scene Builder. slsbPatcher looks at your whole load order and,
for every SLAL pack:

* **the pack already has a conversion** (an automated SLSB conversion, or a registry the pack's
  author made): that registry is **patched** with your action logs. Only tags change. Scenes,
  the stage graph, scene and stage ids and offsets stay exactly as the registry's author made
  them, so saves keep their scene settings.
* **the pack has no conversion yet**: it is **converted**, with the same tagging as the
  community `slsb-convert.py` dev environment, and Scene Builder 3 writes the registry, the
  FNIS list and the behavior files.

Everything goes into one output folder that you install as a mod. Your packs and your other
mods are never changed.

## What you need

* SexLab P+ 2.17 or newer.
* Windows. The release zip holds `slsbPatcher.exe` and `scene_builder_cli.exe`; nothing else
  is needed (no FNIS for Modders, no hkxconv, no base game copy, no Python).
* Pandora or Nemesis, run after any slsbPatcher run that converted a pack.

## Use

Drop your **MO2 profile folder** (for example `MO2\profiles\Default`) onto `slsbPatcher.exe`.
The result is written to `slsbPatcher Output` next to the exe. Install that folder as a mod
with the highest priority (MO2: the bottom of the left pane; Vortex: load it after
everything), then run Pandora if the report says it converted something.

From a command prompt you have every option:

```
slsbPatcher "C:\Modding\MO2\profiles\Default"
slsbPatcher "C:\Modding\MO2\profiles\Default" --logs "D:\Hentairim Tags" --skip-log "SLATE_ActionLog - OsmelMC.json"
slsbPatcher "D:\Extracted SLAL Packs" --logs "D:\SLATE logs" --out "D:\My Conversion"
slsbPatcher "C:\Games\Skyrim Special Edition\Data"
```

Run it again whenever a log or a pack changes. It skips its own earlier output while reading,
so the new output is always made from the original files; just replace the old output mod with
the new one.

Add `--dry-run` to see the report without writing anything. The report is also saved in the
output as `slsbPatcher/report.txt`.

## Inputs

An input is one of:

| Input | Read as |
|---|---|
| an MO2 profile folder, or an MO2 instance folder | the profile's enabled mods in priority order, with the game's Data folder below them and `overwrite` on top |
| a folder laid out like Data (it has `SLAnims`, `SKSE` or `meshes` in it) | one mod |
| any other folder | every folder in it that has `SLAnims` or `SKSE` is a mod; a name starting with `!` is left out |

Several inputs are layered, the last one winning. From those mods slsbPatcher reads, as the
game would see them:

* `SLAnims/json/*.json` and `SLAnims/source/*.txt` (the SLAL packs),
* the packs' FNIS lists, `meshes/actors/<race>/animations/<pack>/FNIS_*_List.txt`,
* `SKSE/SexLab/Registry/**/*.slr` and the Scene Builder projects beside them
  (`*.slsb.json`, usually in `Registry/Source`),
* the action logs in `SKSE/Plugins/SLATE/*.json`, plus any `--logs` you give it.

Only loose files are read. A pack packed into a BSA is not seen.

## When is a pack "already converted"?

When an installed registry plays one of its animation events. A SLAL json counts as converted
as soon as any of its animations is in a registry. If only some are, the report lists the rest:
that usually means the pack was updated after the conversion was made. `--reconvert <json>`
converts it again, keeping the old registry's pack hash, scene ids and stage ids, and writes the
new registry over the old one.

A re-conversion takes its tags from the SLAL json and the action logs given in that run only.
Pass the logs the earlier conversion was made with, or the tags it got from them are gone.

## Patching rules

These are SLATE NG's rules:

* A log line `AddTag,<scene>,<tag>` finds scenes by name, ignoring case.
* `AddTag` does nothing when any stage of the scene already has the tag; otherwise the tag goes
  on every stage. A stage label (`2fv`, `3asvp`, the SexLab Anim Stage Labels, ASL and Hentairim
  codes) goes on the stage it names.
* `RemoveTag` takes the tag off every stage.
* Tags are stored the way Scene Builder compiles them: lower case, spaces removed (`Leg Up`
  becomes `legup`). Old spellings the converter fixes (`laying`, `agressive`, `invfurn` ...)
  count as the fixed ones.
* Lines naming one of P+'s 55 official tags (`anal`, `leadin`, `oral` ...) are skipped by
  default, because P+ reads those per stage: a scene-wide `leadin` turns every stage into a
  lead-in. The report counts them; `--official-tags` applies them to every stage.
* `Comment` and `Disable` lines are ignored.

When a registry's Scene Builder project is installed with it, the project gets the same edits,
so a later Scene Builder build reproduces the patch.

## Converting

The tagging is `slsb-convert.py` 1.3's own, unchanged: tag repair, Hentairim / ASL / Anim Stage
Labels, position tags, futa, submissive, vampire and scaling flags, furniture. Compared with
running the dev environment:

* Scene Builder 3 does the work of Scene Builder 2 plus FNIS for Modders. Given the same FNIS
  lists, its behavior files are byte-identical to FNIS for Modders' (checked on 49 lists).
* The packs are copied to a temporary folder first; the converter's json fixes never touch the
  installed pack.
* It never stops to ask for a pack's animation folder: it picks the folder whose FNIS list holds
  the pack's events.
* With an earlier conversion (installed, or given with `--update`), stage ids are kept as well
  as scene ids, so per-stage settings saved in P+ survive.
* Any json with a `slate.actionlog` list is an action log, whatever its file name.

FNIS options other than `md`, `o`, `a` and `Tn` (Baka's `-Tsoundplay...` sound triggers, for
one) stay in the FNIS list, but Scene Builder 3's behavior generator does not act on them.

## Options

| Option | |
|---|---|
| `-o`, `--out DIR` | output folder (default: `slsbPatcher Output` in the current folder, or next to the exe when started from Explorer) |
| `-l`, `--logs PATH` | extra action log file or folder, e.g. Hentairim tag logs; repeatable |
| `--skip-log NAME` | ignore an installed action log by file name; repeatable |
| `--no-installed-logs` | ignore every action log found in the inputs |
| `--official-tags` | also patch lines naming P+ official tags |
| `--no-convert` | only patch existing registries |
| `--no-patch` | only convert packs that have no registry |
| `--reconvert NAME` | convert a SLAL json (file name or stem), or a whole mod, even though a registry covers it |
| `-u`, `--update DIR` | folder of `*.slsb.json` from an earlier conversion, to keep its hash and ids |
| `--author NAME` | pack author written into new conversions |
| `--stricter-futa` | `slsb-convert.py -sf` |
| `--zip` | also pack the output into a zip beside it |
| `-n`, `--dry-run` | report only |
| `--scene-builder EXE` | a different `scene_builder_cli.exe` |

## Building from source

```
git clone --recurse-submodules https://github.com/crajjjj/slsbPatcher
cd slsbPatcher
py scripts/build.py --no-exe        # builds bin/scene_builder_cli.exe (Rust 1.95+) and runs the tests
py -m slsbpatcher --help            # Python 3.10+
```

`py scripts/build.py` also builds `slsbPatcher.exe` with PyInstaller (from a `.venv` in the repo)
and packs `Release/slsbPatcher-<version>.zip`.

## Credits and license

slsbPatcher is GPL-3.0. It contains:

* `slsb-convert.py` by the SLP-Community contributors (GPL-3.0), in `slsbpatcher/vendor`, with the
  local changes listed at the top of the file;
* the patch rules of SLATE NG (GPL-3.0);
* `scene_builder_cli.exe`, built from SexLab Scene Builder by Scrab, I unpause and Miss Corruption
  (Apache-2.0).

See `THIRD_PARTY_NOTICES.md`.
