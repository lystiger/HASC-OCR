import ast
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def extract_imports(code, file_path=None):
    """Extract all imported module/object paths from python code, resolving relative imports."""
    tree = ast.parse(code)
    imports = []

    parts = []
    if file_path:
        p = Path(file_path).resolve()
        try:
            rel = p.relative_to(REPO_ROOT)
            parts = list(rel.with_suffix("").parts)
            # Both package __init__.py and submodule.py have parent package as parts[:-1]
            parts = parts[:-1]
        except ValueError:
            pass

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0:
                mod = node.module or ""
                if mod:
                    imports.append(mod)
                for alias in node.names:
                    imports.append(f"{mod}.{alias.name}" if mod else alias.name)
            else:
                if parts and len(parts) >= (node.level - 1):
                    base_parts = parts[: len(parts) - (node.level - 1)]
                    base = ".".join(base_parts)
                    if node.module:
                        mod = f"{base}.{node.module}" if base else node.module
                        imports.append(mod)
                        for alias in node.names:
                            imports.append(f"{mod}.{alias.name}")
                    else:
                        for alias in node.names:
                            imports.append(f"{base}.{alias.name}" if base else alias.name)
                else:
                    mod = node.module or ""
                    if mod:
                        imports.append(mod)
                    for alias in node.names:
                        imports.append(f"{mod}.{alias.name}" if mod else alias.name)
    return imports


def find_web_violations(path):
    violations = []
    code = Path(path).read_text(encoding="utf-8")
    for imp in extract_imports(code, path):
        # hasc.reader.photo is allowed for upright display copy (t5)
        if (
            imp == "hasc.reader"
            or imp.startswith("hasc.reader.")
            or imp == "reader"
            or imp.startswith("reader.")
        ) and not (imp == "hasc.reader.photo" or imp.startswith("hasc.reader.photo.")):
            violations.append((path, imp))
    return violations


def find_reader_violations(path):
    violations = []
    code = Path(path).read_text(encoding="utf-8")
    forbidden = ("web", "worker", "db", "hasc.web", "hasc.worker", "hasc.db")
    for imp in extract_imports(code, path):
        for bad in forbidden:
            if imp == bad or imp.startswith(f"{bad}."):
                violations.append((path, imp))
    return violations


class TestImportBoundaries(unittest.TestCase):
    def test_web_does_not_import_reader(self):
        web_dir = REPO_ROOT / "hasc" / "web"
        files = list(web_dir.rglob("*.py"))
        self.assertTrue(files, "hasc/web must contain python files")
        violations = []
        for file in files:
            violations.extend(find_web_violations(file))
        self.assertEqual(violations, [], f"hasc/web must not import hasc/reader: {violations}")

    def test_reader_does_not_import_web_worker_or_db(self):
        reader_dir = REPO_ROOT / "hasc" / "reader"
        files = list(reader_dir.rglob("*.py"))
        self.assertTrue(files, "hasc/reader must contain python files")
        violations = []
        for file in files:
            violations.extend(find_reader_violations(file))
        self.assertEqual(violations, [], f"hasc/reader must not import web, worker or db: {violations}")

    def test_boundary_checker_catches_violations(self):
        sample_web = REPO_ROOT / "hasc" / "web" / "sample.py"
        sample_reader = REPO_ROOT / "hasc" / "reader" / "sample.py"

        # Web importing reader
        code = "from hasc.reader import CloudReader"
        imps = extract_imports(code, sample_web)
        self.assertTrue(any(i == "hasc.reader" or i.startswith("hasc.reader.") for i in imps))

        code = "from ..reader import photo"
        imps = extract_imports(code, sample_web)
        self.assertTrue(any(i == "hasc.reader" or i.startswith("hasc.reader.") for i in imps))

        code = "from hasc import reader"
        imps = extract_imports(code, sample_web)
        self.assertTrue(any(i == "hasc.reader" or i.startswith("hasc.reader.") for i in imps))

        # Reader importing web, worker, db
        for target in ("web", "worker", "db"):
            code = f"from hasc.{target} import something"
            imps = extract_imports(code, sample_reader)
            self.assertTrue(any(i == f"hasc.{target}" or i.startswith(f"hasc.{target}.") for i in imps))

            code = f"from ..{target} import something"
            imps = extract_imports(code, sample_reader)
            self.assertTrue(any(i == f"hasc.{target}" or i.startswith(f"hasc.{target}.") for i in imps))

            code = f"import {target}"
            imps = extract_imports(code, sample_reader)
            self.assertTrue(any(i == target or i.startswith(f"{target}.") for i in imps))


if __name__ == "__main__":
    unittest.main()
