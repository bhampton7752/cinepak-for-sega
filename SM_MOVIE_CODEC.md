# Cinepak for Sega — the 'SM' Movie Codec (Jurassic Park, Sega CD)

Complete specification of the FMV system: MVD (Sega FILM) container,
the compressed 'SM' bitstream, the flat intermediate format, and both
shipped decoders. Derived from full disassembly (SHELL.BIN sub-CPU
kernel; MAINOS.BIN main-CPU OS) and validated by decoding the retail
corpus: nine .MVD files, 3,039 frames, three frame sizes, 100% decode,
output visually confirmed against gameplay. Every claim cites its
source address (MAINOS base $FF0000; SHELL base $7000).

## Contents
1. Architecture
2. MVD container (Sega FILM profile)
3. Compressed frame format
4. Flat (intermediate) frame format
5. Sub-CPU decompressor (SHELL $B8BA)
6. Main-CPU blitter (MAINOS $FF2D74)
7. Presentation & double buffering
8. Playback drivers (three contexts)
9. Global variable map
10. Verification record
11. Writing an encoder

---

## 1. Architecture

Two-stage pipeline across both CPUs:

CD sectors → SUB decompressor $B8BA (temporal delta + two-level VQ
over native 8×8 tiles) → FLAT frame in word RAM → 1M bank swap →
MAIN blitter $FF2D74 (tile DMA + synthesized map) → vblank presenter
$FF2160 (CRAM + map upload) → screen.

This confirms the community's long-standing hypothesis: the codec is
a vector quantizer built directly on the console's tile hardware.

## 2. MVD container (Sega FILM profile)

Big-endian throughout.

| Chunk | Contents |
|---|---|
| 'FILM' | u32 header length (data area begins at this offset) |
| 'FDSC' | u32 len; codec tag 'sega'; height, width in pixels — per-file (JP ships 320×128 transitions, 120×96 and 128×120 message videos) |
| 'STAB' | u32 len; base frequency; sample count; 16-byte records |

STAB record `{u32 offset, u32 length, u32 t1, u32 t2}`; offset is
relative to the FILM header length; table ends at 0xFFFFFFFF
sentinels. Video records: t1 = cumulative timestamp, t2 = duration in
ticks on the 600/s base (message videos: t2=24 → 25 fps; transition
movies use per-index t1=n, t2=1). Audio chunks interleave between
video samples: STAB marks audio entries with info1 == 0xFFFFFFFF, and
their payload is 8-bit SIGN-MAGNITUDE PCM (bit7 sign, bits6:0
magnitude -- the RF5C164's native format, identical to the area FX
banks); the effective sample rate derives from total duration against
the FILM 600 Hz timebase. Some MVDs are audio-only (zeroed video
descriptor). Correct playback walks STAB and dispatches by entry
type.

## 3. Compressed frame format

12-byte header shared with the flat form (§4), then:

| Section | Size | Notes |
|---|---|---|
| Palettes | npal×32 | copied verbatim to output |
| Palette-select stream | ⌈w·h/16⌉×4 | present only when flags&3≠0 (all retail JP frames: npal=3, stream present) |
| u32 L1 + DICT32 | 4+L1 | 32-bit dictionary (`$B90C`, base a2) |
| u32 L2 + DICT16 | 4+L2 | 16-bit dictionary (`$B912`, base a3) |
| Control stream | ⌈w·h/16⌉×4 | 2 bits/cell, MSB-first (`add.l d5,d5` carries) |
| Payload | var | per control code |

Control codes (output advances one 32-byte tile per cell):

| Code | Meaning | Payload | Ratio |
|---|---|---|---|
| 00 | Skip — keep the tile already in the SAME output buffer, i.e. frame N−2 under the double-buffering (not N−1) | none | ∞ |
| 01 | Literal tile | 32 B | 1:1 |
| 10 | VQ-32: 8 index bytes; each pair → two DICT32 entries emitted word-interleaved (`$B958`) | 8 B | 4:1 |
| 11 | VQ-16: 16 index bytes; hi/lo bytes of each DICT16 entry route to alternating byte-plane accumulators, flushed 2 longs per 4 lookups (`$B988`) | 16 B | 2:1 |

## 4. Flat (intermediate) frame format

| Offset | Size | Field |
|---|---|---|
| +$0 | u16 | Magic 'SM' ($534D) — checked `$FF2D9A` / `$B8CC` |
| +$2 | u16 | Flags; bits1:0 = npal−1 (retail: npal=3) |
| +$4 | u32 | **Decompressed frame size in bytes** = 12 + npal·32 + palsel_longs·4 + w·h·32 (verified byte-exact across all three sizes: 5,916 / 7,848 / 20,748). Encoder metadata; read by no shipped code |
| +$8/+$A | u16 | Width / height in cells |
| +$C | n×32 | Palettes; word 0 of each force-cleared on load (`$FF2DD8`) |
| … | ⌈w·h/16⌉×4 | Palette-select stream: 2-bit codes MSB-first (the `ror #17`/`rol #2` reader is exactly equivalent); code = CRAM line, lands in map bits 14:13 |
| … | w·h×32 | Raw 4bpp tiles, row-major cells |

## 5. Sub-CPU decompressor (SHELL `$B8BA`)

Args (dst, src), callee-pops-8. Copies header+palettes(+palsel when
flags&3) verbatim (`$B8EC–$B908`), loads both dictionaries, then the
control loop (§3). Common exit `$B9D4`. Single caller: the streamer —
the routine is movie-exclusive (verified by exhaustive caller scan).

## 6. Main-CPU blitter (MAINOS `$FF2D74`)

Callee-pops-16; args: (priority, map_vram_base, tiles_ptr,
frame_ptr). Spins on busy flag $FF2228 until the presenter consumed
the prior frame; validates 'SM'; stages palettes (word 0 cleared);
builds the tilemap itself — no map data exists in the stream:

    entry(k) = priority<<15 | palsel(k)<<13 | (tile_base + k)
    tile_base = (tiles_ptr >> 5) + buffer_offset

Tiles upload by split DMA (`$FF2E92`: two halves against the 128 KB
source-bank wrap) through the register writer `$FF2EB6` ($8F02
auto-inc; $8174/$8164 mode toggling; length/source regs $93–$97;
first word CPU-written — the DMA-from-RAM erratum workaround, hence
source programmed as (a1+2)/2). **VRAM destination = the tile
pointer's own value** (low 16 bits): tiles land at the slots the map
references by construction. Tiles are inline in the frame stream —
the source cursor lands on them after the bitstream. A vestigial PIO
path ($FF2E4E–$FF2E7A) is dead code (zero inbound branches,
exhaustively verified). Done flag set on exit.

## 7. Presentation & double buffering

Vblank presenter `$FF2160` (installed at vector $FD08 by wrapper
`$FF204C`, old vector saved/restored): raises INT2 to the sub
(`bset #0,$A12000` — the 60 Hz game tick), and when the done flag is
set uploads palettes to CRAM (command $C0000000) and the staged map
to VRAM rows (base $FF22B0 = arg2; row stride $FF22B4 = 128, static
initialized data matching the 64-cell plane; div-by-8 fast path plus
a word-wise path for other widths), then toggles the buffer selector
$FF222A (per presented frame; initial phase from gate-array reg
$1E==$13), increments frame counter $FF1FC0, and calls an optional
user callback ($FF20A4 = the wrapper's stack argument). Double
buffering: selector 0 → decoder uses the second tile bank
(tile_base += w·h, tiles_ptr += w·h·32). Movie screen init `$FF20A8`
(VDP regs, plane A $E000, sprites $F800, hscroll $FC00; font loader
$FF2112 expands the 1bpp font at $FF2F22).

## 8. Playback drivers (three contexts)

1. **Boot/intro movies** — MAINCODE cmd-2 loop → `DoMoviePlayBack`
   ($FF3278): a4=$200000; reads the parameter triplet {tiles_ptr,
   map_base, priority} from word RAM +$0/+$4/+$8; per frame while
   the sub grants command 2: `Set1MFlag(1)` (1M bank swap — params
   live in one bank, the frame at $200000 in the other; sub builds
   frame N+1 while main blits N), call blitter, swap back.
2. **In-area FMV** (interiors; e.g. I1's A-button cinematics):
   kernel API+$4 start (mode, $20, plane $E000, 0, 6, buffer,
   in-module stream table, size) → API+$13C pump until negative →
   API+$40 stop.
3. **JP-CD Video Sequences** — the node-jumper debug menu's FMV
   player (module N0), driving the same streamer.

## 9. Global variable map (main RAM)

| Address | Contents |
|---|---|
| $FF2228 | busy/done flag |
| $FF222A | buffer selector |
| $FF222C / $FF2230 | palette count−1 / palette staging |
| $FF22B0 / $FF22B4 | map VRAM base (arg2) / row stride (=128, static) |
| $FF22B8 / $FF22BC / $FF22C0 | width / height / map staging |
| $FF1FC0 | vblank frame counter |
| $FF20A0 / $FF20A4 | saved vblank vector / user callback |
| $FFA7D6 | CRAM shadow (fade system; commands $D/$E) |

### JP-profile note on code 00
Jurassic Park's retail streams never emit code 00: all 3,039 corpus
frames are literal/VQ only (fully intra). The skip path exists in the
shipped decoder and its reference is the same-bank previous frame
(N−2); titles that use it should be decoded accordingly.

## 10. Verification record

- Both decoders fully disassembled; every branch attributed; dead PIO
  path proven dead; stride "initializer" proven static data.
- Independent implementation (sm_movie_tools.py) decodes the retail
  corpus 100% (nine MVDs, 3,039 frames, three sizes) via STAB-driven
  playback; output visually confirmed against gameplay (user QA).
- +$4 solved from corpus (decompressed size; formula byte-exact ×3).
- Scope: specified from Jurassic Park; other titles' 'sega' streams
  should be checked before treating the spec as universal.

## 11. Prior art
bgvanbur's scdmoviedecode.pl (2011, GPL) implemented a working
decoder for this format from black-box analysis years before this
specification, including the correct double-buffered skip reference.
This document confirms that implementation from the shipped decoders'
disassembly and resolves its noted unknowns (the flags field = palette
count − 1; the 24-bit length field = decompressed frame size).

## 12. Writing an encoder

Emit 'SM', flags=npal−1, +$4 = flat size, w, h, npal palettes
(word 0 ignored), MSB-first 2-bit palette map padded to a long, then
either raw tiles (flat) or the dictionary/control/payload sections
(§3). For live playback: place data so the tile section's address low
16 bits equal the intended VRAM byte destination; wrap frames in
FILM/FDSC/STAB with 600-base timestamps; feed via the 1M bank-swap
protocol under the movie wrapper.
