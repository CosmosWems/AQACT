"""Run the browser-service tests with Node when it is available.

The AI layer lives in ``static/js/services`` and is exercised by Node's built-in
test runner.  This wrapper keeps ``python -m unittest discover -s tests`` as the
single entry point without making Node a hard requirement.
"""

from __future__ import annotations

import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NODE = shutil.which("node")


@unittest.skipUnless(NODE, "Node.js is not installed")
class BrowserServiceTests(unittest.TestCase):
    def test_ai_services(self) -> None:
        result = subprocess.run(
            [NODE, "--test", "tests/js/ai_services.test.mjs"],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=180,
        )
        self.assertEqual(
            result.returncode,
            0,
            f"Node service tests failed.\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}",
        )


if __name__ == "__main__":
    unittest.main()
