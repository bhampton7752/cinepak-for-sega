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
import sys, struct, argparse

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
# ---------------------------------------------------------------------
def decompress_frame(b, o):
    flags = struct.unpack('>H', b[o+2:o+4])[0]
    w, h = struct.unpack('>HH', b[o+8:o+12])
    npal = (flags & 3) + 1
    cells = w * h
    ctrl_longs = (cells + 15) // 16
    out = bytearray(b[o:o+12])
    p = o + 12
    out += b[p:p+npal*32]; p += npal*32
    if flags & 3:
        out += b[p:p+ctrl_longs*4]; p += ctrl_longs*4
    L1, = struct.unpack('>I', b[p:p+4]); p += 4
    d32 = b[p:p+L1]; p += L1
    L2, = struct.unpack('>I', b[p:p+4]); p += 4
    d16 = b[p:p+L2]; p += L2
    ctrl = b[p:p+ctrl_longs*4]; p += ctrl_longs*4
    codes = []
    bits = 0; acc = 0
    ci = 0
    for _ in range(cells):
        if bits < 2:
            acc = (acc << 32) | struct.unpack('>I', ctrl[ci:ci+4])[0]
            ci += 4; bits += 32
        codes.append((acc >> (bits - 2)) & 3)
        bits -= 2
    tiles = bytearray()
    for c in codes:
        if c == 0:
            tiles += b'\x00' * 32          # placeholder; renderer skips
        elif c == 1:
            tiles += b[p:p+32]; p += 32
        elif c == 2:                        # VQ-32
            t = bytearray()
            for k in range(0, 8, 2):
                i1, i2 = b[p+k], b[p+k+1]
                e1 = d32[i1*4:i1*4+4]; e2 = d32[i2*4:i2*4+4]
                t += bytes([e1[0], e1[1], e2[0], e2[1],
                            e1[2], e1[3], e2[2], e2[3]])
            # word-interleave into 32B tile (two rows per pair-quad)
            tiles += bytes(t) * (32 // len(t)) if len(t) != 32 else bytes(t)
            p += 8
        else:                               # VQ-16
            hi = bytearray(); lo = bytearray()
            for k in range(16):
                e = d16[b[p+k]*2:b[p+k]*2+2]
                hi.append(e[0]); lo.append(e[1])
            t = bytearray()
            for k in range(0, 16, 2):
                t += bytes([hi[k], lo[k], hi[k+1], lo[k+1]])
            tiles += bytes(t)
            p += 16
    out += tiles
    return bytes(out), codes

def cram_rgb(word):
    return (((word >> 1) & 7) * 36, ((word >> 5) & 7) * 36,
            ((word >> 9) & 7) * 36)

def render_flat(flat, prev, codes):
    flags = struct.unpack('>H', flat[2:4])[0]
    w, h = struct.unpack('>HH', flat[8:12])
    npal = (flags & 3) + 1
    cells = w * h
    pals = []
    p = 12
    for _ in range(npal):
        pals.append([cram_rgb(struct.unpack('>H', flat[p+i*2:p+i*2+2])[0])
                     for i in range(16)])
        p += 32
    sel = [0]*cells
    if flags & 3:
        longs = (cells + 15) // 16
        bits = 0; acc = 0; ci = p
        for k in range(cells):
            if bits < 2:
                acc = (acc << 32) | struct.unpack('>I', flat[ci:ci+4])[0]
                ci += 4; bits += 32
            sel[k] = (acc >> (bits - 2)) & 3
            bits -= 2
        p += longs*4
    W, H = w*8, h*8
    img = bytearray(prev) if prev else bytearray(W*H*3)
    for k in range(cells):
        if codes and codes[k] == 0 and prev:
            continue
        tile = flat[p+k*32:p+k*32+32]
        pal = pals[min(sel[k], npal-1)]
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
                if not st['loop']: st['play'] = False
            show()
        root.after(frames[st['i']][3], tick)
    def key(e):
        k = e.keysym
        if k == 'space': st['play'] = not st['play']
        elif k == 'Right': st['i'] = min(st['i']+1, len(frames)-1)
        elif k == 'Left': st['i'] = max(st['i']-1, 0)
        elif k == 'Home': st['i'] = 0
        elif k in ('l', 'L'): st['loop'] = not st['loop']
        elif k in ('Escape', 'q'): root.destroy(); return
        show()
    root.bind('<Key>', key)
    show(); root.after(frames[0][3], tick); root.mainloop()

if __name__ == '__main__':
    main()
