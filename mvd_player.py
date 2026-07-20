#!/usr/bin/env python3
"""mvd_player -- a viewer for Sega CD .MVD movies ("Cinepak for Sega").

Plays MVD (Sega FILM / 'sega' codec) files as used by Jurassic Park
(Sega CD, 1993), decoding the 'SM' bitstream documented in
SM_MOVIE_CODEC.md. Pure Python standard library (tkinter display);
no third-party dependencies, no export functionality -- this is a
player, not a converter.

Usage:
    python3 mvd_player.py MOVIE.MVD [--scale N] [--info] [--check]

Controls:
    Space        play / pause
    Left/Right   step one frame
    Home         restart
    L            toggle loop
    Escape / Q   quit

--info prints container details and exits; --check decodes every
frame headlessly and reports (useful for verifying a file).
"""
import sys, struct, argparse, os, tempfile, subprocess, atexit

MAGIC = 0x534D  # 'SM'

# ---------------------------------------------------------------------
# Container: Sega FILM ('FILM' / 'FDSC' / 'STAB')
# ---------------------------------------------------------------------
def parse_film(b):
    if b[:4] != b'FILM':
        raise SystemExit('not a FILM/MVD file')
    hdrlen, = struct.unpack('>I', b[4:8])
    fd = b.find(b'FDSC')
    sp = b.find(b'STAB')
    stlen, = struct.unpack('>I', b[sp+4:sp+8])
    base_freq, = struct.unpack('>I', b[sp+8:sp+12])
    nent = (stlen - 16) // 16
    samples = []
    p = sp + 16
    for _ in range(nent):
        off, ln, t1, t2 = struct.unpack('>IIII', b[p:p+16]); p += 16
        if off == 0xFFFFFFFF:
            break
        pos = hdrlen + off
        is_video = (pos + 2 <= len(b) and
                    struct.unpack('>H', b[pos:pos+2])[0] == MAGIC)
        samples.append(dict(pos=pos, length=ln, t1=t1, t2=t2,
                            video=is_video))
    width = height = 0
    for s in samples:                      # dims from first video frame
        if s['video']:
            w, h = struct.unpack('>HH', b[s['pos']+8:s['pos']+12])
            width, height = w*8, h*8
            break
    return dict(hdrlen=hdrlen, width=width, height=height,
                base=base_freq or 600, samples=samples)

# ---------------------------------------------------------------------
# Codec: 'SM' compressed frame -> flat frame (delta + 2-level VQ)
# Verbatim-semantics port of the corpus-validated reference decoder
# (pixel-exact against 3,039 retail frames).
# ---------------------------------------------------------------------
def _bits2(b, p, n):
    """Read n 2-bit codes, MSB-first from big-endian u32s."""
    codes = []
    acc = 0; bits = 0
    while len(codes) < n:
        if bits == 0:
            acc, = struct.unpack('>I', b[p:p+4]); p += 4; bits = 32
        codes.append((acc >> (bits - 2)) & 3)
        bits -= 2
    return codes, p

def _read_header(b, o):
    flags = struct.unpack('>H', b[o+2:o+4])[0]
    w, h = struct.unpack('>HH', b[o+8:o+12])
    npal = (flags & 3) + 1
    pals = []
    p = o + 12
    for _ in range(npal):
        pal = [struct.unpack('>H', b[p+i*2:p+i*2+2])[0] for i in range(16)]
        pals.append([(((wd >> 1) & 7)*36, ((wd >> 5) & 7)*36,
                      ((wd >> 9) & 7)*36) for wd in pal])
        p += 32
    return dict(flags=flags, w=w, h=h, npal=npal, pals=pals, after_pals=p)

def decompress_frame(b, o):
    hd = _read_header(b, o)
    w, h = hd['w'], hd['h']; cells = w*h
    p = hd['after_pals']
    n = ((cells*2 + 31) >> 5) << 2
    if hd['flags'] & 3:
        palsel_bytes = b[p:p+n]; p += n
    else:
        palsel_bytes = bytes(n)              # synthesize zero stream
    L1, = struct.unpack('>I', b[p:p+4]); p += 4
    d32 = b[p:p+L1]; p += L1
    L2, = struct.unpack('>I', b[p:p+4]); p += 4
    d16 = b[p:p+L2]; p += L2
    codes, p = _bits2(b, p, cells)
    out = bytearray()
    for c in codes:
        if c == 0:
            out += b'\x00'*32                # placeholder; renderer skips
        elif c == 1:
            out += b[p:p+32]; p += 32
        elif c == 2:                          # VQ-32 (4:1)
            for _ in range(4):
                i0 = b[p]*4; i1 = b[p+1]*4; p += 2
                a = d32[i0:i0+4]; c2 = d32[i1:i1+4]
                out += bytes([a[0], a[1], c2[0], c2[1],
                              a[2], a[3], c2[2], c2[3]])
        else:                                 # VQ-16 (2:1) byte-plane
            for _ in range(4):
                hiacc = bytearray(); loacc = bytearray()
                for _k in range(4):
                    i = b[p]*2; p += 1
                    e = d16[i:i+2]
                    hiacc.append(e[0]); loacc.append(e[1])
                out += bytes(hiacc) + bytes(loacc)
    flat = b[o:hd['after_pals']] + palsel_bytes + bytes(out)
    return flat, codes

def render_flat(flat, prev, codes):
    hd = _read_header(flat, 0)
    w, h = hd['w'], hd['h']; cells = w*h
    palsel, p = _bits2(flat, hd['after_pals'], cells)
    W, H = w*8, h*8
    img = bytearray(prev) if prev else bytearray(W*H*3)
    for k in range(cells):
        if codes and codes[k] == 0 and prev:
            continue
        tile = flat[p+k*32:p+k*32+32]
        pal = hd['pals'][min(palsel[k], hd['npal']-1)]
        cy, cx = divmod(k, w)
        bx, by = cx*8, cy*8
        for i, bb in enumerate(tile):
            yy, xx = divmod(i*2, 8)
            for dx, v in ((0, bb >> 4), (1, bb & 0xF)):
                r, g, bl = pal[v]
                off = ((by+yy)*W + bx+xx+dx)*3
                img[off] = r; img[off+1] = g; img[off+2] = bl
    return bytes(img), W, H

# ---------------------------------------------------------------------
AUDIO_RATE = 16276      # RF5C164-derived (12.5 MHz/384, FD=0x800)

def decode_audio(b, film):
    """Interleaved audio: STAB t1 == 0xFFFFFFFF entries, 8-bit
    sign-magnitude PCM -> unsigned 8-bit WAV bytes."""
    pcm = bytearray()
    for s in film['samples']:
        if s['video'] or s['t1'] != 0xFFFFFFFF:
            continue
        for by in b[s['pos']:s['pos']+s['length']]:
            if by == 0xFF:
                by = 0
            pcm.append((0x80 - (by & 0x7F)) if by & 0x80
                       else (0x80 + (by & 0x7F)))
    if not pcm:
        return None
    hdr = (b'RIFF' + struct.pack('<I', len(pcm)+36) + b'WAVEfmt ' +
           struct.pack('<IHHIIHH', 16, 1, 1, AUDIO_RATE, AUDIO_RATE, 1, 8) +
           b'data' + struct.pack('<I', len(pcm)))
    return hdr + bytes(pcm)

class AudioOut:
    """Best-effort audio via the platform's native player.
    winsound on Windows; afplay (macOS) / aplay|paplay (Linux)."""
    def __init__(self, wav):
        self.wav = wav; self.proc = None; self.tmp = None
        self.mode = None
        if wav is None:
            return
        if sys.platform.startswith('win'):
            self.mode = 'winsound'
        else:
            for cand in (['afplay'], ['aplay', '-q'], ['paplay']):
                try:
                    subprocess.run([cand[0], '--version'],
                                   capture_output=True)
                    self.mode = cand; break
                except FileNotFoundError:
                    continue
        if self.mode:
            self.tmp = tempfile.NamedTemporaryFile(suffix='.wav',
                                                   delete=False)
            self.tmp.write(wav); self.tmp.close()
            atexit.register(self.stop)
            atexit.register(lambda: os.unlink(self.tmp.name))
    def play(self, offset_s=0.0):
        if not self.mode: return
        self.stop()
        data = self.wav
        if offset_s > 0:
            cut = 44 + int(offset_s*AUDIO_RATE)
            if cut >= len(self.wav): return
            body = self.wav[cut:]
            data = (self.wav[:4] + struct.pack('<I', len(body)+36) +
                    self.wav[8:40] + struct.pack('<I', len(body)) + body)
        open(self.tmp.name, 'wb').write(data)
        if self.mode == 'winsound':
            import winsound
            winsound.PlaySound(self.tmp.name,
                               winsound.SND_FILENAME | winsound.SND_ASYNC)
        else:
            self.proc = subprocess.Popen(self.mode + [self.tmp.name],
                                         stdout=subprocess.DEVNULL,
                                         stderr=subprocess.DEVNULL)
    def stop(self):
        if self.mode == 'winsound':
            import winsound
            winsound.PlaySound(None, winsound.SND_PURGE)
        elif self.proc:
            self.proc.terminate(); self.proc = None

def decode_all(b, film):
    # Skip (code 00) references the tile still in the SAME output
    # buffer, i.e. frame N-2 under the hardware double-buffering
    # (Jurassic Park's retail streams never emit code 00 -- all
    # frames are literal/VQ -- but other titles may).
    prev1 = prev2 = None; frames = []
    for s in film['samples']:
        if not s['video']:
            continue
        try:
            flat, codes = decompress_frame(b, s['pos'])
        except Exception:
            continue
        img, W, H = render_flat(flat, prev2 if prev2 else prev1, codes)
        prev2 = prev1; prev1 = img
        dur = s['t2'] * 1000 // film['base']
        frames.append((img, W, H, max(dur, 40)))
    return frames

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('file'); ap.add_argument('--scale', type=int, default=2)
    ap.add_argument('--info', action='store_true')
    ap.add_argument('--check', action='store_true')
    ap.add_argument('--mute', action='store_true')
    a = ap.parse_args()
    b = open(a.file, 'rb').read()
    film = parse_film(b)
    nv = sum(1 for s in film['samples'] if s['video'])
    if a.info:
        print(f"{a.file}: {film['width']}x{film['height']} px, "
              f"{nv} video / {len(film['samples'])} samples, "
              f"base {film['base']}")
        return
    frames = decode_all(b, film)
    if a.check:
        print(f'{len(frames)}/{nv} frames decoded OK')
        return
    audio = AudioOut(None if a.mute else decode_audio(b, film))
    import tkinter as tk
    root = tk.Tk(); root.title(a.file)
    st = dict(i=0, play=True, loop=True)
    W, H = frames[0][1]*a.scale, frames[0][2]*a.scale
    lbl = tk.Label(root); lbl.pack()
    def photo(fr):
        img, W0, H0, _ = fr
        ppm = b'P6 %d %d 255\n' % (W0, H0) + img
        ph = tk.PhotoImage(data=ppm, format='PPM')
        return ph.zoom(a.scale) if a.scale > 1 else ph
    def show():
        fr = frames[st['i']]
        ph = photo(fr); lbl.configure(image=ph); lbl.image = ph
        root.title(f"{a.file}  [{st['i']+1}/{len(frames)}]"
                   f"{'' if st['play'] else '  ||'}")
    def tick():
        if st['play']:
            st['i'] += 1
            if st['i'] >= len(frames):
                st['i'] = 0 if st['loop'] else len(frames)-1
                if not st['loop']:
                    st['play'] = False; audio.stop()
                elif st['play']:
                    audio.play(0.0)
            show()
        root.after(frames[st['i']][3], tick)
    def elapsed_s():
        return sum(f[3] for f in frames[:st['i']]) / 1000.0
    def key(e):
        k = e.keysym
        if k == 'space':
            st['play'] = not st['play']
            audio.play(elapsed_s()) if st['play'] else audio.stop()
        elif k == 'Right': st['i'] = min(st['i']+1, len(frames)-1)
        elif k == 'Left': st['i'] = max(st['i']-1, 0)
        elif k == 'Home':
            st['i'] = 0
            if st['play']: audio.play(0.0)
        elif k in ('l', 'L'): st['loop'] = not st['loop']
        elif k in ('Escape', 'q'): root.destroy(); return
        show()
    root.bind('<Key>', key)
    show(); audio.play(0.0); root.after(frames[0][3], tick)
    root.mainloop()

if __name__ == '__main__':
    main()
