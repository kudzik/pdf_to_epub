import { execSync } from 'child_process';

let cachedWindowsHostIp: string | null = null;

export function getWindowsHostIp(): string {
  if (cachedWindowsHostIp) return cachedWindowsHostIp;
  try {
    const route = execSync("ip route show default | awk '{print $3}'", { encoding: 'utf-8' }).trim();
    if (route) {
      cachedWindowsHostIp = route;
      return route;
    }
  } catch {}
  return '127.0.0.1';
}

export function getCandidateOllamaUrls(): string[] {
  const urls: string[] = [];
  const custom = process.env.OLLAMA_API_URL || process.env.OLLAMA_HOST;
  if (custom) {
    urls.push(custom.startsWith('http') ? custom : `http://${custom}`);
  }
  urls.push('http://localhost:11434');
  urls.push('http://127.0.0.1:11434');
  const winHost = getWindowsHostIp();
  if (winHost && winHost !== '127.0.0.1') {
    urls.push(`http://${winHost}:11434`);
  }
  return [...new Set(urls)];
}

export interface OllamaProbeResult {
  available: boolean;
  endpoint: string | null;
  models: string[];
}

export async function probeOllama(): Promise<OllamaProbeResult> {
  const candidates = getCandidateOllamaUrls();
  for (const url of candidates) {
    try {
      const controller = new AbortController();
      const timeout = setTimeout(() => controller.abort(), 400);
      const res = await fetch(`${url}/api/tags`, { signal: controller.signal });
      clearTimeout(timeout);
      if (res.ok) {
        const data = await res.json();
        const models = (data.models || []).map((m: any) => m.name);
        return {
          available: true,
          endpoint: url,
          models,
        };
      }
    } catch {}
  }
  return {
    available: false,
    endpoint: null,
    models: [],
  };
}

