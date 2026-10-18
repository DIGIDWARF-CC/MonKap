import os
import tempfile
import unittest

import _path  # noqa: F401
from PIL import Image

from monkap.core import naming
from monkap.core.cache import purge_latent_cache
from monkap.core.crops import crop_to_file
from monkap.core.ledger import (history_stems, read_provenance, record_name, record_provenance)
from monkap.core.naming import (DEFAULT_TEMPLATE, TemplateError, example_names, next_stem,
                                  normalize_template, plan_output, render_stem)


def opened(path):
    """A fully loaded copy, so the file handle is closed straight away."""
    with Image.open(path) as im:
        im.load()
        return im


def make_grid_image(path, size=(400, 300), mode="RGB"):
    """Every pixel encodes its own coordinates, so any crop can be verified exactly."""
    im = Image.new(mode, size)
    px = im.load()
    for y in range(size[1]):
        for x in range(size[0]):
            px[x, y] = ((x % 256, y % 256, (x * 7 + y * 13) % 256) if mode == "RGB"
                        else (x % 256, y % 256, 5, 255))
    im.save(path)
    return im


class NameTests(unittest.TestCase):
    def test_default_render(self):
        self.assertEqual(render_stem(DEFAULT_TEMPLATE, "comic_page_012", 1), "comic_page_012_crop_001")
        self.assertEqual(render_stem("{parent}_{n}", "image_001", 2), "image_001_2")
        self.assertEqual(render_stem("{parent}_{index:04d}", "a", 7), "a_0007")

    def test_template_without_n_gets_suffix(self):
        t, note = normalize_template("{parent}_panel")
        self.assertTrue(t.endswith("_{n:03d}"))
        self.assertTrue(note)

    def test_unknown_placeholder_rejected(self):
        with self.assertRaises(TemplateError):
            normalize_template("{parent}_{oops}")
        with self.assertRaises(TemplateError):
            normalize_template("{parent}_{n")

    def test_empty_template_defaults(self):
        self.assertEqual(normalize_template("  ")[0], DEFAULT_TEMPLATE)

    def test_traversal_and_bad_chars_neutralised(self):
        stem = render_stem("../..\\x:{parent}*?_{n}", "a|b", 1)
        for bad in '<>:"/\\|?*':
            self.assertNotIn(bad, stem)
        self.assertFalse(stem.startswith("."))

    def test_reserved_and_trailing_dots(self):
        self.assertEqual(render_stem("con", "x", 1), "_con")
        self.assertFalse(render_stem("{parent}. ", "x", 1).endswith((".", " ")))

    def test_unicode_spaces_dashes(self):
        stem = render_stem(DEFAULT_TEMPLATE, "страница 12 - кадр", 3)
        self.assertEqual(stem, "страница 12 - кадр_crop_003")

    def test_long_parent_is_shortened(self):
        stem = render_stem(DEFAULT_TEMPLATE, "x" * 400, 1)
        self.assertLessEqual(len(stem), naming.MAX_STEM_LEN + 12)
        self.assertTrue(stem.endswith("_crop_001"))

    def test_examples(self):
        self.assertEqual(example_names("{parent}_{n}"), ["image_001_1.png", "image_001_2.png"])


class IndexTests(unittest.TestCase):
    def test_one_past_highest_existing(self):
        taken = ["p", "p_crop_001", "p_crop_002", "p_crop_005", "other_crop_009", "p_crop_x"]
        self.assertEqual(next_stem(DEFAULT_TEMPLATE, "p", taken), ("p_crop_006", 6))

    def test_other_parents_ignored(self):
        self.assertEqual(next_stem(DEFAULT_TEMPLATE, "a", ["b_crop_004"]), ("a_crop_001", 1))

    def test_collision_across_extensions(self):
        # a stray p_x1.jpg (any extension) blocks index 1 even with no p_x1.png
        _stem, n = next_stem("{parent}_x{n}", "p", ["p_x1"])
        self.assertEqual(n, 2)

    def test_case_insensitive_filesystem(self):
        _stem, n = next_stem("{parent}_{n}", "p", ["P_1"])
        self.assertEqual(n, 2)

    def test_plan_output_reads_disk(self):
        with tempfile.TemporaryDirectory() as d:
            for name in ("p.png", "p_crop_001.png", "p_crop_001.txt", "p_crop_002.jpg"):
                open(os.path.join(d, name), "wb").close()
            path, n = plan_output(d, DEFAULT_TEMPLATE, "p")
            self.assertEqual(os.path.basename(path), "p_crop_003.png")
            self.assertEqual(n, 3)


class CropTests(unittest.TestCase):
    def test_native_pixels_exact(self):
        with tempfile.TemporaryDirectory() as d:
            src = os.path.join(d, "src.png")
            im = make_grid_image(src)
            dest = os.path.join(d, "out.png")
            box = (13, 29, 213, 129)
            self.assertEqual(crop_to_file(src, box, dest), (200, 100))
            out = opened(dest)
            self.assertEqual(out.size, (200, 100))
            self.assertEqual(out.tobytes(), im.crop(box).tobytes())

    def test_square_crop_keeps_dimensions(self):
        with tempfile.TemporaryDirectory() as d:
            src = os.path.join(d, "s.png")
            make_grid_image(src, (500, 500))
            dest = os.path.join(d, "o.png")
            crop_to_file(src, (0, 0, 320, 320), dest)
            self.assertEqual(opened(dest).size, (320, 320))

    def test_source_untouched(self):
        with tempfile.TemporaryDirectory() as d:
            src = os.path.join(d, "s.png")
            make_grid_image(src)
            with open(src, "rb") as f:
                before = (os.path.getsize(src), os.path.getmtime(src), f.read())
            crop_to_file(src, (1, 1, 50, 50), os.path.join(d, "o.png"))
            with open(src, "rb") as f:
                after = (os.path.getsize(src), os.path.getmtime(src), f.read())
            self.assertEqual(before, after)

    def test_never_overwrites(self):
        with tempfile.TemporaryDirectory() as d:
            src = os.path.join(d, "s.png")
            make_grid_image(src)
            dest = os.path.join(d, "o.png")
            with open(dest, "wb") as f:
                f.write(b"precious")
            with self.assertRaises(FileExistsError):
                crop_to_file(src, (0, 0, 10, 10), dest)
            with open(dest, "rb") as f:
                self.assertEqual(f.read(), b"precious")
            self.assertEqual([f for f in os.listdir(d) if f.endswith(".part")], [])

    def test_bad_boxes(self):
        with tempfile.TemporaryDirectory() as d:
            src = os.path.join(d, "s.png")
            make_grid_image(src, (100, 100))
            dest = os.path.join(d, "o.png")
            for box in ((10, 10, 10, 50), (0, 0, 101, 50), (-1, 0, 50, 50)):
                with self.assertRaises(ValueError):
                    crop_to_file(src, box, dest)
            self.assertFalse(os.path.exists(dest))

    def test_corrupt_source_leaves_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            src = os.path.join(d, "bad.png")
            with open(src, "wb") as f:
                f.write(b"not an image at all")
            dest = os.path.join(d, "o.png")
            with self.assertRaises(Exception):
                crop_to_file(src, (0, 0, 5, 5), dest)
            self.assertEqual(sorted(os.listdir(d)), ["bad.png"])

    def test_missing_output_folder(self):
        with tempfile.TemporaryDirectory() as d:
            src = os.path.join(d, "s.png")
            make_grid_image(src, (50, 50))
            with self.assertRaises(OSError):
                crop_to_file(src, (0, 0, 10, 10), os.path.join(d, "nope", "o.png"))

    def test_alpha_palette_and_cmyk(self):
        with tempfile.TemporaryDirectory() as d:
            rgba = os.path.join(d, "a.png")
            make_grid_image(rgba, (60, 60), "RGBA")
            out = os.path.join(d, "a_out.png")
            crop_to_file(rgba, (5, 5, 25, 25), out)
            self.assertEqual(opened(out).mode, "RGBA")
            pal = os.path.join(d, "p.png")
            Image.new("P", (40, 40)).save(pal)
            out2 = os.path.join(d, "p_out.png")
            crop_to_file(pal, (0, 0, 10, 10), out2)
            self.assertIn(opened(out2).mode, ("RGB", "RGBA", "P", "L"))
            jpg = os.path.join(d, "j.jpg")
            Image.new("CMYK", (40, 40)).save(jpg)
            out3 = os.path.join(d, "j_out.png")
            crop_to_file(jpg, (0, 0, 10, 10), out3)
            self.assertEqual(opened(out3).mode, "RGB")

    def test_series_of_four_distinct_files(self):
        with tempfile.TemporaryDirectory() as d:
            src = os.path.join(d, "comic_page_012.png")
            make_grid_image(src, (400, 400))
            boxes = [(0, 0, 200, 200), (200, 0, 400, 200), (0, 200, 200, 400), (200, 200, 400, 400)]
            names = []
            for box in boxes:
                path, _n = plan_output(d, DEFAULT_TEMPLATE, "comic_page_012")
                crop_to_file(src, box, path)
                names.append(os.path.basename(path))
            self.assertEqual(names, [f"comic_page_012_crop_00{i}.png" for i in range(1, 5)])


class ProvenanceTests(unittest.TestCase):
    def test_append_and_read_unicode(self):
        with tempfile.TemporaryDirectory() as d:
            record_provenance(d, {"file": "кадр_crop_001.png", "source": "кадр.png", "box": [1, 2, 3, 4]})
            record_provenance(d, {"file": "b.png", "source": "a.png", "box": [0, 0, 5, 5]})
            rows = read_provenance(d)
            self.assertEqual([r["file"] for r in rows], ["кадр_crop_001.png", "b.png"])
            self.assertIn("time", rows[0])

    def test_sidecar_is_in_a_subfolder(self):
        with tempfile.TemporaryDirectory() as d:
            record_provenance(d, {"file": "x"})
            self.assertEqual([f for f in os.listdir(d) if os.path.isfile(os.path.join(d, f))], [])


class LedgerTests(unittest.TestCase):
    def test_deleted_numbers_are_not_reused(self):
        taken = ["p", "p_crop_001", "p_crop_002"]              # 003 was deleted from disk
        self.assertEqual(next_stem(DEFAULT_TEMPLATE, "p", taken, ["p_crop_003"]), ("p_crop_004", 4))
        self.assertEqual(next_stem(DEFAULT_TEMPLATE, "p", taken), ("p_crop_003", 3))   # without history

    def test_ledger_roundtrip_and_plan_output(self):
        with tempfile.TemporaryDirectory() as d:
            for n in (1, 2, 3):
                record_name(d, f"p_crop_{n:03d}")
            open(os.path.join(d, "p.png"), "wb").close()      # none of the crops exist any more
            self.assertEqual(history_stems(d), ["p_crop_001", "p_crop_002", "p_crop_003"])
            path, n = plan_output(d, DEFAULT_TEMPLATE, "p")
            self.assertEqual((os.path.basename(path), n), ("p_crop_004.png", 4))

    def test_old_provenance_counts_as_history(self):
        with tempfile.TemporaryDirectory() as d:
            record_provenance(d, {"file": "p_crop_007.png", "source": "p.png", "box": [0, 0, 1, 1]})
            self.assertIn("p_crop_007", history_stems(d))
            self.assertEqual(plan_output(d, DEFAULT_TEMPLATE, "p")[1], 8)

    def test_ledger_lives_in_the_hidden_subfolder(self):
        with tempfile.TemporaryDirectory() as d:
            record_name(d, "x_crop_001")
            self.assertEqual([f for f in os.listdir(d) if os.path.isfile(os.path.join(d, f))], [])


class CachePurgeTests(unittest.TestCase):
    def test_only_the_exact_stems_latents_go(self):
        with tempfile.TemporaryDirectory() as d:
            mine = ["img_1_0640x0480_krea2.safetensors", "IMG_1_0512x0384_qwen_image_2.1.safetensors"]
            keep = ["img_1_crop_001_0320x0320_krea2.safetensors", "img_10_0640x0480_krea2.safetensors",
                    "img_1_krea2_te.safetensors", "img_1x_0640x0480_krea2.safetensors", "notes.txt"]
            for n in mine + keep:
                open(os.path.join(d, n), "wb").close()
            self.assertEqual(purge_latent_cache(d, "img_1"), 2)
            self.assertEqual(sorted(os.listdir(d)), sorted(keep))

    def test_special_characters_in_the_stem_are_literal(self):
        with tempfile.TemporaryDirectory() as d:
            open(os.path.join(d, "a.b(1)_0100x0100_x.safetensors"), "wb").close()
            open(os.path.join(d, "aXb(1)_0100x0100_x.safetensors"), "wb").close()
            self.assertEqual(purge_latent_cache(d, "a.b(1)"), 1)
            self.assertEqual(os.listdir(d), ["aXb(1)_0100x0100_x.safetensors"])

    def test_missing_or_empty_cache_dir_is_harmless(self):
        self.assertEqual(purge_latent_cache("", "x"), 0)
        self.assertEqual(purge_latent_cache(os.path.join(tempfile.gettempdir(), "no_such_dir_monkap"), "x"), 0)


if __name__ == "__main__":
    unittest.main()
