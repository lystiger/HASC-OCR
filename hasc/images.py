"""Sheet photo helpers shared by the reader and the website: turn a photo upright, make a display copy.

Form A sheets are landscape, and October's photos of them were taken portrait with the top of the
page on the right. Sent sideways, the model paired weights with the wrong roll codes and scored
73% on the hidden set; turned upright it scored 97%. Back pages are not always turned the same way.
"""
import io

from PIL import Image, ImageOps


def upright(jpeg, turn=0):
    """Portrait photos turn to landscape; then `turn` more degrees counter-clockwise (the stored photo turn)."""
    image = ImageOps.exif_transpose(Image.open(io.BytesIO(jpeg)))
    if image.height > image.width:
        image = image.rotate(90, expand=True)
    elif not turn:
        return jpeg
    if turn:
        image = image.rotate(turn, expand=True)
    out = io.BytesIO()
    image.save(out, "JPEG", quality=92, subsampling=0)  # full colour keeps blue ink sharp
    return out.getvalue()


DISPLAY_LONG_SIDE = 1600  # about 0.35 MB instead of ~3 MB for a phone photo; enough to read the sheet on screen


def display_copy(jpeg, turn=0):
    """The upright photo shrunk for the office pages. The original is kept unchanged elsewhere."""
    image = Image.open(io.BytesIO(upright(jpeg, turn)))
    image.thumbnail((DISPLAY_LONG_SIDE, DISPLAY_LONG_SIDE))
    out = io.BytesIO()
    image.convert("RGB").save(out, "JPEG", quality=85)
    return out.getvalue()
