import type { APIRoute } from 'astro';
import fs from 'fs';
import path from 'path';
import { getProjectRootDir } from '../../utils/paths';

export const POST: APIRoute = async ({ request }) => {
  try {
    const body = await request.json();
    const { index, text, patches, file = 'ksiazka.md' } = body;
    const rootDir = getProjectRootDir();
    const cleanFileName = path.basename(file);
    const mdPath = path.join(rootDir, 'output', 'markdown', cleanFileName);

    if (!fs.existsSync(mdPath)) {
      return new Response(JSON.stringify({ error: `Plik ${cleanFileName} nie istnieje` }), { status: 404 });
    }

    // Tworzenie kopii zapasowej .bak przed modyfikacją
    const bakPath = mdPath + '.bak';
    if (!fs.existsSync(bakPath)) {
      fs.copyFileSync(mdPath, bakPath);
    }

    const content = fs.readFileSync(mdPath, 'utf-8');
    const paragraphs = content.split('\n\n');

    let updatedCount = 0;

    if (Array.isArray(patches) && patches.length > 0) {
      // Obsługa wsadowa (Batch patch)
      for (const item of patches) {
        const pIdx = parseInt(item.index, 10);
        if (!isNaN(pIdx) && pIdx >= 0 && pIdx < paragraphs.length && item.text !== undefined) {
          paragraphs[pIdx] = item.text.trim();
          updatedCount++;
        }
      }
    } else if (index !== undefined && text !== undefined) {
      // Obsługa pojedynczego akapitu
      const pIdx = parseInt(index, 10);
      if (isNaN(pIdx) || pIdx < 0 || pIdx >= paragraphs.length) {
        return new Response(JSON.stringify({ error: `Nieprawidłowy indeks akapitu: ${index}` }), { status: 400 });
      }
      paragraphs[pIdx] = text.trim();
      updatedCount = 1;
    } else {
      return new Response(JSON.stringify({ error: 'Brak danych do zaaplikowania (wymagany index+text lub patches)' }), { status: 400 });
    }

    fs.writeFileSync(mdPath, paragraphs.join('\n\n'), 'utf-8');

    return new Response(JSON.stringify({ success: true, count: updatedCount }), {
      headers: { 'Content-Type': 'application/json' },
    });
  } catch (error: any) {
    return new Response(JSON.stringify({ success: false, error: error.message }), {
      status: 500,
      headers: { 'Content-Type': 'application/json' },
    });
  }
};
