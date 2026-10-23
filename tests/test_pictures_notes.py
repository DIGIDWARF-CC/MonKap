"""Who wrote a caption (the model or the user), what the list says of it, which verdicts a changed caption takes away, the
unload route, and the housekeeping of a dataset folder."""
import json
import os
import tempfile
import threading
import time
import unittest
from http.server import ThreadingHTTPServer

import _path  # noqa: F401
from PIL import Image
from test_api import ApiCase, call_get, call_post
from test_lmstudio import MODELS, Fake
from test_review import Case as ReviewCase

from monkap import api, maintenance
from monkap.core import captions, journal

import copy


def rows(ds, ext=".txt"):
    return {r[0]: r for r in api.list_items(ds, ext)}


class JournalTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.d = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def test_a_caption_is_the_models_until_its_text_changes(self):
        journal.mark_ai(self.d, "a", "A dog on a lawn.", "vis")
        note = journal.entry(self.d, "a")
        self.assertEqual((journal.classify("A dog on a lawn.", note), journal.classify("  A dog on a lawn.\n", note)), ("ai", "ai"))
        self.assertEqual(journal.classify("A dog on a lawn!", note), "manual")
        self.assertEqual((journal.classify("", note), journal.classify(None, note), journal.classify("x", None)), ("none", "none", "manual"))

    def test_a_mechanical_change_keeps_the_class_and_a_typed_one_does_not(self):
        journal.mark_ai(self.d, "a", "a dog", "vis")
        journal.keep_class(self.d, "a", "a dog", "ohwx, a dog")
        self.assertEqual(journal.classify("ohwx, a dog", journal.entry(self.d, "a")), "ai")
        journal.keep_class(self.d, "a", "something else", "other")                     # the text was not the model's: nothing to keep
        self.assertEqual(journal.classify("other", journal.entry(self.d, "a")), "manual")

    def test_an_undo_puts_the_note_back_and_none_removes_it(self):
        journal.mark_ai(self.d, "a", "x", "vis")
        before = journal.entry(self.d, "a")
        journal.mark_ai(self.d, "a", "y", "vis")
        journal.set_entry(self.d, "a", before)
        self.assertEqual(journal.classify("x", journal.entry(self.d, "a")), "ai")
        journal.set_entry(self.d, "a", None)
        self.assertIsNone(journal.entry(self.d, "a"))

    def test_the_enhanced_flag_lives_beside_the_model_note_and_both_go_with_forget(self):
        journal.mark_ai(self.d, "a", "x", "vis")
        journal.mark_enhanced(self.d, "a")
        journal.drop_ai(self.d, ["a"])
        self.assertEqual(journal.entry(self.d, "a"), {"enhanced": True})
        journal.mark_enhanced(self.d, "a", False)
        self.assertIsNone(journal.entry(self.d, "a"))

    def test_nothing_to_note_leaves_no_file_and_no_folder(self):
        journal.forget(self.d, "a")
        journal.mark_enhanced(self.d, "a", False)
        journal.drop_ai(self.d)
        journal.set_entry(self.d, "a", None)
        self.assertEqual(os.listdir(self.d), [])

    def test_a_broken_file_is_read_as_nothing(self):
        os.makedirs(os.path.join(self.d, ".monkap"))
        with open(journal.path_of(self.d), "w", encoding="utf-8") as f:
            f.write("{not json")
        self.assertEqual(journal.load(self.d), {})


class CaptionClassTests(ApiCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), Fake)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.url = f"http://127.0.0.1:{cls.srv.server_address[1]}"

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def setUp(self):
        super().setUp()
        Fake.models, Fake.own_api = copy.deepcopy(MODELS), True
        Fake.unloaded.clear()
        api.save_settings({"lm_url": self.url, "lm_model": "vis"})

    def klass(self, name):
        return rows(self.ds)[name][2]

    def test_a_caption_you_wrote_is_manual_and_the_models_is_ai_until_you_touch_it(self):
        self.assertEqual((self.klass("img_2.png"), self.klass("img_1.jpg")), ("manual", "none"))
        r = call_post("/api/recaption", {"folder": self.ds, "name": "img_1.jpg"}).data
        self.assertEqual((r["class"], r["caption"]), ("ai", "A dog on a lawn."))
        self.assertEqual(self.klass("img_1.jpg"), "ai")
        h = call_post("/api/caption", {"folder": self.ds, "name": "img_1.jpg", "text": "A dog on a lawn, in rain.", "loaded": r["caption"]}).data
        self.assertEqual((h["class"], self.klass("img_1.jpg")), ("manual", "manual"))

    def test_the_trigger_words_change_keeps_the_models_caption_the_models(self):
        call_post("/api/folder", {"folder": self.ds, "trigger": "ohwx"})
        r = call_post("/api/recaption", {"folder": self.ds, "name": "img_1.jpg"}).data
        self.assertTrue(r["caption"].startswith("ohwx, "))               # the model's caption comes with the word already
        r["caption"] = r["caption"][len("ohwx, "):]
        call_post("/api/caption", {"folder": self.ds, "name": "img_1.jpg", "text": r["caption"], "loaded": "ohwx, " + r["caption"]})
        journal.mark_ai(self.ds, "img_1", r["caption"], "vis")           # (set up: the model's caption without the word)
        h = call_post("/api/caption", {"folder": self.ds, "name": "img_1.jpg", "text": "ohwx, " + r["caption"],
                                       "loaded": r["caption"], "mechanical": True}).data
        self.assertEqual(h["class"], "ai")
        h = call_post("/api/caption", {"folder": self.ds, "name": "img_1.jpg", "text": "ohwx, a cat", "loaded": "ohwx, " + r["caption"],
                                       "mechanical": True}).data
        self.assertEqual(h["class"], "manual")                       # a mechanical flag on a typed text changes nothing

    def test_an_undo_restores_the_class_it_had(self):
        r = call_post("/api/recaption", {"folder": self.ds, "name": "img_2.png"}).data          # was manual (hand-written)
        self.assertEqual((r["class"], r["note_before"]), ("ai", None))
        h = call_post("/api/caption", {"folder": self.ds, "name": "img_2.png", "text": r["before"], "loaded": r["caption"],
                                       "restore": r["note_before"]}).data
        self.assertEqual((h["class"], self.klass("img_2.png")), ("manual", "manual"))
        again = call_post("/api/recaption", {"folder": self.ds, "name": "img_2.png"}).data
        self.assertIsNotNone(call_get("/api/info", folder=self.ds, name="img_2.png").data["class"])
        h = call_post("/api/caption", {"folder": self.ds, "name": "img_2.png", "text": again["before"], "loaded": again["caption"],
                                       "restore": again["note_before"]}).data
        self.assertEqual(h["class"], "ai" if again["note_before"] else "manual")

    def test_clearing_the_caption_makes_it_none_and_a_crop_copy_is_manual(self):
        call_post("/api/recaption", {"folder": self.ds, "name": "img_1.jpg"})
        h = call_post("/api/caption", {"folder": self.ds, "name": "img_1.jpg", "text": "", "loaded": "A dog on a lawn."}).data
        self.assertEqual((h["has_caption"], h["class"]), (False, "none"))
        call_post("/api/caption", {"folder": self.ds, "name": "img_2.png", "text": "a belt", "loaded": "a belt over two pulleys"})
        r = call_post("/api/recaption", {"folder": self.ds, "name": "img_2.png"}).data
        k = call_post("/api/kapture", {"folder": self.ds, "name": "img_2.png", "box": [0, 0, 100, 100], "text": r["caption"], "trigger": ""}).data
        self.assertEqual(rows(self.ds)[k["name"]][2], "manual")      # the copy is a new file with no note: the user's

    def test_info_and_list_carry_the_class_the_time_and_the_enhanced_flag(self):
        d = call_get("/api/info", folder=self.ds, name="img_2.png").data
        self.assertEqual((d["class"], d["enhanced"], d["mtime"] > 0), ("manual", False, True))
        journal.mark_enhanced(self.ds, "img_2")
        r = rows(self.ds)["img_2.png"]
        self.assertEqual((r[1], r[2], r[4], r[3] > 0), (1, "manual", 1, True))

    def test_a_hard_delete_forgets_the_note_and_a_soft_one_keeps_it(self):
        call_post("/api/recaption", {"folder": self.ds, "name": "img_1.jpg"})
        h = call_post("/api/delete", {"folder": self.ds, "name": "img_1.jpg"}).data              # soft by default
        self.assertIsNotNone(journal.entry(self.ds, "img_1"))
        call_post("/api/undelete", {"folder": self.ds, "moved": h["moved"]})
        self.assertEqual(self.klass("img_1.jpg"), "ai")
        call_post("/api/settings", {"settings": {"delete_mode": "hard"}})
        call_post("/api/delete", {"folder": self.ds, "name": "img_1.jpg"})
        self.assertIsNone(journal.entry(self.ds, "img_1"))

    def test_unloading_goes_to_the_models_instances_and_an_unloaded_model_is_fine(self):
        self.assertEqual(call_post("/api/ai/unload", {}).data, {"unloaded": 1})
        self.assertEqual(Fake.unloaded, ["vis"])
        self.assertEqual(call_post("/api/ai/unload", {}).data, {"unloaded": 0})
        api.save_settings({"lm_model": ""})
        self.assertEqual(call_post("/api/ai/unload", {}).code, 400)
        api.save_settings({"lm_model": "vis", "lm_url": "http://127.0.0.1:9"})
        self.assertEqual(call_post("/api/ai/unload", {}).code, 400)


class VerdictHandledTests(ReviewCase):
    """The run read the captions when it started: a caption changed after that has been dealt with, and its verdict goes."""

    def setUp(self):
        super().setUp()
        api.save_settings({"fizgig_dir": self.fz})
        for name, when in (("a", 50), ("b", 500), ("c", 50)):                  # the newest run on this folder started at 200
            path = os.path.join(self.ds, name + ".txt")
            with open(path, "w", encoding="utf-8") as f:
                f.write(name)
            os.utime(path, (when, when))

    def test_a_picture_whose_caption_changed_after_the_run_loses_its_verdict_and_the_counts_follow(self):
        r = api.review(self.ds)
        self.assertEqual(sorted(r["verdicts"]), ["a.png", "c.png"])
        self.assertEqual((r["handled"], r["counts"]["stuck"], r["counts"]["easy"], r["counts"]["mid"]), (1, 0, 1, 1))

    def test_a_picture_with_no_caption_keeps_its_verdict_and_a_run_without_a_start_time_drops_nothing(self):
        os.unlink(os.path.join(self.ds, "b.txt"))
        self.assertEqual(sorted(api.review(self.ds)["verdicts"]), ["a.png", "b.png", "c.png"])
        runs = os.path.join(self.fz, "user_data", "runs.json")
        with open(runs, encoding="utf-8") as f:
            data = json.load(f)
        for run in data:
            run.pop("started_at", None)
        with open(runs, "w", encoding="utf-8") as f:
            json.dump(data, f)
        path = os.path.join(self.ds, "b.txt")
        with open(path, "w", encoding="utf-8") as f:
            f.write("b")
        self.assertEqual(sorted(api.review(self.ds)["verdicts"]), ["a.png", "b.png", "c.png"])


class MaintenanceTests(ApiCase):
    def run_action(self, action, **extra):
        return call_post("/api/maintenance", {"folder": self.ds, "action": action, **extra})

    def test_clear_all_captions_counts_first_then_clears_keeps_backups_and_can_be_undone(self):
        call_post("/api/caption", {"folder": self.ds, "name": "img_1.jpg", "text": "one", "loaded": None})
        journal.mark_ai(self.ds, "img_1", "one", "vis")
        dry = self.run_action("clear_captions", dry=True).data
        self.assertEqual(dry, {"count": 2})
        self.assertTrue(os.path.isfile(os.path.join(self.ds, "img_2.txt")))                     # a dry run touches nothing
        done = self.run_action("clear_captions").data
        self.assertEqual(done["count"], 2)
        self.assertEqual(sorted(n for n in os.listdir(self.ds) if n.endswith(".txt")), [])
        self.assertEqual(len(os.listdir(os.path.join(self.ds, ".monkap", "backups"))), 2)
        self.assertIsNone(journal.entry(self.ds, "img_1"))
        back = self.run_action("restore_captions", items=done["items"]).data
        self.assertEqual(back["count"], 2)
        self.assertEqual(captions.read_caption(os.path.join(self.ds, "img_2.png")), "a belt over two pulleys")
        self.assertEqual(rows(self.ds)["img_1.jpg"][2], "ai")                                  # its class came back too

    def test_restoring_never_overwrites_a_caption_that_appeared_meanwhile(self):
        done = self.run_action("clear_captions").data
        with open(os.path.join(self.ds, "img_2.txt"), "w", encoding="utf-8") as f:
            f.write("new")
        self.assertEqual(self.run_action("restore_captions", items=done["items"]).data["count"], 0)
        self.assertEqual(captions.read_caption(os.path.join(self.ds, "img_2.png")), "new")

    def test_empty_caption_files_go_and_the_others_stay(self):
        with open(os.path.join(self.ds, "img_1.txt"), "w", encoding="utf-8") as f:
            f.write("   \n")
        self.assertEqual(self.run_action("empty_captions", dry=True).data, {"count": 1})
        self.assertEqual(self.run_action("empty_captions").data, {"count": 1})
        self.assertFalse(os.path.exists(os.path.join(self.ds, "img_1.txt")))
        self.assertTrue(os.path.exists(os.path.join(self.ds, "img_2.txt")))

    def test_the_removed_folder_is_erased_for_good(self):
        self.assertEqual(self.run_action("empty_removed").data, {"count": 0, "bytes": 0})
        call_post("/api/delete", {"folder": self.ds, "name": "img_2.png"})
        dry = self.run_action("empty_removed", dry=True).data
        self.assertEqual(dry["count"], 2)
        self.assertTrue(dry["bytes"] > 0 and os.path.isdir(os.path.join(self.ds, "removed")))
        self.assertEqual(self.run_action("empty_removed").data["count"], 2)
        self.assertFalse(os.path.exists(os.path.join(self.ds, "removed")))

    def test_the_notes_go_but_the_backups_stay(self):
        call_post("/api/kapture", {"folder": self.ds, "name": "img_2.png", "box": [0, 0, 50, 50], "text": "t", "trigger": ""})
        journal.mark_ai(self.ds, "img_2", "x", "vis")
        call_post("/api/caption", {"folder": self.ds, "name": "img_2.png", "text": "", "loaded": "a belt over two pulleys"})
        self.assertEqual(self.run_action("clear_notes", dry=True).data, {"count": 3})
        self.assertEqual(self.run_action("clear_notes").data, {"count": 3})
        left = os.listdir(os.path.join(self.ds, ".monkap"))
        self.assertEqual(left, ["backups"])

    def test_an_unknown_action_and_a_missing_folder_are_refused(self):
        self.assertEqual(self.run_action("burn").code, 400)
        self.assertEqual(call_post("/api/maintenance", {"folder": os.path.join(self.d, "nope"), "action": "clear_captions"}).code, 404)


if __name__ == "__main__":
    unittest.main()
