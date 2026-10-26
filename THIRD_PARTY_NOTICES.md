# Third-party notices

## slsb-convert.py

`slsbpatcher/vendor/slsb_convert.py` is `slsb-convert.py` 1.3 from
https://github.com/SLP-Community/slsb-convert.py (commit `eabea7d`), GPL-3.0, by the
SLP-Community contributors. The local changes are listed at the top of the file.

## SexLab Scene Builder

`scene_builder_cli.exe` is built from SexLab Scene Builder,
https://github.com/SLP-Community/SexLab-Scene-Builder (the `lib/SexLab-Scene-Builder`
submodule; the release zip names the commit), by Scrab, I unpause and Miss Corruption, under
the Apache License 2.0 (`licenses/SexLab-Scene-Builder-LICENSE.txt` in the release zip). Its
behavior generator packs Havok files with serde-hkx
(https://github.com/SARDONYX-sard/serde-hkx, MIT OR Apache-2.0). The other Rust crates it is
built with keep their own licenses (MIT and/or Apache-2.0).

## SLATE NG

The registry reader and the patch rules (`slsbpatcher/slr.py`, `slsbpatcher/actionlog.py`,
`slsbpatcher/patcher.py`) are ported from SLATE NG, https://github.com/crajjjj/SLATE-NG,
GPL-3.0.
