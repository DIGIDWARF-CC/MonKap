"""The enhance routes (monkap/enhance.py through api.py) against a made-up ComfyUI: start, status, preview, result and the four
decisions."""
import json
import os
import tempfile
import time
import unittest

import _path  # noqa: F401
from fake_comfy import PREVIEW, RESULT, FakeComfy
from test_api import FakeHandler, call_get, call_post, make_png

from monkap import api, enhance
from monkap.core import captions
from test_comfy import CLASSES


def read(path) -> bytes:
    with open(path, 'rb') as f:
        return f.read()


class Case(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = self.tmp.name
        self._home = os.environ.get("MONKAP_HOME")
        os.environ["MONKAP_HOME"] = os.path.join(self.d, "home")
        self.ds = os.path.join(self.d, "ds")
        os.makedirs(self.ds)
        make_png(os.path.join(self.ds, "shot.png"), (60, 40))
        with open(os.path.join(self.ds, "shot.txt"), "w", encoding="utf-8") as f:
            f.write("a belt over two pulleys")
        self.fc = FakeComfy(classes=CLASSES, steps=3)
        call_post("/api/settings", {"settings": {"comfy_url": self.fc.url}})
        enhance._run = None

    def tearDown(self):
        job = enhance._run["job"] if enhance._run else None
        if job:
            job.join(10)
        enhance._run = None
        self.fc.stop()
        if self._home is None:
            os.environ.pop("MONKAP_HOME", None)
        else:
            os.environ["MONKAP_HOME"] = self._home
        self.tmp.cleanup()

    def start(self, **extra):
        h = call_post("/api/enhance/start", {"folder": self.ds, "name": "shot.png", **extra})
        self.assertEqual(h.code, 200, h.data)
        return h.data

    def wait(self, status="done", timeout=15):
        end = time.time() + timeout
        while time.time() < end:
            st = call_get("/api/enhance/status").data
            if st["status"] == status:
                return st
            time.sleep(0.03)
        self.fail(f"never became {status}: {call_get('/api/enhance/status').data}")

    def decide(self, action, **extra):
        return call_post("/api/enhance/decide", {"action": action, "folder": self.ds, "name": "shot.png", **extra})


class RunTests(Case):
    def test_a_run_shows_progress_a_preview_and_the_result(self):
        self.assertEqual(call_get("/api/enhance/status").data, {"status": "none"})
        seed = self.start(hint="  sharper,\n only the face ")["seed"]
        st = self.wait()
        self.assertEqual((st["name"], st["folder"], st["seed"], st["percent"], st["result"]), ("shot.png", self.ds, seed, 100, True))
        self.assertEqual(call_get("/api/enhance/result").body, RESULT)
        self.assertEqual(call_get("/api/enhance/preview").body, PREVIEW)
        sent = self.fc.prompts[0]
        base = "Enhance quality, reduce noise and blur, make clean and sharp, keep the content unchanged"
        self.assertEqual((sent["2"]["inputs"]["value"], sent["3"]["inputs"]["value"]), (base + ". sharper, only the face", seed))

    def test_the_hint_is_added_to_the_settings_prompt_not_put_in_its_place(self):
        call_post("/api/settings", {"settings": {"enhance_prompt": "make it crisp"}})
        self.start(hint="only the face")
        self.wait()
        self.assertEqual(self.fc.prompts[0]["2"]["inputs"]["value"], "make it crisp. only the face")

    def test_a_hint_for_a_template_without_a_prompt_node_is_refused_in_words(self):
        with open(enhance.TEMPLATE, encoding="utf-8") as f:
            g = json.load(f)
        del g["2"], g["3"]
        g["9"]["inputs"]["prompt"] = "fixed"
        g["10"]["inputs"]["seed"] = 5
        own = os.path.join(self.d, "own.json")
        with open(own, "w", encoding="utf-8") as f:
            json.dump(g, f)
        call_post("/api/settings", {"settings": {"enhance_template": own}})
        h = call_post("/api/enhance/start", {"folder": self.ds, "name": "shot.png", "hint": "only the face"})
        self.assertEqual(h.code, 400)
        self.assertIn("no \"MonKap Prompt\" node", h.data["error"])
        self.start()                                                         # without words the same template runs
        self.wait()

    def test_the_settings_prompt_is_used_when_there_is_no_hint(self):
        call_post("/api/settings", {"settings": {"enhance_prompt": "make it crisp"}})
        self.start()
        self.wait()
        self.assertEqual(self.fc.prompts[0]["2"]["inputs"]["value"], "make it crisp")

    def test_nothing_to_show_before_a_run_and_a_second_run_is_refused_while_one_goes(self):
        self.assertEqual(call_get("/api/enhance/result").code, 404)
        self.fc.steps = 30
        self.fc.step_delay = 0.1
        self.start()
        h = call_post("/api/enhance/start", {"folder": self.ds, "name": "shot.png"})
        self.assertEqual(h.code, 400)
        self.assertIn("already going", h.data["error"])
        call_post("/api/enhance/cancel", {})
        self.wait("cancelled")

    def test_a_picture_that_is_not_there_and_a_broken_template_are_refused_with_words(self):
        h = call_post("/api/enhance/start", {"folder": self.ds, "name": "nope.png"})
        self.assertEqual(h.code, 404)
        bad = os.path.join(self.d, "bad.json")
        with open(bad, "w", encoding="utf-8") as f:
            json.dump({"nodes": [], "links": []}, f)
        call_post("/api/settings", {"settings": {"enhance_template": bad}})
        h = call_post("/api/enhance/start", {"folder": self.ds, "name": "shot.png"})
        self.assertEqual(h.code, 400)
        self.assertIn("Save (API Format)", h.data["error"])

    def test_check_reports_the_server_and_the_template(self):
        self.assertTrue(call_get("/api/enhance/check").data["ok"])
        del self.fc.classes["KSampler"]
        data = call_get("/api/enhance/check").data
        self.assertFalse(data["ok"])
        self.assertIn("KSampler", data["problems"][0])
        call_post("/api/settings", {"settings": {"comfy_url": "http://127.0.0.1:1"}})
        data = call_get("/api/enhance/check").data
        self.assertFalse(data["ok"])
        self.assertIn("Can't reach ComfyUI", data["error"])

    def test_the_reference_template_can_be_downloaded_and_is_the_one_in_the_package(self):
        h = call_get("/api/enhance/template")
        self.assertEqual(h.ctype, "application/json")
        self.assertIn("MonKap Image", h.body.decode("utf-8"))

    def test_free_asks_comfy_to_unload_and_says_so_when_it_cannot(self):
        self.assertEqual(call_post("/api/enhance/free", {}).data, {"freed": True})
        self.assertEqual(self.fc.freed, 1)
        call_post("/api/settings", {"settings": {"comfy_url": "http://127.0.0.1:1"}})
        self.assertEqual(call_post("/api/enhance/free", {}).code, 400)


class DecisionTests(Case):
    def setUp(self):
        super().setUp()
        self.start()
        self.wait()
        self.original = read(os.path.join(self.ds, "shot.png"))

    def tearDown(self):
        super().tearDown()

    def files(self):
        return sorted(n for n in os.listdir(self.ds) if not n.startswith("."))

    def test_discard_forgets_the_result_and_changes_no_file(self):
        self.assertEqual(self.decide("discard").data, {"action": "discard"})
        self.assertEqual(call_get("/api/enhance/status").data, {"status": "none"})
        self.assertEqual(self.files(), ["shot.png", "shot.txt"])

    def test_retry_runs_again_with_a_new_seed_and_the_same_words(self):
        first = call_get("/api/enhance/status").data["seed"]
        out = self.decide("retry").data
        self.assertEqual(out["action"], "retry")
        st = self.wait()
        self.assertNotEqual(st["seed"], first)
        self.assertEqual(out["seed"], st["seed"])
        self.assertEqual(len(self.fc.prompts), 2)
        self.assertEqual(self.fc.prompts[0]["2"]["inputs"]["value"], self.fc.prompts[1]["2"]["inputs"]["value"])

    def test_store_as_kapture_makes_a_new_picture_with_the_caption_of_the_parent(self):
        out = self.decide("kapture", text="a belt over two pulleys", trigger="").data
        self.assertEqual((out["action"], out["name"], out["caption"]), ("kapture", "shot_crop_001.png", "a belt over two pulleys"))
        self.assertEqual(self.files(), ["shot.png", "shot.txt", "shot_crop_001.png", "shot_crop_001.txt"])
        self.assertEqual(read(os.path.join(self.ds, "shot.png")), self.original)
        self.assertEqual(call_get("/api/enhance/status").data, {"status": "none"})

    def test_replace_keeps_the_original_and_undo_brings_it_back(self):
        out = self.decide("replace").data
        self.assertEqual((out["action"], out["name"], out["old"]), ("replace", "shot.png", "shot.png"))
        self.assertNotEqual(read(os.path.join(self.ds, "shot.png")), self.original)
        self.assertEqual(captions.read_caption(os.path.join(self.ds, "shot.png")), "a belt over two pulleys")
        back = call_post("/api/enhance/undo", {"folder": self.ds, "name": out["name"], "backup": out["backup"], "old": out["old"]})
        self.assertEqual(back.data, {"restored": "shot.png"})
        self.assertEqual(read(os.path.join(self.ds, "shot.png")), self.original)

    def test_a_decision_for_another_picture_or_without_a_result_is_refused(self):
        h = call_post("/api/enhance/decide", {"action": "replace", "folder": self.ds, "name": "other.png"})
        self.assertEqual(h.code, 400)
        self.assertIn("another picture", h.data["error"])
        self.assertEqual(self.decide("explode").code, 400)
        self.decide("discard")
        self.assertEqual(self.decide("replace").code, 400)
        self.assertIn("no enhance result", self.decide("replace").data["error"])


if __name__ == "__main__":
    unittest.main()
