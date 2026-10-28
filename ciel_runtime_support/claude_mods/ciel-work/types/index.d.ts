export type WorkDetail = { session: string; id: string | null; version: number;
  nodes: { id: string; parent_id: string | null; title: string; status: string; note: string; evidence: string;
    ref?: string; waiting_on?: string; urgent?: boolean }[];
  error: string }
declare module 'claude-code' {
  interface PluginState { 'ciel-work': { detail: WorkDetail | null; panel: { session: string;
    tab: 'progress' | 'queue' | 'completed'; page: number; detailPage: number; detailList?: boolean } | null } }
}
