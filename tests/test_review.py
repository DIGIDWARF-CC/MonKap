"""What a Fizgig run found out about each picture, on a made-up Fizgig and run (nothing real is read)."""
import json
import os
import tempfile
import unittest

import _path  # noqa: F401
from PIL import Image

from monkap import api
from monkap.integrations import fizgig_review as fr


def write(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(data if isinstance(data, str) else json.dumps(data))


class Case(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = self.tmp.name
        self.fz, self.ds, self.out = (os.path.join(d, n) for n in ("Fizgig", "ds", "out"))
        os.makedirs(self.ds)
        for n in ("a.png", "b.png", "c.png"):
            Image.new("RGB", (20, 20)).save(os.path.join(self.ds, n))
        write(os.path.join(self.fz, "fizgig.py"), "")
        write(os.path.join(self.fz, "user_data", "prefs.json"), {})
        # b is far harder than a and c at the same noise level; every picture is seen at t=0.5 in both epochs
        lines = []
        for ep in (1, 2):
            for key, loss in (("a", 0.30), ("b", 0.50), ("c", 0.31)):
                lines.append(json.dumps({"epoch": ep, "step": len(lines) + 1, "key": key, "t": 0.5, "loss": loss}))
        lines.append("{cut short")
        lines.append(json.dumps({"epoch": 2, "step": 99, "key": "a|b", "t": 0.5, "loss": 9.0}))        # a batch mean
        write(os.path.join(self.out, "loss_log", "per_image_loss.jsonl"), "\n".join(lines) + "\n")
        write(os.path.join(self.out, "loss_log", "problem_images.json"), {
            "epoch": 2, "plateaued": True, "best_epoch_estimate": 1, "improving_count": 0,
            "images": {"a": {"verdict": "easy", "slope": 0.0}, "b": {"verdict": "stuck", "slope": 0.001, "multiplier": 1.0},
                       "c": {"verdict": "mid"}}})
        write(os.path.join(self.fz, "user_data", "runs.json"), [
            {"id": "old", "image_folder": self.ds, "output_dir": self.out, "started_at": 100, "lora": "x"},
            {"id": "new", "image_folder": self.ds.upper() if os.name == "nt" else self.ds, "output_dir": self.out,
             "started_at": 200, "lora": "x", "family": "f", "result": "finished", "progress": {"total_epochs": 2},
             "values": {"KREA2_LOSS_WATCH": True}},
            {"id": "other", "image_folder": os.path.join(d, "elsewhere"), "output_dir": self.out, "started_at": 300}])
        self._home = os.environ.get("MONKAP_HOME")
        os.environ["MONKAP_HOME"] = os.path.join(d, "home")

    def tearDown(self):
        if self._home is None:
            os.environ.pop("MONKAP_HOME", None)
        else:
            os.environ["MONKAP_HOME"] = self._home
        self.tmp.cleanup()


class ReviewTests(Case):
    def test_the_newest_run_on_this_folder_is_found(self):
        self.assertEqual(fr.find_run(self.fz, self.ds)["id"], "new")                # not the other folder's, not the older
        self.assertIsNone(fr.find_run(self.fz, os.path.join(self.tmp.name, "nowhere")))

    def test_residual_is_loss_minus_the_noise_level_mean_and_skips_broken_lines_and_batches(self):
        r = fr.residuals(os.path.join(self.out, "loss_log", "per_image_loss.jsonl"))
        self.assertEqual(sorted(r), ["a", "b", "c"])
        self.assertEqual(len(r["a"]), 2)
        self.assertGreater(r["b"][0], r["a"][0])
        self.assertAlmostEqual(r["b"][0] - r["a"][0], 0.20, places=6)

    def test_review_gathers_verdicts_settings_and_summary(self):
        rev = fr.review(self.fz, self.ds)
        self.assertTrue(rev["available"])
        self.assertEqual(rev["images"]["b"]["verdict"], "stuck")
        self.assertGreater(rev["images"]["b"]["recent"], rev["images"]["a"]["recent"])
        self.assertEqual((rev["report"]["best_epoch"], rev["run"]["epochs"]), (1, 2))
        self.assertTrue(rev["settings"]["loss_watch"])
        self.assertFalse(rev["settings"]["per_image_lr"])

    def test_no_run_is_not_an_error(self):
        rev = fr.review(self.fz, os.path.join(self.tmp.name, "nowhere"))
        self.assertFalse(rev["available"])
        self.assertTrue(rev["reason"])
        self.assertFalse(fr.review(self.ds, self.ds)["available"])                  # not a Fizgig folder at all

    def test_advice_names_fizgigs_settings_and_the_best_epoch(self):
        text = "\n".join(fr.advice(fr.review(self.fz, self.ds)))
        self.assertIn("Only 3 pictures", text)
        self.assertIn("Auto-recaption stuck images", text)                          # not ticked in this run
        self.assertIn("Per-image adaptive LR", text)
        self.assertIn("epoch 1 of 2", text)


class ApiReviewTests(Case):
    def test_the_review_by_file_name_with_the_legend(self):
        self.assertFalse(api.review(self.ds)["available"])                          # no Fizgig folder set yet
        api.save_settings({"fizgig_dir": self.fz})
        r = api.review(self.ds)
        self.assertTrue(r["available"])
        self.assertEqual(r["verdicts"]["b.png"]["label"], "Stuck")
        self.assertEqual(r["counts"]["stuck"], 1)
        self.assertEqual(len(r["legend"]), 9)
        self.assertTrue(all(v["means"] and v["todo"] for v in r["verdicts"].values()))


if __name__ == "__main__":
    unittest.main()
