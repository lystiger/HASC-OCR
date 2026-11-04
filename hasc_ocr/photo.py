"""Turn a sheet photo upright before it is read.

Form A sheets are landscape, and October's photos of them were taken portrait with the top of the
page on the right. Sent sideways, the model paired weights with the wrong roll codes and scored
73% on the hidden set; turned upright it scored 97%. Back pages are not always turned the same way.
"""
import io

from PIL import Image, ImageOps


def upright(jpeg):
    image = ImageOps.exif_transpose(Image.open(io.BytesIO(jpeg)))
    if image.height <= image.width:
        return jpeg
    out = io.BytesIO()
    image.rotate(90, expand=True).save(out, "JPEG", quality=92, subsampling=0)  # full colour keeps blue ink sharp
    return out.getvalue()
