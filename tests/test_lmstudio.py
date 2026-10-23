"""LM Studio captions against a fake server (no LM Studio, no GPU)."""
import copy
import json
import os
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import _path  # noqa: F401
from PIL import Image  # noqa: E402

from monkap.ai import lmstudio  # noqa: E402

MODELS = {"models": [
    {"key": "vis", "type": "llm", "capabilities": {"vision": True}, "loaded_instances": [{"id": "vis"}]},
    {"key": "text-only", "type": "llm", "capabilities": {"vision": False}, "loaded_instances": []},
    {"key": "embed", "type": "embedding"},
    {"key": "vis2", "type": "llm", "capabilities": {"vision": True}, "loaded_instances": []}]}


class Fake(BaseHTTPRequestHandler):
    seen = []
    reply = {"choices": [{"message": {"content": "  \"A dog on a lawn.\"\n"}, "finish_reason": "stop"}]}
    status = 200
    models = MODELS
    unloaded = []
    own_api = True                  # False: a server that has no LM Studio API (the OpenAI list only)

    def log_message(self, *a):
        pass

    def _send(self, code, obj):
        raw = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        if self.path == "/api/v1/models" and Fake.own_api:
            return self._send(200, Fake.models)
        self._send(404, {})

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        if self.path == "/api/v1/models/unload":
            Fake.unloaded.append(body["instance_id"])
            for m in Fake.models["models"]:
                m["loaded_instances"] = [i for i in m.get("loaded_instances", []) if i["id"] != body["instance_id"]]
            return self._send(200, {"instance_id": body["instance_id"]})
        Fake.seen.append(body)
        if Fake.status != 200:
            return self._send(Fake.status, {"error": {"message": "model failed to load"}})
        self._send(200, Fake.reply)


class LmStudioTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), Fake)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.url = f"http://127.0.0.1:{cls.srv.server_address[1]}"
        cls.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        cls.pic = os.path.join(cls.tmp.name, "a.png")
        Image.new("RGBA", (3000, 1500), (200, 30, 30, 255)).save(cls.pic)

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.tmp.cleanup()

    def setUp(self):
        Fake.seen.clear()
        Fake.status = 200
        Fake.models, Fake.own_api = copy.deepcopy(MODELS), True
        Fake.unloaded.clear()

    def test_unload_takes_the_models_instances_out_and_leaves_the_others(self):
        Fake.models["models"][3]["loaded_instances"] = [{"id": "other"}]           # another model loaded: not ours
        self.assertEqual(lmstudio.unload(self.url, "vis"), 1)
        self.assertEqual(Fake.unloaded, ["vis"])
        self.assertEqual([m["loaded"] for m in lmstudio.list_models(self.url)], [False, True])

    def test_unloading_what_is_not_loaded_is_not_an_error(self):
        self.assertEqual(lmstudio.unload(self.url, "vis2"), 0)
        self.assertEqual(lmstudio.unload(self.url, "not-a-model"), 0)
        self.assertEqual(lmstudio.unload(self.url, "vis"), 1)
        self.assertEqual(lmstudio.unload(self.url, "vis"), 0)                     # again: nothing left, no error
        self.assertEqual(Fake.unloaded, ["vis"])

    def test_unload_says_when_the_server_is_not_lm_studio_or_not_there(self):
        Fake.own_api = False
        with self.assertRaisesRegex(ValueError, "not LM Studio"):
            lmstudio.unload(self.url, "vis")
        with self.assertRaises(OSError):
            lmstudio.unload("http://127.0.0.1:9", "vis")

    def test_address_is_tidied(self):
        self.assertEqual(lmstudio.base_url(""), "http://localhost:1234")
        self.assertEqual(lmstudio.base_url("localhost:1234/"), "http://localhost:1234")
        self.assertEqual(lmstudio.base_url("http://10.0.0.5:1234/v1"), "http://10.0.0.5:1234")
        with self.assertRaises(ValueError):
            lmstudio.base_url("http://bad host/")

    def test_only_models_that_see_pictures(self):
        got = lmstudio.list_models(self.url)
        self.assertEqual([m["id"] for m in got], ["vis", "vis2"])
        self.assertEqual([m["loaded"] for m in got], [True, False])

    def test_nothing_listening(self):
        with self.assertRaises(OSError):
            lmstudio.list_models("http://127.0.0.1:9")

    def test_caption_is_one_clean_line_and_picture_is_small_jpeg(self):
        text = lmstudio.caption_image(self.url, "vis", self.pic, "Describe it.", 120)
        self.assertEqual(text, "A dog on a lawn.")
        body = Fake.seen[0]
        self.assertEqual(body["model"], "vis")
        self.assertEqual(body["reasoning_effort"], "none")
        self.assertEqual(body["max_tokens"], 120)
        url = body["messages"][0]["content"][1]["image_url"]["url"]
        self.assertTrue(url.startswith("data:image/jpeg;base64,"))

    def test_thinking_gets_room_and_no_switch(self):
        lmstudio.caption_image(self.url, "vis", self.pic, "x", 100, think=True)
        self.assertNotIn("reasoning_effort", Fake.seen[0])
        self.assertEqual(Fake.seen[0]["max_tokens"], 100 + lmstudio.THINK_EXTRA_TOKENS)

    def test_think_block_is_dropped(self):
        self.assertEqual(lmstudio.clean("<think>hm\nhm</think>\nA cat."), "A cat.")

    def test_server_error_is_said(self):
        Fake.status = 500
        with self.assertRaises(RuntimeError) as c:
            lmstudio.caption_image(self.url, "vis", self.pic, "x", 50)
        self.assertIn("failed to load", str(c.exception))

    def test_ran_out_of_tokens_is_said(self):
        old = Fake.reply
        Fake.reply = {"choices": [{"message": {"content": ""}, "finish_reason": "length"}]}
        try:
            with self.assertRaises(RuntimeError) as c:
                lmstudio.caption_image(self.url, "vis", self.pic, "x", 50)
            self.assertIn("ran out of tokens", str(c.exception))
        finally:
            Fake.reply = old


class RecaptionHintTests(unittest.TestCase):
    """The user's hint rides with the task to the model, and the caption comes back written."""

    @classmethod
    def setUpClass(cls):
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), Fake)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.url = f"http://127.0.0.1:{cls.srv.server_address[1]}"

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def setUp(self):
        from monkap import api
        self.api = api
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self._home = os.environ.get("MONKAP_HOME")
        os.environ["MONKAP_HOME"] = os.path.join(self.tmp.name, "home")
        self.ds = os.path.join(self.tmp.name, "ds")
        os.makedirs(self.ds)
        Image.new("RGB", (64, 64)).save(os.path.join(self.ds, "a.png"))
        api.save_settings({"lm_url": self.url, "lm_model": "vis"})
        Fake.seen.clear()

    def tearDown(self):
        if self._home is None:
            os.environ.pop("MONKAP_HOME", None)
        else:
            os.environ["MONKAP_HOME"] = self._home
        self.tmp.cleanup()

    def sent_text(self):
        return Fake.seen[0]["messages"][0]["content"][0]["text"]

    def test_without_a_hint_the_task_goes_alone(self):
        r = self.api.recaption(self.ds, "a.png", ".txt")
        self.assertEqual(r["caption"], "A dog on a lawn.")
        self.assertNotIn("The user adds this note", self.sent_text())

    def test_the_answer_carries_the_caption_it_replaced_so_that_a_batch_can_be_undone(self):
        self.assertIsNone(self.api.recaption(self.ds, "a.png", ".txt")["before"])          # there was none
        with open(os.path.join(self.ds, "a.png").replace(".png", ".txt"), "w", encoding="utf-8") as f:
            f.write("an old caption")
        r = self.api.recaption(self.ds, "a.png", ".txt")
        self.assertEqual((r["before"], r["caption"]), ("an old caption", "A dog on a lawn."))
        with open(os.path.join(self.ds, "a.txt"), "w", encoding="utf-8") as f:
            f.write("a hand-written one")
        self.assertEqual(self.api.recaption(self.ds, "a.png", ".txt")["before"], "a hand-written one")

    def test_a_hint_goes_after_the_task_tidied_and_cut(self):
        self.api.recaption(self.ds, "a.png", ".txt", "  close-up,\n rear view, the manifold is off ")
        text = self.sent_text()
        self.assertIn("The user adds this note", text)
        self.assertTrue(text.endswith("close-up, rear view, the manifold is off"))
        Fake.seen.clear()
        self.api.recaption(self.ds, "a.png", ".txt", "x" * 5000)
        self.assertTrue(self.sent_text().endswith("x" * self.api.MAX_HINT))


if __name__ == "__main__":
    unittest.main()
