import unittest
from PIL import Image

from app_wallpaper import DEFAULT_BACKGROUND, compose_background


class WallpaperCompositionTests(unittest.TestCase):
    def test_transparency_blends_only_the_photo_with_the_theme(self):
        source = Image.new("RGB", (4, 2), "red")
        for transparency, center in ((0, (255, 0, 0)), (50, (127, 0, 127)), (100, (0, 0, 255))):
            with self.subTest(transparency=transparency):
                result = compose_background(source, (8, 8), "blue", transparency)
                self.assertEqual(result.size, (8, 8))
                self.assertEqual(result.getpixel((4, 4)), center)
                self.assertEqual(result.getpixel((0, 0)), (0, 0, 255))
        self.assertEqual(source.size, (4, 2))

    def test_default_background_is_a_real_4k_photo(self):
        with Image.open(DEFAULT_BACKGROUND) as source:
            self.assertEqual(source.size, (3840, 2160))
            source.verify()
