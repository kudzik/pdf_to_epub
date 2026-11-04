import type { APIRoute } from 'astro';
import fs from 'node:fs';
import path from 'node:path';
import { execFile } from 'node:child_process';
import sharp from 'sharp';
import { getProjectRootDir } from '../../utils/paths.ts';

export const GET: APIRoute = async ({ url }) => {
  try {
    const rootDir = getProjectRootDir();
    const book = url.searchParams.get('book') || 'ksiazka';
    const cleanBook = path.basename(book).replace(/\.pdf$/, '');
    const pageStr = url.searchParams.get('page');
    const cropParam = url.searchParams.get('crop'); // e.g. "x,y,w,h" or "0.1,0.2,0.8,0.5"

    if (!pageStr || !/^\d+$/.test(pageStr)) {
      return new Response(JSON.stringify({ error: 'Wymagany parametr ?page=<numer_strony>' }), {
        status: 400,
        headers: { 'Content-Type': 'application/json' },
      });
    }

    const pageNo = parseInt(pageStr, 10);
    const pdfPath = path.join(rootDir, 'input', `${cleanBook}.pdf`);

    if (!fs.existsSync(pdfPath)) {
      return new Response(JSON.stringify({ error: `Plik PDF input/${cleanBook}.pdf nie istnieje` }), {
        status: 404,
        headers: { 'Content-Type': 'application/json' },
      });
    }

    // 1. Check existing fixture images
    let fullImageBuffer: Buffer | null = null;
    const fixturePadded = path.join(rootDir, 'benchmarks', 'fixtures', 'images', `page_${String(pageNo).padStart(3, '0')}.png`);
    if (fs.existsSync(fixturePadded)) {
      fullImageBuffer = fs.readFileSync(fixturePadded);
    }

    // 2. Check candidate cache
    const cacheDir = path.join(rootDir, 'output', 'cache', 'images');
    fs.mkdirSync(cacheDir, { recursive: true });
    const cachedImagePath = path.join(cacheDir, `${cleanBook}_p${pageNo}.png`);

    if (!fullImageBuffer && fs.existsSync(cachedImagePath)) {
      fullImageBuffer = fs.readFileSync(cachedImagePath);
    }

    // 3. Render page on demand using pypdfium2 via Python CLI if not yet rendered
    if (!fullImageBuffer) {
      const pythonBin = path.join(rootDir, '.venv', 'bin', 'python');
      const pyScript = `
import sys, pypdfium2 as pdfium
from pathlib import Path

pdf_path = sys.argv[1]
page_no = int(sys.argv[2])
out_path = sys.argv[3]

doc = pdfium.PdfDocument(pdf_path)
if page_no < 1 or page_no > len(doc):
    sys.exit(2)

page = doc[page_no - 1]
bitmap = page.render(scale=2.0)
pil_img = bitmap.to_pil()
pil_img.save(out_path, format="PNG")
page.close()
doc.close()
`;

      await new Promise<void>((resolve, reject) => {
        execFile(
          pythonBin,
          ['-c', pyScript, pdfPath, String(pageNo), cachedImagePath],
          { timeout: 15_000 },
          (error, stdout, stderr) => {
            if (error) {
              return reject(new Error(stderr || error.message));
            }
            resolve();
          }
        );
      });

      if (fs.existsSync(cachedImagePath)) {
        fullImageBuffer = fs.readFileSync(cachedImagePath);
      }
    }

    if (!fullImageBuffer) {
      return new Response(JSON.stringify({ error: 'Nie udało się wygenerować obrazu strony' }), {
        status: 500,
        headers: { 'Content-Type': 'application/json' },
      });
    }

    // 4. Handle optional crop
    if (cropParam) {
      const parts = cropParam.split(',').map((p) => parseFloat(p.trim()));
      if (parts.length === 4 && parts.every((v) => !isNaN(v) && v >= 0)) {
        const [rawX, rawY, rawW, rawH] = parts;
        const meta = await sharp(fullImageBuffer).metadata();
        const imgW = meta.width || 1000;
        const imgH = meta.height || 1000;

        let left = rawX <= 1.0 ? Math.round(rawX * imgW) : Math.round(rawX);
        let top = rawY <= 1.0 ? Math.round(rawY * imgH) : Math.round(rawY);
        let width = rawW <= 1.0 ? Math.round(rawW * imgW) : Math.round(rawW);
        let height = rawH <= 1.0 ? Math.round(rawH * imgH) : Math.round(rawH);

        left = Math.max(0, Math.min(left, imgW - 1));
        top = Math.max(0, Math.min(top, imgH - 1));
        width = Math.max(1, Math.min(width, imgW - left));
        height = Math.max(1, Math.min(height, imgH - top));

        const croppedBuffer = await sharp(fullImageBuffer)
          .extract({ left, top, width, height })
          .png()
          .toBuffer();

        return new Response(croppedBuffer, {
          headers: {
            'Content-Type': 'image/png',
            'Cache-Control': 'public, max-age=3600',
          },
        });
      }
    }

    return new Response(fullImageBuffer, {
      headers: {
        'Content-Type': 'image/png',
        'Cache-Control': 'public, max-age=3600',
      },
    });
  } catch (error: any) {
    return new Response(JSON.stringify({ error: error.message || 'Wewnętrzny błąd serwera' }), {
      status: 500,
      headers: { 'Content-Type': 'application/json' },
    });
  }
};
