"""Offline blue-emblem alpha extraction and character-grid generation (Pillow).
Usage: python frontend/scripts/gen-ascii-logo.py [approved blue logo on white]
No image sampling or remote dependency is required at runtime.
"""
import json
import sys
from pathlib import Path
from PIL import Image
ROOT = Path(__file__).resolve().parents[1]
source = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / 'icon/changguang_emblem_400_white.png'
image = Image.open(source).convert('RGBA')
w, h = image.size
alpha = Image.new('L', (w, h))
# White matte removal: blue chroma preserves pale antialiased boundaries and
# rejects neutral white. This is derived from the supplied pixels, not a redraw.
alpha.putdata([round(a * max(0, min(1, (b-r)/70))) for r,g,b,a in image.getdata()])
size = (round(320*w/max(w,h)), round(320*h/max(w,h)))
alpha = alpha.resize(size, Image.Resampling.LANCZOS)
mask = Image.new('RGBA', (400,400))
layer = Image.new('RGBA', size, (255,255,255,0))
layer.putalpha(alpha)
mask.paste(layer, ((400-size[0])//2, (400-size[1])//2))
mask.save(ROOT / 'public/ascii-logo-mask.png')
a = mask.getchannel('A')
cells = []
for y in range(40,360,8):
    for x in range(40,360,5):
        coverage = sum(a.crop((x,y,x+5,y+8)).getdata())/(5*8*255)
        if coverage > .035:
            cells.append([x,y,round(coverage,3)])
output = f'/** Offline-derived from {source.name}; regenerate with scripts/gen-ascii-logo.py. */\n'
output += 'export const LOGO_CELLS: readonly (readonly [number, number, number])[] = '+json.dumps(cells,separators=(',',':'))+';\n'
output += "export const LOGO_MASK_SRC = 'ascii-logo-mask.png';\nexport const LOGO_FPS = 15;\nexport const LOGO_CYCLE_MS = 4800;\n"
(ROOT / 'src/lib/asciiLogoFrames.ts').write_text(output,encoding='utf-8')
print(f'Generated exact alpha mask and {len(cells)} occupied character cells.')
