"""What the enhance results do to the files: Store as Kapture (a new picture) and Replace (the original kept, with Undo)."""
import io
import os
import tempfile
import unittest

import _path  # noqa: F401
from PIL import Image

from monkap.core import captions, jobs, ledger
from monkap.core.naming import DEFAULT_TEMPLATE


def read(path) -> bytes:
    with open(path, "rb") as f:
        return f.read()


def png_bytes(size=(120, 80), color=(200, 30, 30), mode="RGB") -> bytes:
    buf = io.BytesIO()
    Image.new(mode, size, color).save(buf, format="PNG")
    return buf.getvalue()


def jpg_bytes(size=(100, 60)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, (30, 30, 200)).save(buf, format="JPEG")
    return buf.getvalue()


class Case(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.d = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def picture(self, name="shot.png", data=None):
        path = os.path.join(self.d, name)
        with open(path, "wb") as f:
            f.write(data if data is not None else png_bytes((60, 40), (10, 10, 10)))
        return path

    def size(self, name):
        with Image.open(os.path.join(self.d, name)) as im:
            return im.size


class StoreEnhancedTests(Case):
    def test_a_new_uniquely_named_png_is_made_and_the_original_is_not_touched(self):
        src = self.picture("shot.png")
        before = read(src)
        res = jobs.store_enhanced(src, png_bytes((120, 80)), DEFAULT_TEMPLATE, captions.MODE_COPY, True, ".txt", "a belt", "t")
        self.assertEqual(os.path.basename(res.path), "shot_crop_001.png")
        self.assertEqual(self.size("shot_crop_001.png"), (120, 80))
        self.assertEqual(read(src), before)
        self.assertEqual(res.caption, "a belt")
        self.assertEqual(captions.read_caption(res.path), "a belt")
        self.assertIn("shot_crop_001", ledger.history_stems(self.d))
        row = ledger.read_provenance(self.d)[-1]
        self.assertEqual((row["file"], row["source"], row["enhanced"]), ("shot_crop_001.png", "shot.png", True))

    def test_the_next_name_is_never_the_same(self):
        src = self.picture("shot.png")
        names = [os.path.basename(jobs.store_enhanced(src, png_bytes(), DEFAULT_TEMPLATE, captions.MODE_EMPTY, False, ".txt", "", "").path)
                 for _ in range(2)]
        self.assertEqual(names, ["shot_crop_001.png", "shot_crop_002.png"])

    def test_a_jpeg_result_is_stored_as_png_and_junk_is_refused_without_leaving_files(self):
        src = self.picture("shot.png")
        res = jobs.store_enhanced(src, jpg_bytes(), DEFAULT_TEMPLATE, captions.MODE_EMPTY, False, ".txt", "", "")
        self.assertTrue(res.path.endswith(".png") and self.size(os.path.basename(res.path)) == (100, 60))
        with self.assertRaisesRegex(ValueError, "can't be read"):
            jobs.store_enhanced(src, b"not a picture", DEFAULT_TEMPLATE, captions.MODE_EMPTY, False, ".txt", "", "")
        self.assertEqual(sorted(n for n in os.listdir(self.d) if not n.startswith(".")), ["shot.png", "shot_crop_001.png"])

    def test_a_picture_with_transparency_keeps_it(self):
        src = self.picture("shot.png")
        res = jobs.store_enhanced(src, png_bytes((20, 20), (255, 0, 0, 90), "RGBA"), DEFAULT_TEMPLATE, captions.MODE_EMPTY,
                                  False, ".txt", "", "")
        with Image.open(res.path) as im:
            self.assertEqual(im.mode, "RGBA")


class ReplaceTests(Case):
    def test_the_original_goes_to_the_backup_the_new_one_takes_its_name_and_the_caption_stays(self):
        src = self.picture("shot.png")
        captions.write_new_caption(src, "a belt")
        old = read(src)
        res = jobs.replace_picture(src, png_bytes((120, 80)))
        self.assertEqual((res["name"], res["old"]), ("shot.png", "shot.png"))
        self.assertEqual(self.size("shot.png"), (120, 80))
        self.assertEqual(captions.read_caption(os.path.join(self.d, "shot.png")), "a belt")
        kept = os.path.join(self.d, ".monkap", "replaced", res["backup"])
        self.assertEqual(read(kept), old)

    def test_a_jpg_original_becomes_a_png_of_the_same_name_and_comes_back_on_undo(self):
        src = self.picture("photo.jpg", jpg_bytes((90, 50)))
        captions.write_new_caption(src, "a face")
        res = jobs.replace_picture(src, png_bytes((180, 100)))
        self.assertEqual((res["name"], res["old"]), ("photo.png", "photo.jpg"))
        self.assertFalse(os.path.exists(src))
        self.assertEqual(captions.read_caption(os.path.join(self.d, "photo.png")), "a face")
        back = jobs.revert_replace(self.d, res["name"], res["backup"], res["old"])
        self.assertEqual((back, self.size("photo.jpg")), ("photo.jpg", (90, 50)))
        self.assertFalse(os.path.exists(os.path.join(self.d, "photo.png")))
        self.assertEqual(captions.read_caption(os.path.join(self.d, "photo.jpg")), "a face")

    def test_a_png_over_a_png_comes_back_on_undo(self):
        src = self.picture("shot.png")
        res = jobs.replace_picture(src, png_bytes((120, 80)))
        jobs.revert_replace(self.d, res["name"], res["backup"], res["old"])
        self.assertEqual(self.size("shot.png"), (60, 40))

    def test_an_existing_png_of_that_name_blocks_the_replace_and_nothing_changes(self):
        src = self.picture("photo.jpg", jpg_bytes())
        self.picture("photo.png")
        with self.assertRaisesRegex(ValueError, "Store as Kapture"):
            jobs.replace_picture(src, png_bytes())
        self.assertTrue(os.path.exists(src))
        self.assertFalse(os.path.exists(os.path.join(self.d, ".monkap", "replaced")) and os.listdir(os.path.join(self.d, ".monkap", "replaced")))

    def test_a_bad_result_leaves_the_original_alone(self):
        src = self.picture("shot.png")
        before = read(src)
        with self.assertRaisesRegex(ValueError, "can't be read"):
            jobs.replace_picture(src, b"junk")
        self.assertEqual(read(src), before)
        self.assertEqual([n for n in os.listdir(self.d) if n.endswith((".new", ".part"))], [])

    def test_two_replaces_keep_both_originals(self):
        src = self.picture("shot.png")
        first = jobs.replace_picture(src, png_bytes((120, 80)))
        second = jobs.replace_picture(os.path.join(self.d, "shot.png"), png_bytes((240, 160)))
        self.assertNotEqual(first["backup"], second["backup"])
        self.assertEqual(len(os.listdir(os.path.join(self.d, ".monkap", "replaced"))), 2)

    def test_undo_refuses_when_the_original_is_gone_or_its_name_is_taken(self):
        src = self.picture("photo.jpg", jpg_bytes())
        res = jobs.replace_picture(src, png_bytes())
        self.picture("photo.jpg", jpg_bytes((10, 10)))
        with self.assertRaisesRegex(ValueError, "name is there now"):
            jobs.revert_replace(self.d, res["name"], res["backup"], res["old"])
        os.remove(os.path.join(self.d, "photo.jpg"))
        os.remove(os.path.join(self.d, ".monkap", "replaced", res["backup"]))
        with self.assertRaisesRegex(ValueError, "not in the backup"):
            jobs.revert_replace(self.d, res["name"], res["backup"], res["old"])


if __name__ == "__main__":
    unittest.main()
