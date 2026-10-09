self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (event) => event.waitUntil(self.clients.claim()));
self.addEventListener("fetch", () => {
  // v0 intentionally performs no offline caching. The service-worker boundary is
  // present so a later HTTPS deployment can add an explicit cache policy.
});
