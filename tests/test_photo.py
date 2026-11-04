import io
import unittest

from PIL import Image

from hasc_ocr import photo


def jpeg(width, height, mark_top_left=False):
    image = Image.new("RGB", (width, height), "white")
    if mark_top_left:
        image.paste((0, 0, 0), (0, 0, 10, 10))
    out = io.BytesIO()
    image.save(out, "JPEG")
    return out.getvalue()


def opened(data):
    return Image.open(io.BytesIO(data))


class Upright(unittest.TestCase):
    def test_portrait_photo_turns_left_to_landscape(self):
        image = opened(photo.upright(jpeg(100, 200, mark_top_left=True)))
        self.assertEqual(image.size, (200, 100))
        self.assertLess(image.getpixel((5, 95))[0], 50)  # the old top-left corner is now bottom-left

    def test_landscape_photo_is_sent_unchanged(self):
        data = jpeg(200, 100)
        self.assertEqual(photo.upright(data), data)
