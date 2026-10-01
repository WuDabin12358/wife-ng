import os
from PIL import ImageFont

def load_font(size):
    configured = os.environ.get("WIFE_NG_PREVIEW_FONT")
    if configured:
        return ImageFont.truetype(configured, size)
    for name in ("C:/Windows/Fonts/msyh.ttc", "DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            pass
    return ImageFont.load_default(size=size)
