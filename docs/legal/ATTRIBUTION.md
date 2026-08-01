# Attribution, data provenance, and project status

Meowtoko E7 Tool is a free, unofficial fan-made companion for Epic Seven. It is not
affiliated with, endorsed by, or sponsored by Smilegate, Super Creative, or
the Epic Seven rights holders. Epic Seven names, characters, statistics, and
other game material remain the property of their respective rights holders.
This project uses public catalog facts with attribution under the project
owner's stated fair-use basis; that statement is context, not a legal
guarantee.

## Character and artifact snapshot

The desktop package contains an offline English snapshot derived from
[RexQian's Fribbels Epic 7 Optimizer offline fork](https://github.com/RexQian/Fribbels-Epic-7-Optimizer/tree/feat/offline):

- repository revision: `f49b0676c27d893ae4aa1b69920e4c98f37eb3fb`;
- revision date: `2026-07-16T16:00:28Z`;
- cache update commit: `dab0509584b1405aa13f5e1ddbfea9d919269fe8` (`update: patch 20260716`);
- declared offline app version: `1.11.0-offline.20260108`;
- 386 hero records, raw SHA-256 `a5ed0b641e578a2b290b75d6f75a866a93b91e40c1064a4f1a264630a745c349`;
- 283 artifact records, raw SHA-256 `ed1bb666ae7465560fbc1a163000966821174b0a48be826b28da16021f463ac0`.

The snapshot is fixed at build time. Meowtoko E7 Tool does not fetch changing character
data at runtime. Exact source paths, Git blob identities, normalization rules,
and reproducible hashes are recorded in
[`CHARACTER_SNAPSHOT.md`](../../src/optimizer/data/CHARACTER_SNAPSHOT.md) and the
bundled `manifest-v1.json`.

The source project identifies Fribbels as its author, the offline fork is
maintained by RexQian, and its `app/package.json` declares MIT. The pinned root
tree did not contain a standalone `LICENSE` file, so Meowtoko E7 Tool records that
package declaration without inventing missing license text.

## Fribbels inventory compatibility

Meowtoko E7 Tool's `gear.txt` reader and Fribbels-style metric behavior were implemented
against the same pinned offline revision above. “Compatible with Fribbels”
describes file and calculation compatibility; it does not imply affiliation or
endorsement. Fixture lineage and exact accepted shapes are recorded in
[`tests/fixtures/fribbels/manifest.json`](../../tests/fixtures/fribbels/manifest.json)
and the data contracts under [`src/optimizer/data`](../../src/optimizer/data).

## Fribbels equipment artwork

The six equipment-slot icons and twenty-four set icons under
[`assets/equipment`](../../assets/equipment) are unmodified copies from
[Fribbels Epic 7 Optimizer](https://github.com/fribbels/Fribbels-Epic-7-Optimizer)
revision `b291cbbc415f11abede146859edc7b67d26e9c4b`. The source project maps these
files through `app/js/lib/assets.js`, and its `app/package.json` declares MIT.
Exact source URLs, byte lengths, and SHA-256 hashes are recorded beside the
files in [`slots/SOURCE.md`](../../assets/equipment/slots/SOURCE.md) and
[`sets/SOURCE.md`](../../assets/equipment/sets/SOURCE.md).

Epic Seven artwork and game material remain the property of their respective
rights holders. This attribution records the technical source and declared
license; it does not imply endorsement.

## Meowtoko E7 Tool and bundled software

The Meowtoko E7 Tool desktop package currently declares `UNLICENSED` in
[`desktop/package.json`](../../desktop/package.json). That means the repository
does not grant a general reuse or redistribution license merely because its
source can be viewed. Third-party components retain their own licenses and
terms. The packaged runtime notice and collected license texts are under
`resources/runtime` in the installed application; the source notice is
[`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md).

Scapy is bundled in the frozen backend for packet capture and retains its
GPLv2 terms. Npcap is a separately installed system dependency and is not
redistributed. Ollama, Tesseract, Android Platform Tools/ADB, CuPy, NVIDIA
components, Npcap, and the NVIDIA driver are not silently bundled as ordinary
Meowtoko E7 Tool application code. Health Center identifies which optional capability is
missing and keeps unrelated workflows available.
