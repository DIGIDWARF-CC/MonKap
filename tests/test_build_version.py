"""packaging/build.py --bump: the revision (third number) goes up for a build, and comes back when the build fails."""
import importlib.util
import os
import sys
import tempfile
import unittest

import _path  # noqa: F401

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..")


def load_build():
    spec = importlib.util.spec_from_file_location("monkap_build", os.path.join(ROOT, "packaging", "build.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class Case(unittest.TestCase):
    def setUp(self):
        self.b = load_build()
        self.tmp = tempfile.TemporaryDirectory()
        self.init = os.path.join(self.tmp.name, "__init__.py")
        self.write('"""MonKap."""\n\n__version__ = "0.3.2"\nOTHER = "1.2.3"\n')

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, text):
        with open(self.init, "w", encoding="utf-8", newline="") as f:
            f.write(text)

    def text(self):
        with open(self.init, encoding="utf-8") as f:
            return f.read()


class BumpTests(Case):
    def test_the_third_number_goes_up_and_nothing_else_changes(self):
        self.assertEqual(self.b.bump_version(self.init), ("0.3.2", "0.3.3"))
        self.assertEqual(self.text(), '"""MonKap."""\n\n__version__ = "0.3.3"\nOTHER = "1.2.3"\n')
        self.assertEqual(self.b.read_version(self.init), "0.3.3")

    def test_the_revision_is_a_number_not_a_digit(self):
        self.write('__version__ = "0.3.9"\n')
        self.assertEqual(self.b.bump_version(self.init)[1], "0.3.10")
        self.assertEqual(self.b.bump_version(self.init)[1], "0.3.11")

    def test_line_endings_are_kept(self):
        self.write('__version__ = "1.0.0"\r\nOTHER = 1\r\n')
        self.b.bump_version(self.init)
        with open(self.init, "rb") as f:
            self.assertEqual(f.read(), b'__version__ = "1.0.1"\r\nOTHER = 1\r\n')

    def test_a_file_without_a_version_is_refused_and_left_alone(self):
        self.write("x = 1\n")
        with self.assertRaises(SystemExit):
            self.b.bump_version(self.init)
        self.assertEqual(self.text(), "x = 1\n")
        with self.assertRaises(SystemExit):
            self.b.write_version("not.a.version", self.init)

    def test_the_real_version_is_in_one_place_and_the_project_reads_it_from_there(self):
        import monkap
        self.assertEqual(self.b.read_version(), monkap.__version__)
        with open(os.path.join(ROOT, "pyproject.toml"), encoding="utf-8") as f:
            toml = f.read()
        self.assertIn('dynamic = ["version"]', toml)
        self.assertNotRegex(toml, r'(?m)^version\s*=\s*"\d')


class MainTests(Case):
    def run_main(self, build, *args):
        old = (self.b.INIT, self.b.build, sys.argv)
        self.b.INIT, self.b.build, sys.argv = self.init, build, ["build.py", *args]
        try:
            return self.b.main()
        finally:
            self.b.INIT, self.b.build, sys.argv = old

    def test_a_good_build_with_bump_keeps_the_new_number_and_builds_that_version(self):
        seen = []
        self.assertEqual(self.run_main(lambda v, n: seen.append((v, n)) or 0, "--bump"), 0)
        self.assertEqual((seen, self.b.read_version(self.init)), ([("0.3.3", False)], "0.3.3"))

    def test_without_bump_the_number_stays(self):
        seen = []
        self.run_main(lambda v, n: seen.append(v) or 0, "--no-installer")
        self.assertEqual((seen, self.b.read_version(self.init)), (["0.3.2"], "0.3.2"))

    def test_a_failed_build_gives_the_revision_back(self):
        self.assertEqual(self.run_main(lambda v, n: 1, "--bump"), 1)
        self.assertEqual(self.b.read_version(self.init), "0.3.2")

        def boom(v, n):
            raise RuntimeError("PyInstaller fell over")
        with self.assertRaises(RuntimeError):
            self.run_main(boom, "--bump")
        self.assertEqual(self.b.read_version(self.init), "0.3.2")


if __name__ == "__main__":
    unittest.main()
