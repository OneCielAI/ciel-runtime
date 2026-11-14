export type WorkDetail = { session: string; id: string | null; version: number;
  nodes: { id: string; parent_id: string | null; title: string; status: string; note: string; evidence: string;
    ref?: string; waiting_on?: string; urgent?: boolean; priority?: string; phase?: string }[];
  error: string }
export type ObservatoryView = { session: string; tab: 'work' | 'side' | 'settings';
  workTab: 'progress' | 'queue' | 'completed'; page: number; detailPage: number; detailList?: boolean;
  sideReceipt?: string | null; sidePage?: number; sideP1?: boolean;
  settingsPage?: number; settingsKey?: string | null; settingsChoicePage?: number }
// One host-held delivery identity (at most 300 chars), plus an optional ref.
// parallel:<lease>: a Work review prompt. wake:<event> and wake:notice:<event>:
// the prompt that wakes an idle main for an appended row; it holds no payload.
// side:<event> and notice:<digest>: the claim an old router's wake or notice
// needs before its idle append. Pending rows fence an unresolved identity;
// terminal rows keep the bounded outcome so the same identity is not resent.
export type PromptPending = { key: string; generation: string; ref?: string; status?: 'pending' | 'terminal';
  outcome?: 'queued' | 'failed' | 'uncertain'; settledAt?: number }
declare module 'claude-code' {
  // legacyWakes: old-router side-wake attempt ids already claimed in this
  // session (newest 200), the cross-instance delivery fence in side-intake.js.
  // promptPending: per-identity prompt registry (at most 200 pending plus 200
  // terminal rows), the cross-reload duplicate fence in side-intake.js.
  interface PluginState { 'ciel-work': { detail: WorkDetail | null; panel: ObservatoryView | null; legacyWakes: string[];
    promptPending: PromptPending[] } }
}
