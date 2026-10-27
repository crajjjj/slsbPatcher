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

Everything goes into one output folder, a mod of its own (installed in MO2, slsbPatcher makes it
for you). Your packs and your other mods are never changed.

## What you need

* SexLab P+ 2.17 or newer.
* Windows. The release zip holds `slsbPatcher.exe` (the window), `slsbPatcher-cli.exe` (the
  command line) and `scene_builder_cli.exe`; nothing else is needed (no FNIS for Modders, no
  hkxconv, no base game copy, no Python).
* Pandora or Nemesis, run after any slsbPatcher run that converted a pack.

## Install

Download `slsbPatcher-<version>.zip` from the
[Releases page](https://github.com/crajjjj/slsbPatcher/releases). It holds three programs and
their licenses, no game files:

| File | |
|---|---|
| `slsbPatcher.exe` | the window |
| `slsbPatcher-cli.exe` | the same work from a command prompt |
| `scene_builder_cli.exe` | SexLab Scene Builder, used for conversions; keep it beside the other two |

### With Mod Organizer 2 (recommended)

Set it up like Pandora: a mod that holds the program, and an executable entry that runs it.

1. **Install the zip as a mod.** In MO2, *Install a new mod from an archive* (the disc icon,
   or drag the zip onto the left pane), and name it `slsbPatcher`. MO2 will say the archive
   does not look like a mod, because it has no game data: choose to install it anyway.
2. **Enable the mod.** Tick `slsbPatcher` in the left pane. MO2 only runs a program that sits
   inside a mod when that mod is enabled. Its position in the list does not matter.
3. **Add the executable.** Open *Tools > Executables* (the gear icon, or Ctrl+E), press the
   `+` and choose *Add from file*, then pick `<your MO2>\mods\slsbPatcher\slsbPatcher.exe`.
   The fields:
   * *Title*: `slsbPatcher`
   * *Binary*: the path you just picked
   * *Start in* and *Arguments*: leave empty
   * *Create files in mod instead of overwrite*: leave **off**. slsbPatcher writes its own mod
     and never touches `overwrite`.

   Press *OK*. `slsbPatcher` now appears in the executables drop-down next to *Run*.
4. **Run it.** Pick `slsbPatcher` in the drop-down and press *Run*. The window opens, finds the
   MO2 it is installed in, reads the profile MO2 has selected, and previews at once. Look at
   the report, untick what you want left alone, press the big **Run** button, then **close
   slsbPatcher**. MO2 waits for it and refreshes when it closes.
5. **Enable the output.** MO2 now lists a new mod, **`slsbPatcher Output`**. Tick it and drag it
   to the bottom of the left pane (the highest priority), so its registries win over the
   originals. If the report says packs were converted, run Pandora (or Nemesis) afterwards.
   **Keep your conversion mods and SLAL packs enabled.** The output holds only the files that
   changed (the patched registries, new conversions) and lays them over the originals; with the
   originals disabled, SexLab P+ has almost no scenes left and nothing starts.

From then on it is steps 4 and 5: slsbPatcher replaces `slsbPatcher Output` on every run and
always reads your original files, never its own earlier output, so you can re-run after every
new action log or pack. Keep the SLAL packs installed: conversions use their animation files.

To **update slsbPatcher**, install the new zip over the `slsbPatcher` mod (MO2: *Replace*). The
executable entry keeps working; the ticks you made and the log stay (`slsbPatcher.settings.json`
and `slsbPatcher.log` live in the mod folder). To **undo** everything slsbPatcher did, untick
`slsbPatcher Output`. To **remove** it, delete the two mods and the executable entry.

If you prefer the command line, add `slsbPatcher-cli.exe` as an executable the same way.
Started with no arguments it does a full run on the selected profile and waits for Enter
before its window closes.

### With Vortex, or without a mod manager

Extract the zip anywhere outside the game folder, for example `D:\Tools\slsbPatcher`, and start
`slsbPatcher.exe`. It asks for a load order under *Menu > Options*: add your mod manager's
staging folder of extracted packs, or the game's `Data` folder, or just drop such a folder onto
`slsbPatcher.exe`. After **Run**, install the output folder (`slsbPatcher Output`, next to the
exe unless you chose another place) as a mod: zip it and install it with Vortex, giving it a
*load after* rule over your animation packs, or copy its contents into `Data`. Run Pandora
afterwards if packs were converted. Run slsbPatcher again whenever a log or a pack changes,
and replace the installed output with the new one.

## The window

It is laid out like Pandora. On the left, everything the run can touch, each row with a tick
box; on the right, the report; at the bottom, one big **Run** button.

* **SLAL packs and registries**: every SLAL json in the load order (and every registry that no
  installed SLAL json is the source of, such as a hand-made or carried-over conversion), with
  its mod, its number of animations, and what the run does with it: *patched*, *up to date*,
  *no registry: converted*, *left alone*. Untick a row to leave that pack alone: it is not
  converted, and the registry made from it is not patched. Right-click a pack for
  **Re-convert** (see below).
* **SLATE action logs**: the logs installed in the load order, with how many `AddTag` and
  `RemoveTag` lines they hold and what they change. Untick a log to leave it out. Add other
  logs (Hentairim tag logs, for example) under *Menu > Options*.
* The tick box in a column header ticks or unticks every row shown; the search box at the top
  filters both lists.
* **Report** shows the summary and then the details of what a run would do, refreshed after
  every tick. **Changes** opens the same up mod by mod, registry by registry and scene by
  scene, down to every tag added or removed and the action log it came from.
* **Run** writes the output. The status line under the lists says where it went and what to
  do next (install it, close slsbPatcher for MO2, run Pandora).

*Menu > Options* holds the load order (an MO2 instance or profile folder, a Data folder, or a
folder of extracted SLAL packs; or drop such a folder onto `slsbPatcher.exe`), extra action
logs, patch and convert on or off, P+ official tags, the converter settings and the output
folder. The window remembers everything, ticks included, in `slsbPatcher.settings.json` beside
the exe, so the next time it opens it previews at once and Run is one click.

## Command line

`slsbPatcher-cli.exe` does the same work without the window:

```
slsbPatcher-cli
slsbPatcher-cli "C:\Modding\MO2\profiles\Default"
slsbPatcher-cli "C:\Modding\MO2\profiles\Default" --logs "D:\Hentairim Tags" --skip-log "SLATE_ActionLog - OsmelMC.json"
slsbPatcher-cli "D:\Extracted SLAL Packs" --logs "D:\SLATE logs" --out "D:\My Conversion"
slsbPatcher-cli "C:\Games\Skyrim Special Edition\Data"
```

Without a folder it uses the MO2 it is installed in. Add `--dry-run` to see the report without
writing anything, and `--summary` to print only its summary. The report is also saved in the
output as `slsbPatcher/report.txt`.

## The report

It starts with a summary:

```
SUMMARY
  Read 2 action logs (3564 lines) and 69 registries from 2330 mods.
  Patched 9 of 69 registries: 4 tags added, 12 removed, in 16 scenes.
    By mod:
      [NoDelete][9810] Automated SLSB Conversions - Hentairim v1.3: 6 registries, +2 -11
      [NoDelete][9811] Automated SLSB Conversions - FB Milky Nibbles: 3 registries, +2 -1
    By action log:
      SLATE_ActionLog - Nymra.json: no changes (already in the registries, or for scenes not installed)
      SLATE_ActionLog - OsmelMC.json: +4 -12
    Tags: -furniture x8, +furniture x2, +vampire x2, -futa x2, -.behind x1, -femdom x1
  3 SLAL jsons are only partly converted (67 animations in no registry; re-convert to add them): ...
```

then the details: every registry with each tag change, its scene and its log, the conversions,
the scenes named in the logs that are not installed, and any problems.

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
that usually means the pack was updated after the conversion was made. Re-convert (the tick box
in the window, `--reconvert <json>` on the command line) converts it again, keeping the old registry's pack hash, scene ids and stage ids, and writes the
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
| `-o`, `--out DIR` | output folder (default: the mod `slsbPatcher Output` when installed in MO2, else `slsbPatcher Output` in the current folder) |
| `-l`, `--logs PATH` | extra action log file or folder, e.g. Hentairim tag logs; repeatable |
| `--skip-log NAME` | ignore an installed action log by file name; repeatable |
| `--no-installed-logs` | ignore every action log found in the inputs |
| `--official-tags` | also patch lines naming P+ official tags |
| `--no-convert` | only patch existing registries |
| `--no-patch` | only convert packs that have no registry |
| `--skip-pack NAME` | leave this SLAL json alone: not converted, its registry not patched; repeatable |
| `--skip-registry NAME` | leave this registry (.slr name or path) alone; repeatable |
| `--reconvert NAME` | convert a SLAL json (file name or stem), or a whole mod, even though a registry covers it |
| `-u`, `--update DIR` | folder of `*.slsb.json` from an earlier conversion, to keep its hash and ids |
| `--author NAME` | pack author written into new conversions |
| `--stricter-futa` | `slsb-convert.py -sf` |
| `--zip` | also pack the output into a zip beside it |
| `-n`, `--dry-run` | report only |
| `-s`, `--summary` | print only the summary of the report |
| `--scene-builder EXE` | a different `scene_builder_cli.exe` |

## Language

The window and the command line follow the language of Windows; English is the fallback.
To use another one, pick it in **Menu > Options → Interface → Language**, pass `--lang zh_CN`, or set
`SLSBPATCHER_LANG=zh_CN`. A new language takes effect the next time slsbPatcher starts.

```
py -m slsbpatcher --lang zh_CN            # the window
slsbPatcher-cli.exe --lang zh_CN ...      # the command line
```

### Translating

The text is in `slsbpatcher/locale/<lang>/LC_MESSAGES/slsbpatcher.po`; there is no build
step, so the file a translator edits is the file the program reads. `msgid` is the English
sentence and stays as it is; only `msgstr` is translated. `%(name)s` marks a value that is
filled in later — keep every placeholder, and feel free to reorder them to suit the language.

```
py scripts/i18n.py extract   # refresh slsbpatcher/locale/slsbpatcher.pot from the source
py scripts/i18n.py check     # fail on a missing entry, a fuzzy line, a changed placeholder...
py scripts/i18n.py stats     # how much of each language is done
```

A translation is rejected when a placeholder is missing or renamed, when a line is more than
the console can show in a non-Unicode Windows code page, or when it is marked `#, fuzzy`
(that mark means the English changed and the text above it is stale).

Chinese needs no plural handling: its `.po` declares `nplurals=1`, so one `msgstr[0]`
covers every count.

### Changing a few lines without a new build

A `.po` file of your own overrides single lines of the built-in translation; the rest stays
as shipped. Put it in either place — the later one wins:

```
<folder of slsbPatcher.exe>/locale/zh_CN/LC_MESSAGES/slsbpatcher.po
%APPDATA%/slsbPatcher/locale/zh_CN/LC_MESSAGES/slsbpatcher.po
```

A file holding one entry changes one line:

```po
msgid "  Patching is off."
msgstr "  未进行打补丁。"
```

This is safe to take from anyone: a `.po` cannot run code or read files. The worst it can
do is show the wrong words. If the English of an entry changes in a later version, that
entry falls back to English instead of showing an outdated translation.

## Building from source

```
git clone --recurse-submodules https://github.com/crajjjj/slsbPatcher
cd slsbPatcher
py scripts/build.py --no-exe        # builds bin/scene_builder_cli.exe (Rust 1.95+) and runs the tests
py -m slsbpatcher                   # the window (Python 3.10+ with tkinter)
py -m slsbpatcher --help            # the command line
```

`py scripts/build.py` also builds `slsbPatcher.exe` and `slsbPatcher-cli.exe` with PyInstaller (from a `.venv` in the repo)
and packs `Release/slsbPatcher-<version>.zip`.

## Credits and license

slsbPatcher is GPL-3.0. It contains:

* `slsb-convert.py` by the SLP-Community contributors (GPL-3.0), in `slsbpatcher/vendor`, with the
  local changes listed at the top of the file;
* the patch rules of SLATE NG (GPL-3.0);
* `scene_builder_cli.exe`, built from SexLab Scene Builder by Scrab, I unpause and Miss Corruption
  (Apache-2.0).

See `THIRD_PARTY_NOTICES.md`.
