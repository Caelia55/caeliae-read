const LIVE_RUNTIME_HOSTS = new Set(["127.0.0.1", "localhost"]);

export function requireAcceptanceBaseURL() {
  const raw = process.env.CAELIAE_READ_ACCEPTANCE_URL;
  if (!raw) {
    throw new Error(
      "CAELIAE_READ_ACCEPTANCE_URL is required; acceptance will not default to a live runtime",
    );
  }

  let parsed;
  try {
    parsed = new URL(raw);
  } catch (error) {
    throw new Error("CAELIAE_READ_ACCEPTANCE_URL must be an absolute HTTP(S) URL", { cause: error });
  }
  if (parsed.protocol !== "http:" && parsed.protocol !== "https:") {
    throw new Error("CAELIAE_READ_ACCEPTANCE_URL must use HTTP(S)");
  }

  const isLiveRuntime = parsed.port === "8765" && LIVE_RUNTIME_HOSTS.has(parsed.hostname);
  if (isLiveRuntime && process.env.CAELIAE_READ_ALLOW_LIVE !== "1") {
    throw new Error(
      "Refusing live 8765 acceptance without explicit CAELIAE_READ_ALLOW_LIVE=1",
    );
  }
  if (!isLiveRuntime && !process.env.CAELIAE_READ_DATA_ROOT) {
    throw new Error(
      "CAELIAE_READ_DATA_ROOT is required for isolated acceptance",
    );
  }

  return raw.replace(/\/$/, "");
}
