"""Spec R9 and criteria 14 and 18: nothing but the summary sends a Zalo message, and no test touches private data."""
import ast
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parent.parent
SENDERS = {"hasc/zalo/summary.py"}  # the only module that may ask the Bot API to send a message
PRIVATE = ("Documents/" + "HASC", ".config/" + "hasc-ocr")


def forbid_sends(case):
    """Replace the Bot API call for one test and fail the test at cleanup if anything was sent through it."""
    from unittest import mock

    from hasc.zalo import receiver

    sent = []
    patcher = mock.patch.object(receiver, "call", side_effect=lambda *args, **kwargs: sent.append(args))
    patcher.start()
    case.addCleanup(patcher.stop)
    case.addCleanup(lambda: case.assertEqual(sent, [], "no Zalo message is sent"))


def python_files(folder):
    return [p for p in (ROOT / folder).rglob("*.py") if "__pycache__" not in p.parts]


class NothingSends(unittest.TestCase):
    def test_only_the_summary_module_calls_the_bot_api_to_send(self):
        offenders = []
        for path in python_files("hasc"):
            relative = path.relative_to(ROOT).as_posix()
            tree = ast.parse(path.read_text(encoding="utf-8"))
            sends = any(isinstance(n, ast.Constant) and isinstance(n.value, str) and n.value.lower().startswith("send")
                        and n.value[4:5].isupper() for n in ast.walk(tree))
            if sends and relative not in SENDERS:
                offenders.append(relative)
        self.assertEqual(offenders, [])

    def test_the_gate_the_filing_page_and_the_worker_do_not_import_the_sender(self):
        for name in ("hasc/worker/gate.py", "hasc/worker/jobs.py", "hasc/web/zalo_filing.py", "hasc/intake.py",
                     "hasc/plans.py"):
            source = (ROOT / name).read_text(encoding="utf-8")
            self.assertNotIn("zalo.summary", source, name)
            self.assertNotIn("receiver import call", source, name)


class NoPrivateData(unittest.TestCase):
    def test_no_test_or_module_names_a_private_folder(self):
        this = Path(__file__).resolve()
        for path in (*python_files("tests"), *python_files("hasc")):
            if path.resolve() == this:
                continue
            text = path.read_text(encoding="utf-8")
            for private in PRIVATE:
                self.assertNotIn(private, text, f"{path.relative_to(ROOT)} names {private}")


class NoTimeZoneDatabase(unittest.TestCase):
    """Windows Python has no time-zone database and the project ships no tzdata: use a fixed offset."""

    def test_no_module_under_hasc_imports_zoneinfo_or_tzdata(self):
        for path in python_files("hasc"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    names = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom) and node.level == 0:
                    names = [node.module or ""]
                else:
                    continue
                for name in names:
                    self.assertNotIn(name.split(".")[0], {"zoneinfo", "tzdata"},
                                     f"{path.relative_to(ROOT)} imports {name}")


if __name__ == "__main__":
    unittest.main()
