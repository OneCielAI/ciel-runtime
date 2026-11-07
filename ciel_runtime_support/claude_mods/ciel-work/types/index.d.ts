export type WorkDetail = { session: string; id: string | null; version: number;
  nodes: { id: string; parent_id: string | null; title: string; status: string; note: string; evidence: string;
    ref?: string; waiting_on?: string; urgent?: boolean; priority?: string; phase?: string }[];
  error: string }
export type ObservatoryView = { session: string; tab: 'work' | 'side' | 'settings';
  workTab: 'progress' | 'queue' | 'completed'; page: number; detailPage: number; detailList?: boolean;
  sideReceipt?: string | null; sidePage?: number; sideP1?: boolean;
  settingsPage?: number; settingsKey?: string | null; settingsChoicePage?: number }
declare module 'claude-code' {
  interface PluginState { 'ciel-work': { detail: WorkDetail | null; panel: ObservatoryView | null } }
}
