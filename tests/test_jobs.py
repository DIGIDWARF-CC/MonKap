import os
import tempfile
import unittest

import _path  # noqa: F401
from PIL import Image

from monkap.core import captions, jobs, ledger
from monkap.core.naming import DEFAULT_TEMPLATE


def make_png(path, size=(300, 200)):
    Image.new("RGB", size, (10, 120, 200)).save(path)


class KaptureJobTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.d = self.tmp.name
        self.src = os.path.join(self.d, "page.png")
        make_png(self.src)

    def tearDown(self):
        self.tmp.cleanup()

    def run_kapture(self, mode=captions.MODE_COPY, parent="engine, a belt", trigger="t", provenance=True):
        return jobs.kapture(self.src, (10, 20, 110, 120), parent, trigger, DEFAULT_TEMPLATE, mode, provenance)

    def test_creates_file_caption_ledger_and_provenance(self):
        res = self.run_kapture()
        self.assertEqual(os.path.basename(res.path), "page_crop_001.png")
        self.assertEqual(res.size, (100, 100))
        self.assertEqual(captions.read_caption(res.path), "engine, a belt")
        self.assertEqual(ledger.history_stems(self.d)[0], "page_crop_001")
        row = ledger.read_provenance(self.d)[0]
        self.assertEqual((row["file"], row["source"], row["box"]), ("page_crop_001.png", "page.png", [10, 20, 110, 120]))
        self.assertEqual(row["caption_sha1"], captions.text_digest("engine, a belt"))
        self.assertEqual(res.notes, [])

    def test_modes(self):
        empty = self.run_kapture(captions.MODE_EMPTY)
        self.assertIsNone(captions.read_caption(empty.path))
        self.assertEqual(empty.entry["caption_mode"], captions.MODE_EMPTY)
        trig = self.run_kapture(captions.MODE_TRIGGER, parent="a belt", trigger="engine_timing")
        self.assertEqual(captions.read_caption(trig.path), "engine_timing, a belt")

    def test_parent_without_caption_says_so(self):
        res = self.run_kapture(captions.MODE_COPY, parent="")
        self.assertIsNone(captions.read_caption(res.path))
        self.assertTrue(any("no caption to copy" in n for n in res.notes))

    def test_provenance_off_still_keeps_the_ledger(self):
        res = self.run_kapture(provenance=False)
        self.assertEqual(ledger.read_provenance(self.d), [])
        self.assertIn("page_crop_001", ledger.history_stems(self.d))
        os.remove(res.path)                                     # deleting the crop does not free its number
        self.assertEqual(os.path.basename(self.run_kapture(provenance=False).path), "page_crop_002.png")

    def test_a_failing_crop_raises_and_records_nothing(self):
        with self.assertRaises(ValueError):
            jobs.kapture(self.src, (0, 0, 9999, 10), "x", "", DEFAULT_TEMPLATE, captions.MODE_COPY, True)
        self.assertEqual(ledger.history_stems(self.d), [])
        self.assertEqual(sorted(os.listdir(self.d)), ["page.png"])


class DeleteJobTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.d = self.tmp.name
        self.img = os.path.join(self.d, "a.png")
        make_png(self.img)
        with open(os.path.join(self.d, "a.txt"), "w", encoding="utf-8") as f:
            f.write("caption")

    def tearDown(self):
        self.tmp.cleanup()

    def test_image_and_caption_go(self):
        res = jobs.delete_permanently(self.img, "")
        self.assertEqual(os.listdir(self.d), [])
        self.assertEqual((res.purged, res.notes), (0, []))

    def test_cache_is_purged_for_exactly_that_image(self):
        cache = os.path.join(self.d, "cache")
        os.makedirs(cache)
        for n in ("a_0300x0200_krea2.safetensors", "a_crop_001_0100x0100_krea2.safetensors"):
            open(os.path.join(cache, n), "wb").close()
        res = jobs.delete_permanently(self.img, cache)
        self.assertEqual(res.purged, 1)
        self.assertEqual(os.listdir(cache), ["a_crop_001_0100x0100_krea2.safetensors"])

    def test_missing_image_and_caption_are_tolerated(self):
        os.remove(self.img)
        os.remove(os.path.join(self.d, "a.txt"))
        res = jobs.delete_permanently(self.img, "")
        self.assertEqual(res.notes, [])

    def test_a_locked_image_raises_and_the_caption_stays(self):
        real_remove = os.remove

        def fake_remove(path, *a, **k):
            if path == self.img:
                raise PermissionError("locked")
            return real_remove(path, *a, **k)
        os.remove = fake_remove
        orig_sleep = jobs.time.sleep
        jobs.time.sleep = lambda s: None
        try:
            with self.assertRaises(PermissionError):
                jobs.delete_permanently(self.img, "")
        finally:
            os.remove = real_remove
            jobs.time.sleep = orig_sleep
        self.assertTrue(os.path.exists(os.path.join(self.d, "a.txt")))


class CaptionExtensionTests(unittest.TestCase):
    """Fizgig's Caption Extension setting: every caption file follows it."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.d = self.tmp.name
        self.src = os.path.join(self.d, "page.png")
        make_png(self.src)

    def tearDown(self):
        self.tmp.cleanup()

    def test_kapture_writes_the_configured_extension(self):
        res = jobs.kapture(self.src, (0, 0, 40, 40), "a belt", "", DEFAULT_TEMPLATE, captions.MODE_COPY, False,
                           ext=".caption")
        self.assertEqual(res.caption, "a belt")
        self.assertTrue(os.path.isfile(os.path.splitext(res.path)[0] + ".caption"))
        self.assertFalse(os.path.isfile(os.path.splitext(res.path)[0] + ".txt"))

    def test_delete_removes_the_configured_extension_and_a_stray_txt(self):
        for e in (".caption", ".txt"):
            with open(os.path.join(self.d, "page" + e), "w", encoding="utf-8") as f:
                f.write("x")
        jobs.delete_permanently(self.src, "", ".caption")
        self.assertEqual(os.listdir(self.d), [])


if __name__ == "__main__":
    unittest.main()
