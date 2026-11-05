import type { APIRoute } from 'astro';
import path from 'node:path';
import fs from 'node:fs';
import { getProjectRootDir } from '../../utils/paths';
import { readJobs } from '../../utils/job-store';
export const GET: APIRoute = ({url}) => {
  const root=getProjectRootDir();
  const book=path.basename(url.searchParams.get('book') || 'ksiazka');
  let extractProgress=null;
  try { extractProgress=JSON.parse(fs.readFileSync(path.join(root,'output',`extract_progress_${book}.json`),'utf8')); } catch {}
  return Response.json({...readJobs(root,book),extractProgress},{headers:{'Cache-Control':'no-store'}});
};
