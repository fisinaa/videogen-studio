"""Check inline JavaScript syntax in VideoGen templates using Node.js.

Run: .venv/bin/python scripts/check_frontend_js.py
No browser or web server required.
"""
from __future__ import annotations

import re
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
templates = sorted((ROOT / "app" / "templates").glob("*.html"))
if not templates:
    raise SystemExit("No HTML templates found")

count = 0
for template in templates:
    text = template.read_text(encoding="utf-8")
    blocks = re.findall(r"<script(?:\s[^>]*)?>([\s\S]*?)</script>", text, flags=re.IGNORECASE)
    for index, block in enumerate(blocks, 1):
        if not block.strip():
            continue
        # Jinja template tokens are not valid JS; these templates use
        # JavaScript in <script> blocks without server-side expressions.
        with tempfile.NamedTemporaryFile("w", suffix=".js", encoding="utf-8") as output:
            output.write(block)
            output.flush()
            result = subprocess.run(
                ["node", "--check", output.name],
                capture_output=True, text=True,
            )
        if result.returncode:
            raise SystemExit(
                f"JavaScript syntax error in {template.name} script #{index}:\n"
                + result.stderr
            )
        count += 1
print(f"OK: JavaScript syntax valid ({count} inline scripts)")
