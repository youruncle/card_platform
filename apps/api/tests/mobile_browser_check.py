"""Browser smoke runner. Invoked only by scripts/test-mobile.sh in a disposable DB."""

import os
from pathlib import Path
import subprocess
import tempfile
import time
import urllib.request
from uuid import uuid4

import cv2
import numpy as np
from fastapi.testclient import TestClient
from card_platform.main import app

assert os.environ["DATABASE_URL"] == os.environ["TEST_DATABASE_URL"]
assert os.environ["TEST_DATABASE_URL"].endswith("/mobile_test")
root = Path(__file__).resolve().parents[3]
email = f"browser-{uuid4()}@example.invalid"
password = "Temporary-browser-password-only!"
with TestClient(app) as client:
    result = client.post(
        "/v1/auth/register",
        json={"email": email, "password": password, "display_name": "测试收藏者"},
    )
    assert result.status_code == 201, result.text
with tempfile.TemporaryDirectory() as temp:
    sample = Path(temp) / "sample.png"
    image = np.full((600, 400, 3), (225, 230, 215), dtype=np.uint8)
    cv2.rectangle(image, (25, 25), (375, 575), (70, 100, 80), 8)
    cv2.putText(image, "TEST CARD", (55, 200), cv2.FONT_HERSHEY_SIMPLEX, 1.2, (70, 100, 80), 2)
    cv2.imwrite(str(sample), image)
    env = dict(
        os.environ,
        MOBILE_DEV_API="http://127.0.0.1:8019",
        VITE_MOBILE_DEV_ORIGIN="http://127.0.0.1:5178",
        MOBILE_TEST_EMAIL=email,
        MOBILE_TEST_PASSWORD=password,
        MOBILE_TEST_IMAGE=str(sample),
    )
    processes = []
    try:
        with (root / "work/mobile/browser-services.log").open("w") as log:
            processes.append(
                subprocess.Popen(
                    [
                        str(root / ".venv/bin/python"),
                        "-m",
                        "uvicorn",
                        "card_platform.main:app",
                        "--host",
                        "127.0.0.1",
                        "--port",
                        "8019",
                    ],
                    cwd=root / "apps/api",
                    env=env,
                    stdout=log,
                    stderr=log,
                )
            )
            processes.append(
                subprocess.Popen(
                    ["node", "node_modules/vite/bin/vite.js", "--host", "127.0.0.1"],
                    cwd=root / "apps/mobile",
                    env=env,
                    stdout=log,
                    stderr=log,
                )
            )
            for url in ("http://127.0.0.1:8019/health/ready", "http://127.0.0.1:5178"):
                for _ in range(60):
                    try:
                        with urllib.request.urlopen(url, timeout=1) as response:
                            if response.status == 200:
                                break
                    except Exception:
                        time.sleep(0.3)
                else:
                    raise RuntimeError("test service did not start")
            subprocess.run(
                ["node", "node_modules/@playwright/test/cli.js", "test"],
                cwd=root / "apps/mobile",
                env=env,
                check=True,
            )
    finally:
        for process in processes:
            process.terminate()
        for process in processes:
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
