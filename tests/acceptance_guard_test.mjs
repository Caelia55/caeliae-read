import assert from "node:assert/strict";
import { requireAcceptanceBaseURL } from "./acceptance_guard.mjs";

const originalURL = process.env.CAELIAE_READ_ACCEPTANCE_URL;
const originalDataRoot = process.env.CAELIAE_READ_DATA_ROOT;
const originalAllowLive = process.env.CAELIAE_READ_ALLOW_LIVE;

try {
  delete process.env.CAELIAE_READ_ACCEPTANCE_URL;
  assert.throws(() => requireAcceptanceBaseURL(), /is required/);

  process.env.CAELIAE_READ_ACCEPTANCE_URL = "http://127.0.0.1:8766/";
  delete process.env.CAELIAE_READ_DATA_ROOT;
  assert.throws(() => requireAcceptanceBaseURL(), /DATA_ROOT/);
  process.env.CAELIAE_READ_DATA_ROOT = "isolated-test-root";
  assert.equal(requireAcceptanceBaseURL(), "http://127.0.0.1:8766");

  process.env.CAELIAE_READ_ACCEPTANCE_URL = "http://127.0.0.1:8765";
  delete process.env.CAELIAE_READ_ALLOW_LIVE;
  assert.throws(() => requireAcceptanceBaseURL(), /CAELIAE_READ_ALLOW_LIVE/);

  process.env.CAELIAE_READ_ALLOW_LIVE = "1";
  assert.equal(requireAcceptanceBaseURL(), "http://127.0.0.1:8765");
  console.log("acceptance guard checks passed");
} finally {
  if (originalURL === undefined) delete process.env.CAELIAE_READ_ACCEPTANCE_URL;
  else process.env.CAELIAE_READ_ACCEPTANCE_URL = originalURL;
  if (originalDataRoot === undefined) delete process.env.CAELIAE_READ_DATA_ROOT;
  else process.env.CAELIAE_READ_DATA_ROOT = originalDataRoot;
  if (originalAllowLive === undefined) delete process.env.CAELIAE_READ_ALLOW_LIVE;
  else process.env.CAELIAE_READ_ALLOW_LIVE = originalAllowLive;
}
