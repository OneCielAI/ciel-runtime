export type WorkDetail = { session: string; id: string | null; version: number;
  nodes: { id: string; parent_id: string | null; title: string; status: string; note: string; evidence: string }[];
  error: string }
declare module 'claude-code' {
  interface PluginState { 'ciel-work': { detail: WorkDetail | null } }
}
