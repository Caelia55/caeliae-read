import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { readFile, readdir } from "node:fs/promises";
import { spawnSync } from "node:child_process";
import { join, relative } from "node:path";
import { chromium } from "../web/node_modules/playwright-core/index.mjs";
import { requireAcceptanceBaseURL } from "./acceptance_guard.mjs";

const baseURL = requireAcceptanceBaseURL();
const dataRoot = process.env.CAELIAE_READ_DATA_ROOT;
const pdfPath = process.env.CAELIAE_READ_ACCEPTANCE_PDF;
if (!dataRoot || !pdfPath) throw new Error("isolated data root and synthetic PDF are required");

async function snapshotDirectory(root) {
  const result = {};
  async function visit(directory) {
    for (const entry of await readdir(directory, { withFileTypes: true })) {
      const path = join(directory, entry.name);
      const key = relative(root, path);
      if (entry.isDirectory()) await visit(path);
      else result[key] = createHash("sha256").update(await readFile(path)).digest("hex");
    }
  }
  await visit(root);
  return result;
}

function mutateBundle(bundle, mode) {
  const python = process.env.CAELIAE_READ_PYTHON ?? join(process.cwd(), ".venv", "Scripts", "python.exe");
  const script = String.raw`
import hashlib, io, json, sys, zipfile
mode = sys.argv[1]
source = sys.stdin.buffer.read()
with zipfile.ZipFile(io.BytesIO(source)) as archive:
    members = {name: archive.read(name) for name in archive.namelist()}
manifest = json.loads(members["manifest.json"])
if mode == "unsupported":
    manifest["version"] = "2.0"
elif mode == "integrity":
    members["paper.pdf"] += b"\n%tampered"
elif mode == "metadata-conflict":
    metadata = json.loads(members["metadata.json"])
    metadata["title"] = "Incoming title"
    members["metadata.json"] = (json.dumps(metadata, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
    manifest["files"] = [item for item in manifest["files"] if item["path"] != "citation.bib"]
    members.pop("citation.bib", None)
    for item in manifest["files"]:
        if item["path"] == "metadata.json":
            item["size_bytes"] = len(members["metadata.json"])
            item["sha256"] = hashlib.sha256(members["metadata.json"]).hexdigest()
manifest_bytes = (json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
members["manifest.json"] = manifest_bytes
output = io.BytesIO()
with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
    for name, data in members.items():
        archive.writestr(name, data)
sys.stdout.buffer.write(output.getvalue())
`;
  const result = spawnSync(python, ["-c", script, mode], { input: bundle });
  assert.equal(result.status, 0, result.stderr.toString());
  return result.stdout;
}

const browser = await chromium.launch({
  headless: true,
  executablePath: process.env.CAELIAE_READ_BROWSER_PATH ?? "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe",
});

try {
  const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
  const pdf = await readFile(pdfPath);
  const upload = async (name, suffix) => {
    const response = await page.request.post(`${baseURL}/api/papers`, {
      multipart: { file: { name, mimeType: "application/pdf", buffer: Buffer.concat([pdf, Buffer.from(`\n% ${suffix}`)]) } },
    });
    assert.equal(response.status(), 201);
    return response.json();
  };
  const firstUpload = await upload("preview-ui-first.pdf", "preview-ui-first");
  const secondUpload = await upload("preview-ui-second.pdf", "preview-ui-second");
  const firstPaperId = (await firstUpload).paper.paper_id;
  const secondPaperId = (await secondUpload).paper.paper_id;
  const firstBundle = await (await page.request.get(`${baseURL}/api/papers/${firstPaperId}/bundle`)).body();
  const secondBundle = await (await page.request.get(`${baseURL}/api/papers/${secondPaperId}/bundle`)).body();

  await page.goto(`${baseURL}/reader/`);
  await page.getByRole("button", { name: /preview-ui-first\.pdf/ }).click();
  await page.getByText("1 / 2").waitFor();
  await page.getByTestId("paper-bundle-preview-open").click();
  assert.equal(await page.getByTestId("paper-list-view").count(), 0);
  assert.equal(await page.getByTestId("paper-bundle-preview-panel").count(), 1);

  const input = page.locator('input[aria-label="选择研究包文件"]');
  let previewRequests = 0;
  const requestListener = (request) => {
    if (request.method() === "POST" && new URL(request.url()).pathname === "/api/paper-bundles/preview") previewRequests += 1;
  };
  page.on("request", requestListener);
  await input.setInputFiles({ name: "first.caeliae-paper.zip", mimeType: "application/zip", buffer: firstBundle });
  await page.getByTestId("bundle-preview-success").waitFor();
  assert.equal(previewRequests, 1, "one selected file must issue exactly one preview request");
  assert.match(await page.getByTestId("bundle-preview-target").textContent(), /本地已存在同一份 PDF/);
  assert.match(await page.getByTestId("bundle-preview-metadata").textContent(), /研究包中没有文献信息/);
  assert.match(await page.getByTestId("bundle-preview-批注").textContent(), /研究包中没有批注/);
  assert.match(await page.getByTestId("bundle-preview-derived").textContent(), /无 BibTeX 文件/);
  assert.match(await page.getByTestId("bundle-preview-importability").textContent(), /未发现阻塞冲突/);
  assert.match(await page.getByText("当前页面仅预览，不会写入数据。", { exact: true }).textContent(), /仅预览/);
  page.off("request", requestListener);

  const localMetadata = { title: "Local title", authors: null, publication_year: null, venue: null, doi: null, arxiv_id: null, citation_key: null };
  assert.equal((await page.request.put(`${baseURL}/api/papers/${firstPaperId}/metadata`, { data: localMetadata })).status(), 200);
  const localBundle = await (await page.request.get(`${baseURL}/api/papers/${firstPaperId}/bundle`)).body();
  const conflictBundle = mutateBundle(localBundle, "metadata-conflict");
  await input.setInputFiles({ name: "conflict.caeliae-paper.zip", mimeType: "application/zip", buffer: conflictBundle });
  await page.getByTestId("bundle-preview-success").waitFor();
  assert.match(await page.getByTestId("bundle-preview-metadata").textContent(), /文献信息存在差异/);
  assert.match(await page.getByTestId("bundle-preview-metadata").textContent(), /本地：Local title/);
  assert.match(await page.getByTestId("bundle-preview-metadata").textContent(), /研究包：Incoming title/);
  assert.match(await page.getByTestId("bundle-preview-importability").textContent(), /发现需要处理的冲突/);
  assert.equal(await page.getByRole("button", { name: /确认导入|开始导入|立即导入|保存/ }).count(), 0);

  const errorCases = [
    { name: "invalid.zip", data: Buffer.from("not a zip"), message: "这不是有效的 Caeliae 研究包" },
    { name: "unsupported.zip", data: mutateBundle(localBundle, "unsupported"), message: "研究包版本暂不支持" },
    { name: "integrity.zip", data: mutateBundle(localBundle, "integrity"), message: "研究包文件完整性校验失败" },
  ];
  for (const errorCase of errorCases) {
    await input.setInputFiles({ name: errorCase.name, mimeType: "application/zip", buffer: errorCase.data });
    await page.getByTestId("bundle-preview-error").waitFor();
    const errorText = await page.getByTestId("bundle-preview-error").textContent();
    assert.match(errorText, new RegExp(errorCase.message));
    assert.doesNotMatch(errorText, /bundle_schema_error|traceback|data\\\\|Users/);
  }

  const beforePreview = await snapshotDirectory(dataRoot);
  let releaseFirst;
  let routeCount = 0;
  await page.route(`${baseURL}/api/paper-bundles/preview`, async (route) => {
    routeCount += 1;
    if (routeCount === 1) {
      await new Promise((resolve) => {
        releaseFirst = resolve;
      });
      try { await route.continue(); } catch { /* the UI may abort the stale request */ }
      return;
    }
    await route.continue();
  });
  const staleInput = page.locator('input[aria-label="选择研究包文件"]');
  await staleInput.setInputFiles({ name: "stale-a.zip", mimeType: "application/zip", buffer: firstBundle });
  await new Promise((resolve) => {
    const poll = () => routeCount > 0 ? resolve() : setTimeout(poll, 10);
    poll();
  });
  await staleInput.setInputFiles({ name: "fresh-b.zip", mimeType: "application/zip", buffer: secondBundle });
  await page.getByTestId("bundle-preview-success").waitFor();
  assert.match(await page.getByTestId("bundle-preview-file").textContent(), /fresh-b\.zip/);
  releaseFirst?.();
  await page.waitForTimeout(350);
  assert.match(await page.getByTestId("bundle-preview-file").textContent(), /fresh-b\.zip/);
  await page.unroute(`${baseURL}/api/paper-bundles/preview`);

  let releaseClosedRequest;
  let closeRouteSeen = false;
  await page.route(`${baseURL}/api/paper-bundles/preview`, async (route) => {
    closeRouteSeen = true;
    await new Promise((resolve) => {
      releaseClosedRequest = resolve;
    });
    try { await route.continue(); } catch { /* close may abort the request */ }
  });
  await staleInput.setInputFiles({ name: "closed-pending.zip", mimeType: "application/zip", buffer: firstBundle });
  await new Promise((resolve) => {
    const poll = () => closeRouteSeen ? resolve() : setTimeout(poll, 10);
    poll();
  });
  await page.getByRole("button", { name: "返回", exact: true }).click();
  await page.getByTestId("paper-list-view").waitFor();
  releaseClosedRequest?.();
  await page.waitForTimeout(350);
  assert.equal(await page.getByTestId("paper-bundle-preview-panel").count(), 0);
  await page.unroute(`${baseURL}/api/paper-bundles/preview`);

  const afterPreview = await snapshotDirectory(dataRoot);
  assert.deepEqual(afterPreview, beforePreview, "preview UI changed isolated data files");

  await page.getByTestId("paper-bundle-preview-open").click();
  await input.setInputFiles({ name: "narrow.zip", mimeType: "application/zip", buffer: secondBundle });
  await page.getByTestId("bundle-preview-success").waitFor();
  await page.setViewportSize({ width: 390, height: 844 });
  await page.waitForTimeout(200);
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth), true);

  console.log(JSON.stringify({
    entry_and_exclusive_view: true,
    valid_existing_preview: true,
    metadata_conflict_diff: true,
    validation_error_mapping: true,
    stale_request_protection: true,
    preview_close_stale_protection: true,
    zero_write_snapshot: true,
    narrow_no_horizontal_overflow: true,
  }, null, 2));
} finally {
  await browser.close();
}
