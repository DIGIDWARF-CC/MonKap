import os
import tempfile
import unittest

import _path  # noqa: F401
from monkap.core.captions import (MODE_COPY, MODE_EMPTY, MODE_TRIGGER, caption_path, child_caption,
                                    text_digest,
                                    ensure_trigger_prefix, read_caption, save_caption,
                                    write_new_caption)
from monkap.core.settings import MonkapSettings


class TriggerTests(unittest.TestCase):
    def test_prefix_added_like_the_worker_does(self):
        self.assertEqual(ensure_trigger_prefix("a pony", "mlp_style"), "mlp_style, a pony")

    def test_prefix_not_duplicated(self):
        self.assertEqual(ensure_trigger_prefix("mlp_style, a pony", "mlp_style"), "mlp_style, a pony")
        self.assertEqual(ensure_trigger_prefix("MLP_STYLE a pony", "mlp_style"), "MLP_STYLE a pony")
        self.assertEqual(ensure_trigger_prefix("mlp_styled pony", "mlp_style"),
                         "mlp_style, mlp_styled pony")

    def test_empty_inputs(self):
        self.assertEqual(ensure_trigger_prefix("", "t"), "t")
        self.assertEqual(ensure_trigger_prefix("x", ""), "x")


class ChildTests(unittest.TestCase):
    def test_modes(self):
        self.assertIsNone(child_caption(MODE_EMPTY, "parent text", "t"))
        self.assertEqual(child_caption(MODE_COPY, "parent text", "t"), "parent text")
        self.assertEqual(child_caption(MODE_TRIGGER, "parent text", "t"), "t, parent text")

    def test_missing_or_blank_parent(self):
        self.assertIsNone(child_caption(MODE_COPY, None, "t"))
        self.assertIsNone(child_caption(MODE_COPY, "  \n", "t"))
        self.assertEqual(child_caption(MODE_TRIGGER, None, "t"), "t")
        self.assertIsNone(child_caption(MODE_TRIGGER, None, ""))


class FileTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.d = self.tmp.name
        self.img = os.path.join(self.d, "a.png")
        open(self.img, "wb").close()

    def tearDown(self):
        self.tmp.cleanup()

    def test_read_missing_is_none_and_bom_tolerated(self):
        self.assertIsNone(read_caption(self.img))
        with open(caption_path(self.img), "wb") as f:
            f.write("﻿привет мир".encode("utf-8"))
        self.assertEqual(read_caption(self.img), "привет мир")

    def test_write_and_unchanged(self):
        self.assertEqual(save_caption(self.img, "hello", None).kind, "written")
        self.assertEqual(read_caption(self.img), "hello")
        self.assertEqual(save_caption(self.img, "hello", "hello").kind, "unchanged")
        self.assertEqual(save_caption(self.img, "  hello \n", "hello").kind, "unchanged")

    def test_unicode_roundtrip(self):
        save_caption(self.img, "пони с «кавычками» 🐴", None)
        self.assertEqual(read_caption(self.img), "пони с «кавычками» 🐴")

    def test_clear_removes_txt_and_backs_up(self):
        save_caption(self.img, "keep me somewhere", None)
        r = save_caption(self.img, "", "keep me somewhere")
        self.assertEqual(r.kind, "cleared")
        self.assertFalse(os.path.exists(caption_path(self.img)))
        self.assertTrue(r.backup and os.path.exists(r.backup))
        with open(r.backup, encoding="utf-8") as f:
            self.assertEqual(f.read(), "keep me somewhere")

    def test_empty_with_no_file_is_a_noop(self):
        self.assertEqual(save_caption(self.img, "", None).kind, "unchanged")
        self.assertFalse(os.path.exists(caption_path(self.img)))

    def test_new_caption_refuses_overwrite(self):
        write_new_caption(self.img, "first")
        with self.assertRaises(FileExistsError):
            write_new_caption(self.img, "second")
        self.assertEqual(read_caption(self.img), "first")

    def test_child_edit_does_not_touch_parent(self):
        with open(caption_path(self.img), "w", encoding="utf-8") as f:
            f.write("parent")
        child = os.path.join(self.d, "a_crop_001.png")
        open(child, "wb").close()
        write_new_caption(child, child_caption(MODE_COPY, read_caption(self.img), ""))
        save_caption(child, "child changed", "parent")
        self.assertEqual(read_caption(self.img), "parent")
        self.assertEqual(read_caption(child), "child changed")

    def test_no_stray_part_files(self):
        save_caption(self.img, "x", None)
        self.assertEqual([f for f in os.listdir(self.d) if f.endswith(".part")], [])


class ConflictTests(unittest.TestCase):
    """The .txt changed on disk while the editor held an older text (a captioning job, another tool)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.img = os.path.join(self.tmp.name, "a.png")
        open(self.img, "wb").close()

    def tearDown(self):
        self.tmp.cleanup()

    def put(self, text):
        with open(caption_path(self.img), "w", encoding="utf-8") as f:
            f.write(text)

    def test_overwrite_after_an_outside_change_keeps_the_other_text(self):
        self.put("what the editor loaded")
        self.put("written by a captioning job")                   # behind the editor's back
        res = save_caption(self.img, "typed by the user", "what the editor loaded")
        self.assertEqual((res.kind, res.conflict), ("written", True))
        self.assertEqual(read_caption(self.img), "typed by the user")
        with open(res.backup, encoding="utf-8") as f:
            self.assertEqual(f.read(), "written by a captioning job")
        self.assertIn(os.path.join(".monkap", "backups"), res.backup)

    def test_no_conflict_when_disk_matches_what_was_loaded(self):
        self.put("same")
        res = save_caption(self.img, "edited", "same")
        self.assertEqual((res.kind, res.conflict, res.backup), ("written", False, ""))

    def test_clearing_after_an_outside_change_backs_up_the_new_text(self):
        self.put("old")
        self.put("newer, from outside")
        res = save_caption(self.img, "", "old")
        self.assertEqual((res.kind, res.conflict), ("cleared", True))
        with open(res.backup, encoding="utf-8") as f:
            self.assertEqual(f.read(), "newer, from outside")

    def test_file_deleted_outside_is_just_recreated(self):
        res = save_caption(self.img, "fresh", "was there once")
        self.assertEqual((res.kind, res.backup), ("written", ""))
        self.assertEqual(read_caption(self.img), "fresh")

    def test_unedited_text_never_overwrites_an_outside_change(self):
        self.put("loaded")
        self.put("outside")
        self.assertEqual(save_caption(self.img, "loaded", "loaded").kind, "unchanged")
        self.assertEqual(read_caption(self.img), "outside")

    def test_missing_txt_and_empty_box_are_one_state(self):
        self.assertEqual(save_caption(self.img, "", None).kind, "unchanged")
        self.assertEqual(save_caption(self.img, "  \n", None).kind, "unchanged")

    def test_digest_ignores_edge_whitespace(self):
        self.assertEqual(text_digest(" a caption \n"), text_digest("a caption"))
        self.assertNotEqual(text_digest("a caption"), text_digest("another"))


class SettingsTests(unittest.TestCase):
    def test_defaults_and_garbage(self):
        s = MonkapSettings.from_dict({"ratio": "7:5", "caption_mode": "weird", "template": "{x}",
                                      "junk": 1})
        self.assertEqual((s.ratio, s.caption_mode), ("1:1", MODE_COPY))
        self.assertIn("{parent}", s.template)
        self.assertEqual(MonkapSettings.from_dict(None), MonkapSettings())

    def test_roundtrip(self):
        s = MonkapSettings(ratio="16:9", template="{parent}_p{n}", caption_mode=MODE_TRIGGER,
                           store_provenance=False, show_thirds=False)
        self.assertEqual(MonkapSettings.from_dict(s.to_dict()), s)


if __name__ == "__main__":
    unittest.main()
