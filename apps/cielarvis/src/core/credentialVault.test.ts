import { describe, expect, it } from "vitest";
import { CIELARVIS_VAULT_CONSENT, permitsCredentialRelease, type VaultUseRequest } from "./credentialVault";

describe("CIELARVIS vault consent boundary", () => {
  const request: VaultUseRequest = {
    schema: CIELARVIS_VAULT_CONSENT,
    requestId: "request-1",
    credentialId: "credential-1",
    appId: "ai.oneciel.cielarvis.browser",
    origin: "https://example.com",
    purpose: "Sign in",
  };

  it("requires an explicit matching approval", () => {
    expect(permitsCredentialRelease(request, { ...request, decision: "allow_once" })).toBe(true);
    expect(permitsCredentialRelease(request, { ...request, origin: "https://evil.test", decision: "allow_once" })).toBe(false);
    expect(permitsCredentialRelease(request, { ...request, decision: "deny" })).toBe(false);
  });
});
