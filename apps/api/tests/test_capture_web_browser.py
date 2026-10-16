"""Opt-in real browser smoke; only scripts/test-mobile.sh disposable database."""

import os
from pathlib import Path
import subprocess
import time
import urllib.request
from uuid import uuid4

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient
from card_platform.main import app

pytestmark = pytest.mark.skipif(
    os.environ.get("WEB_CAPTURE_BROWSER") != "1" or "TEST_DATABASE_URL" not in os.environ,
    reason="opt-in disposable database/browser smoke",
)


def test_web_single_side_crop_and_confirmation_browser(tmp_path):
    root = Path(__file__).resolve().parents[3]
    email = f"web-browser-{uuid4()}@example.invalid"
    password = "Temporary-browser-password-only!"
    with TestClient(app) as client:
        created = client.post(
            "/v1/auth/register",
            json={"email": email, "password": password, "display_name": "Web smoke"},
        )
        assert created.status_code == 201, created.text
    sample = tmp_path / "card.png"
    pixels = np.full((600, 400, 3), 210, dtype=np.uint8)
    cv2.rectangle(pixels, (30, 30), (370, 570), (60, 60, 60), 6)
    cv2.imwrite(str(sample), pixels)
    env = dict(
        os.environ,
        ALLOWED_ORIGINS="http://127.0.0.1:3040",
        API_INTERNAL_URL="http://127.0.0.1:8040",
        NEXT_PUBLIC_API_URL="",
        ROOT=str(root),
        TEST_EMAIL=email,
        TEST_PASSWORD=password,
        TEST_IMAGE=str(sample),
    )
    script = tmp_path / "browser.cjs"
    script.write_text(r"""
const {chromium,expect}=require(process.env.ROOT+'/apps/mobile/node_modules/@playwright/test');
const {execFileSync}=require('node:child_process');
(async()=>{
 const browser=await chromium.launch({channel:'chrome',headless:true});
 try {
  const context=await browser.newContext();const page=await context.newPage();
  const bad=[];page.on('response',r=>{if(r.url().includes('capabilities')&&r.status()>=400)bad.push(r.url());});
  const login=await page.request.post('http://127.0.0.1:3040/v1/auth/login',{data:{email:process.env.TEST_EMAIL,password:process.env.TEST_PASSWORD}});expect(login.status()).toBe(200);
  await page.goto('http://127.0.0.1:3040');
  await page.getByRole('button',{name:'照片录卡 / 任务'}).click();
  await page.getByLabel('照片模式').selectOption('back');
  await page.getByLabel('背面照片').setInputFiles(process.env.TEST_IMAGE);
  await page.getByLabel('保留范围').selectOption('slab');
  await page.getByRole('button',{name:'检查背面裁切'}).click();
  await expect(page.getByRole('heading',{name:'确认裁切范围'})).toBeVisible();
  await expect(page.getByLabel('背面照片')).toBeDisabled();
  await expect(page.getByLabel('照片模式')).toBeDisabled();
  await page.getByRole('button',{name:'保留原图',exact:true}).click();
  await expect(page.getByRole('heading',{name:'确认裁切范围'})).toHaveCount(0);
  const uploaded=page.waitForResponse(r=>r.url().endsWith('/v1/capture/jobs')&&r.request().method()==='POST');
  await page.getByRole('button',{name:'提交识别'}).click();const response=await uploaded;expect(response.status()).toBe(202);const job=await response.json();
  expect(job.available_sides).toEqual(['back']);expect(job.front_image_url).toBeNull();expect(job.images.back.crop_recipe.confirmed).toBe(true);
  const thumbnail=await page.request.get('http://127.0.0.1:3040'+job.images.back.thumb_url);expect(thumbnail.status()).toBe(200);
  execFileSync(process.env.ROOT+'/.venv/bin/python',['-c',"import os,psycopg; c=psycopg.connect(os.environ['TEST_DATABASE_URL']); c.execute(\"UPDATE scanner_recognition_job SET status='succeeded' WHERE id=%s\",('"+job.id+"',));c.commit();c.close()"],{env:process.env});
  await expect(page.getByRole('button',{name:'查看任务'})).toBeEnabled({timeout:15000});await page.getByRole('button',{name:'查看任务'}).click();
  await page.getByLabel('球员',{exact:true}).fill('Browser synthetic');await page.getByLabel('品牌',{exact:true}).fill('Test');await page.getByLabel('产品',{exact:true}).fill('Test');await page.getByLabel('赛季起始年').fill('2026');
  await page.getByRole('button',{name:'确认入库',exact:true}).click();await expect(page.getByRole('dialog',{name:'通用照片录卡'})).toHaveCount(0);
  await expect(page.getByText('Browser synthetic',{exact:true}).first()).toBeVisible();
  const image=page.getByAltText('Browser synthetic 卡片背面');await expect(image).toBeVisible();expect(await image.getAttribute('src')).toContain('variant=thumb');expect(bad).toEqual([]);
  await page.screenshot({path:process.env.ROOT+'/work/mobile/web-capture-smoke.png',fullPage:true});
  console.log('WEB_SMOKE_OK: actual capabilities, back-only upload, slab crop/keep original, immutable editing, private thumb, manual confirmation, back-only portfolio thumbnail');
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exit(1);});
""")
    processes = []
    with (tmp_path / "services.log").open("w") as log:
        try:
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
                        "8040",
                    ],
                    cwd=root / "apps/api",
                    env=env,
                    stdout=log,
                    stderr=log,
                )
            )
            processes.append(
                subprocess.Popen(
                    [
                        "node",
                        "node_modules/next/dist/bin/next",
                        "dev",
                        "--port",
                        "3040",
                        "--hostname",
                        "127.0.0.1",
                    ],
                    cwd=root / "apps/web",
                    env=env,
                    stdout=log,
                    stderr=log,
                )
            )
            for url in ("http://127.0.0.1:8040/health/ready", "http://127.0.0.1:3040"):
                for _ in range(90):
                    try:
                        with urllib.request.urlopen(url, timeout=2) as response:
                            if response.status == 200:
                                break
                    except Exception:
                        time.sleep(0.5)
                else:
                    raise RuntimeError((tmp_path / "services.log").read_text())
            result = subprocess.run(
                ["node", str(script)], env=env, capture_output=True, text=True, timeout=120
            )
            assert result.returncode == 0, (
                result.stdout + result.stderr + "\n" + (tmp_path / "services.log").read_text()
            )
            print(result.stdout)
        finally:
            for process in processes:
                process.terminate()
            for process in processes:
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
