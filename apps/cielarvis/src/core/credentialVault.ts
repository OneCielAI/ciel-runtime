export const CIELARVIS_VAULT_CONSENT = "ai.oneciel.cielarvis.vault-consent/v1" as const;

export type VaultCredentialReference = {
  id: string;
  label: string;
  origin: string;
  usernameHint?: string;
};

export type VaultUseRequest = {
  schema: typeof CIELARVIS_VAULT_CONSENT;
  requestId: string;
  credentialId: string;
  appId: string;
  origin: string;
  purpose: string;
};

export type VaultUseDecision = {
  requestId: string;
  decision: "deny" | "allow_once" | "allow_session";
  credentialId: string;
  appId: string;
  origin: string;
};

export function permitsCredentialRelease(request: VaultUseRequest, decision: VaultUseDecision): boolean {
  return decision.requestId === request.requestId
    && decision.credentialId === request.credentialId
    && decision.appId === request.appId
    && decision.origin === request.origin
    && decision.decision !== "deny";
}
