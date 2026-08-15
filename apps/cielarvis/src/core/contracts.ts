export type ServiceProbe = {
  enabled?: boolean;
  reachable?: boolean;
  error?: string;
  base_url?: string;
  model?: string;
};

export type SpeechHealth = {
  ok?: boolean;
  services?: Record<string, ServiceProbe>;
};

export type RuntimeSnapshot = {
  connected: boolean;
  endpoint: string;
  error?: string;
  channel?: Record<string, unknown>;
  runtime?: {
    active_client_count?: number;
    active_client_pids?: number[];
  };
  tui?: {
    active_count?: number;
    active?: unknown[];
  };
  speech?: SpeechHealth;
  speech_config?: Record<string, unknown>;
};

export function runtimeAgentReady(snapshot: RuntimeSnapshot | null): boolean {
  return Boolean(
    snapshot?.connected
    && (
      Number(snapshot.runtime?.active_client_count ?? 0) > 0
      || Number(snapshot.tui?.active_count ?? 0) > 0
    )
  );
}

export type ChannelMessage = {
  id: number;
  channel?: string;
  sender_id?: string;
  recipients?: string[] | string;
  kind?: string;
  message?: string;
  created_at?: string;
  meta?: Record<string, unknown>;
};

export type ChannelPage = {
  ok: boolean;
  messages: ChannelMessage[];
  last_id: number;
};

export type RuntimeConnection = {
  endpoint: string;
  token: string;
  workspace: string;
};

export type TerminalKind = "runtime" | "speech" | "shell";

export type TerminalSpawnRequest = {
  title: string;
  kind: TerminalKind;
  program: string;
  args: string[];
  cwd?: string;
  cols: number;
  rows: number;
};

export type TerminalSessionInfo = {
  id: string;
  title: string;
  kind: TerminalKind;
};

export type TerminalOutput = {
  id: string;
  data: string;
};

export type BootstrapPlan = {
  endpoint: string;
  runtime: TerminalSpawnRequest;
  speech_status?: TerminalSpawnRequest;
  speech_login?: TerminalSpawnRequest;
  speech_deploy?: TerminalSpawnRequest;
};

export function voiceNeedsSetup(snapshot: RuntimeSnapshot | null): boolean {
  if (!snapshot?.connected) return false;
  const services = snapshot.speech?.services ?? {};
  const probes = [services.asr, services.tts].filter(Boolean) as ServiceProbe[];
  return probes.length < 2 || probes.some((probe) => !probe.enabled || !probe.reachable);
}
