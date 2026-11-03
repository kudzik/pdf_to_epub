import type { APIRoute } from 'astro';
import fs from 'fs';
import path from 'path';
import { getProjectRootDir } from '../../utils/paths';

export const GET: APIRoute = async ({ url }) => {
  const file = url.searchParams.get('file') || 'ksiazka.epub';
  const rootDir = getProjectRootDir();
  const epubPath = path.join(rootDir, 'output', 'epub', path.basename(file));

  if (!fs.existsSync(epubPath)) {
    return new Response('Plik EPUB nie został jeszcze wygenerowany', { status: 404 });
  }

  const fileBuffer = fs.readFileSync(epubPath);
  return new Response(fileBuffer, {
    headers: {
      'Content-Type': 'application/epub+zip',
      'Content-Disposition': `attachment; filename="${path.basename(file)}"`,
    },
  });
};
