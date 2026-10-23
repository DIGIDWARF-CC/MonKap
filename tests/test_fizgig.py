"""The read-only look at a Fizgig install, on a made-up one (nothing of a real Fizgig is touched)."""
import json
import os
import tempfile
import unittest

import _path  # noqa: F401
from PIL import Image

from monkap import api, appdata
from monkap.integrations import fizgig


def write(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(data if isinstance(data, str) else json.dumps(data))


class FakeFizgig(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.d = self.tmp.name
        self.fz = os.path.join(self.d, "Fizgig")
        self.ds = os.path.join(self.d, "ds")
        os.makedirs(self.ds)
        write(os.path.join(self.fz, "fizgig.py"), "")
        write(os.path.join(self.fz, "docs", "RELEASE_NOTES_v8.5.9.md"), "")
        write(os.path.join(self.fz, "docs", "RELEASE_NOTES_v8.10.0.md"), "")
        write(os.path.join(self.fz, "docs", "RELEASE_NOTES_v8.9.1.md"), "")
        write(os.path.join(self.fz, "user_data", "last_used.json"), {
            "image_folder": self.ds, "recent_folders": [self.ds],
            "folder_triggers": {os.path.normcase(os.path.abspath(self.ds)): "ohwx,"}})
        write(os.path.join(self.fz, "user_data", "web_state.json"),
              {"family": "klein", "values": {"klein": {"DATASET_CAPTION_EXT": "caption"}}})
        write(os.path.join(self.fz, "user_data", "prefs.json"), {"cache_dir": "user_data/cache", "lora_output_dir": "output_loras"})
        self._home = os.environ.get("MONKAP_HOME")
        os.environ["MONKAP_HOME"] = os.path.join(self.d, "home")

    def tearDown(self):
        if self._home is None:
            os.environ.pop("MONKAP_HOME", None)
        else:
            os.environ["MONKAP_HOME"] = self._home
        self.tmp.cleanup()


class ReadTests(FakeFizgig):
    def test_it_recognises_an_install_and_reads_the_newest_version_by_number(self):
        self.assertTrue(fizgig.looks_like_fizgig(self.fz))
        self.assertFalse(fizgig.looks_like_fizgig(self.ds))
        self.assertEqual(fizgig.version(self.fz), "8.10.0")                      # not "8.9.1": numbers, not text

    def test_trigger_extension_and_paths(self):
        self.assertEqual(fizgig.trigger_word(self.fz, self.ds), "ohwx")           # the trailing comma is not part of it
        self.assertEqual(fizgig.trigger_word(self.fz, os.path.join(self.d, "other")), "")
        self.assertEqual(fizgig.caption_ext(self.fz), ".caption")
        self.assertEqual(fizgig.cache_root(self.fz), os.path.join(self.fz, "user_data", "cache"))
        self.assertEqual(fizgig.output_dir(self.fz), os.path.join(self.fz, "output_loras"))

    def test_the_latent_cache_folder_is_named_the_way_fizgig_names_it(self):
        # the expected names come from Fizgig 8.5.9's own cache_dir_for
        self.write_cache_root("C:/c")
        self.assertEqual(fizgig.latent_cache_for(self.fz, "D:/data/My Photos/"), os.path.join("C:/c", "My_Photos-ac530214"))
        self.assertEqual(fizgig.latent_cache_for(self.fz, "D:\\data\\Engines"), os.path.join("C:/c", "Engines-7f78de7d"))

    def write_cache_root(self, root):
        write(os.path.join(self.fz, "user_data", "prefs.json"), {"cache_dir": root})

    def test_missing_or_broken_files_give_nothing_not_errors(self):
        os.remove(os.path.join(self.fz, "user_data", "web_state.json"))
        write(os.path.join(self.fz, "user_data", "last_used.json"), "{not json")
        self.assertEqual(fizgig.caption_ext(self.fz), "")
        self.assertEqual(fizgig.trigger_word(self.fz, self.ds), "")
        self.assertTrue(fizgig.summary(self.fz)["ok"])
        self.assertFalse(fizgig.summary(self.ds)["ok"])
        self.assertFalse(fizgig.summary("")["ok"])

    def test_a_newer_fizgig_gets_a_note(self):
        self.assertIn("newer", fizgig.summary(self.fz)["note"])                  # 8.10.0 against 8.5.9


class FollowTests(FakeFizgig):
    def setUp(self):
        super().setUp()
        Image.new("RGB", (40, 30)).save(os.path.join(self.ds, "a.png"))

    def test_follows_fizgig_for_trigger_and_extension_only_when_told_to(self):
        self.assertEqual(api.folder_trigger(self.ds), "")
        api.save_settings({"fizgig_dir": self.fz})
        self.assertEqual(api.folder_trigger(self.ds), "ohwx")
        self.assertEqual(api.effective_ext(), ".caption")
        api.save_settings({"fizgig_trigger": False, "fizgig_ext": False})
        self.assertEqual((api.folder_trigger(self.ds), api.effective_ext()), ("", ".txt"))

    def test_the_triggers_set_in_monkap_win(self):
        api.save_settings({"fizgig_dir": self.fz})
        api.open_folder(self.ds, "mine")
        self.assertEqual(api.folder_trigger(self.ds), "mine")

    def test_a_hard_delete_clears_the_picture_in_fizgigs_cache_folder(self):
        cache = fizgig.latent_cache_for(self.fz, self.ds)
        os.makedirs(cache)
        for n in ("a_1024x1024_klein.safetensors", "a_crop_001_1024x1024_klein.safetensors"):
            open(os.path.join(cache, n), "w").close()
        api.save_settings({"fizgig_dir": self.fz, "delete_mode": "hard"})
        r = api.delete(self.ds, "a.png", ".txt")
        self.assertEqual(r["purged"], 1)
        self.assertEqual(os.listdir(cache), ["a_crop_001_1024x1024_klein.safetensors"])   # a crop's latents stay


if __name__ == "__main__":
    unittest.main()
