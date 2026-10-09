"""Isolated migration browser regression; never starts a formal Tunnel."""
from __future__ import annotations

import json
import argparse
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--real-pdf", type=Path, required=True, help="Explicit source PDF; only its bytes are read, never a user database")
    args = parser.parse_args()
    if not args.real_pdf.is_file():
        parser.error("the explicitly selected source PDF does not exist")
    with tempfile.TemporaryDirectory(prefix="caeliae-browser-") as temporary:
        env = os.environ.copy()
        for key in list(env):
            if key.startswith("COREAD_") or key.startswith("CAELIAE_READ_"):
                env.pop(key)
        env["CAELIAE_READ_DATA_ROOT"] = str(Path(temporary) / "data")
        with socket.socket() as available:
            available.bind(("127.0.0.1", 0))
            port = available.getsockname()[1]
        env["CAELIAE_READ_API_PORT"] = str(port)
        base = f"http://127.0.0.1:{port}"
        env["CAELIAE_READ_ACCEPTANCE_URL"] = base
        env["CAELIAE_READ_ACCEPTANCE_PDF"] = str(ROOT / "tests/fixtures/selectable-paper.pdf")
        env["CAELIAE_READ_TEST_REAL_PDF"] = str(args.real_pdf.resolve())
        env["CAELIAE_READ_EXPECT_FIXED"] = "1"
        output = ROOT / ".artifacts/naming-migration"
        output.mkdir(parents=True, exist_ok=True)
        for suffix in ("HEADER", "SCROLL", "LAYOUT", "STDIO_WEB", "REVOKE"):
            env[f"CAELIAE_READ_{suffix}_OUTPUT"] = str(output / suffix.lower())
        server = subprocess.Popen([str(Path(sys.executable).parent / "caeliae-read-api.exe")], cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            for _ in range(100):
                if server.poll() is not None:
                    raise RuntimeError("isolated API exited")
                try:
                    with urlopen(base + "/api/health", timeout=1) as response:
                        assert json.load(response)["service"] == "caeliae-read"
                    break
                except OSError:
                    time.sleep(.1)
            else:
                raise RuntimeError("isolated API did not become ready")
            def run(script: str, status: str = "PASS") -> str:
                result = subprocess.run(["node", str(ROOT / "tests" / script)], cwd=ROOT, env=env, capture_output=True, text=True, encoding="utf-8", timeout=180)
                (output / (script + ".log")).write_text(result.stdout + result.stderr, encoding="utf-8")
                if result.returncode:
                    raise RuntimeError(f"{script} failed: {result.stderr}")
                print(f"{status} {script}", flush=True)
                return result.stdout
            seeded = json.loads(run("naming_browser_acceptance.mjs"))
            env["CAELIAE_READ_REMEMBER_ANNOTATION_ID"] = seeded["rememberAnnotationId"]
            run("cross_page_source_preflight.mjs")
            for script in ("annotation_index_acceptance.mjs", "real_annotation_scroll_acceptance.mjs", "compact_header_acceptance.mjs", "zoom_remember_revoke_acceptance.mjs", "stdio_web_annotation_acceptance.mjs", "sticky_note_acceptance.mjs", "responsive_layout_acceptance.mjs", "browser_acceptance.mjs"):
                run(script)
        finally:
            # Only the isolated child process created by this test is stopped.
            server.terminate()
            server.wait(timeout=15)


if __name__ == "__main__":
    main()
