"""Every source file must parse on the oldest Python this project claims.

`pyproject.toml` says `requires-python = ">=3.11"`. A developer working on 3.12
can write syntax 3.11 rejects and never notice -- f-string escaping changed in
3.12 (PEP 701), so a backslash inside an f-string expression compiles on 3.12
and is a SyntaxError on 3.11. That exact mistake shipped once and was caught by
the CI matrix; this test moves the feedback to the developer's own machine.

``ast.parse(feature_version=(3, 11))`` cannot be used for this: the restriction
lives in the tokenizer, and a 3.12 tokenizer does not re-apply it. So the check
runs the real interpreter when one can be found, and skips loudly when it
cannot -- CI remains the authority, since it runs the whole suite on 3.11, 3.12
and 3.13.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
import tomllib
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]
SKIP_DIRECTORIES = {".venv", "build", "dist", "__pycache__", ".git"}


def minimum_version() -> tuple[int, int]:
    raw = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    requires = raw["project"]["requires-python"]
    match = re.search(r">=\s*(\d+)\.(\d+)", requires)
    if match is None:
        raise AssertionError(f"cannot read a minimum version from {requires!r}")
    return int(match.group(1)), int(match.group(2))


def source_files() -> list[Path]:
    return sorted(
        path for path in ROOT.rglob("*.py")
        if not SKIP_DIRECTORIES.intersection(path.relative_to(ROOT).parts)
    )


def find_interpreter(version: tuple[int, int]) -> str | None:
    name = f"python{version[0]}.{version[1]}"
    found = shutil.which(name)
    if found:
        return found
    try:
        result = subprocess.run(
            ["uv", "python", "find", f"{version[0]}.{version[1]}"],
            capture_output=True, text=True, timeout=30, cwd=ROOT,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    candidate = result.stdout.strip()
    return candidate if result.returncode == 0 and Path(candidate).exists() else None


CHECK = """
import sys
from pathlib import Path
failures = []
for name in sys.argv[1:]:
    path = Path(name)
    try:
        compile(path.read_text(encoding="utf-8"), name, "exec")
    except SyntaxError as exc:
        failures.append(f"{name}:{exc.lineno}: {exc.msg}")
print("\\n".join(failures))
sys.exit(1 if failures else 0)
"""


class MinimumPythonTests(unittest.TestCase):
    def test_pyproject_declares_a_minimum(self) -> None:
        major, minor = minimum_version()
        self.assertEqual((major, minor), (3, 11))

    def test_every_source_file_compiles_on_the_minimum_interpreter(self) -> None:
        version = minimum_version()
        files = source_files()
        self.assertGreater(len(files), 30, "source discovery found suspiciously few files")

        if sys.version_info[:2] == version:
            failures = [
                f"{path}:{exc.lineno}: {exc.msg}"
                for path in files
                for exc in _syntax_error(path)
            ]
            self.assertEqual(failures, [], "\n".join(failures))
            return

        interpreter = find_interpreter(version)
        if interpreter is None:
            self.skipTest(
                f"python{version[0]}.{version[1]} is not available here; CI runs the "
                f"whole suite on {version[0]}.{version[1]}, 3.12 and 3.13"
            )
        result = subprocess.run(
            [interpreter, "-c", CHECK, *[str(path) for path in files]],
            capture_output=True, text=True, cwd=ROOT,
        )
        self.assertEqual(result.returncode, 0, result.stdout.strip() or result.stderr.strip())


def _syntax_error(path: Path) -> list[SyntaxError]:
    try:
        compile(path.read_text(encoding="utf-8"), str(path), "exec")
    except SyntaxError as exc:
        return [exc]
    return []


if __name__ == "__main__":
    unittest.main()
