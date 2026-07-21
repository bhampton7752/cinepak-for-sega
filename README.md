# Cinepak for Sega — the Sega CD 'SM' movie codec

Documentation and a reference **player** for the FMV codec used by
early Sega CD titles inside Sega FILM (.MVD) containers — previously
listed among MultimediaWiki's *Undiscovered Video Codecs* ("suspected
to be some kind of vector quantizer that exploits the tiling
properties of the Sega CD's video hardware"). That suspicion is
confirmed and the format is now fully specified: a temporal-delta +
two-level vector-quantization scheme over the console's native 8×8
tiles, decoded across both of the Sega CD's 68000 CPUs.

Derived from complete disassembly of the shipped decoders in
**Jurassic Park** (Sega/BlueSky, 1993) and validated by decoding the
retail corpus — nine .MVD files, 3,039 frames, three frame sizes,
100% of frames, output verified against real gameplay.

## Contents
- **SM_MOVIE_CODEC.md** — the full specification: container, both
  frame formats, both decoders (with source addresses), presentation
  pipeline, playback protocols, verification record, encoder notes.
- **mvd_player.py** — a reference player. Pure Python standard
  library (tkinter), zero dependencies. Plays .MVD files with
  STAB-driven timing, exactly as the shipped game player paces them.

## Playing a movie
    python3 mvd_player.py MOVIE.MVD [--scale N]
Space = play/pause · arrows = step · Home = restart · L = loop ·
Esc = quit. `--info` prints container details; `--check` verifies a
file decodes without opening a window.

This tool is a **player only** — it does not convert, export, or
extract assets.

## Scope and status
The specification is complete for the Jurassic Park profile and
byte-exact against its corpus. Other titles' 'sega' streams should be
checked before treating the spec as universal — verification reports
and test results from other games are very welcome.

## Roadmap
Planned direction: broaden the player toward the wider Sega FILM /
Cinepak family, roughly in this order —
1. **Other 'sega'-codec titles** (Sega CD era): decode-verify streams
   from additional games against the spec; document any per-title
   deviations. Test files and reports welcome via issues.
2. **Standard Cinepak ('cvid') FILM files**: the Saturn-era container
   sibling, so one player covers the whole FILM family.
3. **Robustness**: tolerate container variations (STAB layouts,
   base frequencies, dimensions) beyond the Jurassic Park profile.

## Prior art & credits
bgvanbur's **scdmoviedecode.pl** (2011) decoded this format from
black-box analysis long before this specification existed, and its
double-buffered skip handling anticipated what the disassembly
confirms. This project's contribution is the formal from-silicon
specification (both shipped decoders at address level), the solved
header fields, and corpus validation.

## Legal
This repository contains original documentation and original code
only: no game assets, no disc data, no copyrighted material. Movie
files are not included; use files from a disc you own.
