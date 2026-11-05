import type { APIRoute } from 'astro';
import path from 'node:path';
import { getProjectRootDir } from '../../utils/paths';
import { probeOllama, getWindowsHostIp } from '../../utils/ollama';
import { readBookState } from '../../utils/book-state';

export const GET: APIRoute = async ({ url }) => {
  const book = path.basename(url.searchParams.get('file') || url.searchParams.get('book') || 'ksiazka').replace(/\.(md|pdf|epub)$/, '');
  const state = readBookState(getProjectRootDir(), book);
  const ollama = await probeOllama();
  return Response.json({ ...state, repairVersion: 'source-ocr-v1',
    ollama: { ...ollama, windowsHostIp: getWindowsHostIp() } }, { headers: { 'Cache-Control': 'no-store' } });
};
// extractProgress is read from extract_progress_<book>.json by readBookState.
