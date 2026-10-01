"""Render our rect/text-only blueprint SVG to PNG for inspection (no game)."""
import sys
from pathlib import Path
from xml.etree import ElementTree
from PIL import Image, ImageDraw, ImageFont
from preview_font import load_font

source = Path(sys.argv[1])
svg = ElementTree.parse(source).getroot()
canvas = Image.new('RGB', (int(svg.attrib['width']), int(svg.attrib['height'])), 'white')
draw = ImageDraw.Draw(canvas)
font = load_font(16)
for item in svg.iter():
    tag = item.tag.rsplit('}', 1)[-1]
    a = item.attrib
    if tag == 'rect':
        x, y = float(a.get('x', 0)), float(a.get('y', 0))
        draw.rectangle((x, y, x+float(a['width'])-0.01, y+float(a['height'])-0.01), fill=a['fill'])
    elif tag == 'text':
        draw.text((float(a['x']), float(a['y'])-18), item.text or '', fill='#252525', font=font)
output = source.with_suffix('.png')
canvas.save(output)
print(output.resolve())
