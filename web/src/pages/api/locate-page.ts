import type { APIRoute } from 'astro';
import fs from 'node:fs';
import path from 'node:path';
import { execFile } from 'node:child_process';
import { getProjectRootDir } from '../../utils/paths.ts';

export const GET: APIRoute = async ({ url }) => {
  try {
    const rootDir = getProjectRootDir();
    const book = url.searchParams.get('book') || 'ksiazka';
    const cleanBook = path.basename(book).replace(/\.pdf$/, '');
    const indexStr = url.searchParams.get('index');
    const snippet = url.searchParams.get('snippet') || '';

    const pdfPath = path.join(rootDir, 'input', `${cleanBook}.pdf`);
    const mdPath = path.join(rootDir, 'output', 'markdown', `${cleanBook}.md`);

    if (!fs.existsSync(pdfPath)) {
      return new Response(JSON.stringify({ error: `Plik PDF input/${cleanBook}.pdf nie istnieje` }), {
        status: 404,
        headers: { 'Content-Type': 'application/json' },
      });
    }

    let queryText = snippet;
    if (!queryText && fs.existsSync(mdPath) && indexStr && /^\d+$/.test(indexStr)) {
      const pIdx = parseInt(indexStr, 10);
      const content = fs.readFileSync(mdPath, 'utf8');
      const paragraphs = content.split('\n\n');
      if (pIdx >= 0 && pIdx < paragraphs.length) {
        queryText = paragraphs[pIdx].slice(0, 300);
      }
    }

    if (!queryText || queryText.trim().length < 5) {
      return new Response(JSON.stringify({
        page: 1,
        confidence: 0,
        reason: 'Zbyt krótki fragment tekstu do lokalizacji',
      }), {
        headers: { 'Content-Type': 'application/json' },
      });
    }

    const pythonBin = path.join(rootDir, '.venv', 'bin', 'python');
    const pyScript = `
import sys, json, re
import pypdfium2 as pdfium
from pathlib import Path
from difflib import SequenceMatcher

pdf_path = sys.argv[1]
query = sys.argv[2]

try:
    doc = pdfium.PdfDocument(pdf_path)
    total = len(doc)
    
    # Extract significant query words
    q_words = [w.lower() for w in re.findall(r'\\b[a-zA-ZąćęłńóśźżĄĆĘŁŃÓŚŹŻ]{3,}\\b', query)]
    if not q_words:
        q_words = [w.lower() for w in re.findall(r'\\w+', query) if len(w) >= 2]
    
    best_page = 1
    best_score = 0
    
    # Fast text search across pages
    for i in range(total):
        page = doc[i]
        tp = page.get_textpage()
        text = tp.get_text_range().lower()
        tp.close()
        page.close()
        
        matches = sum(1 for w in q_words if w in text)
        score = matches / max(1, len(q_words))
        if score > best_score:
            best_score = score
            best_page = i + 1
            if score >= 0.8:
                break
                
    doc.close()
    print(json.dumps({"page": best_page, "score": round(best_score, 2), "total_pages": total}))
except Exception as e:
    print(json.dumps({"error": str(e), "page": 1, "score": 0}))
`;

    const result: any = await new Promise((resolve) => {
      execFile(
        pythonBin,
        ['-c', pyScript, pdfPath, queryText.slice(0, 500)],
        { timeout: 10_000 },
        (error, stdout, stderr) => {
          if (error) {
            return resolve({ page: 1, score: 0, error: stderr || error.message });
          }
          try {
            resolve(JSON.parse(stdout.trim()));
          } catch {
            resolve({ page: 1, score: 0 });
          }
        }
      );
    });

    return new Response(JSON.stringify({
      success: true,
      page: result.page || 1,
      confidence: result.score || 0,
      totalPages: result.total_pages || 914,
    }), {
      headers: { 'Content-Type': 'application/json' },
    });
  } catch (error: any) {
    return new Response(JSON.stringify({ success: false, error: error.message }), {
      status: 500,
      headers: { 'Content-Type': 'application/json' },
    });
  }
};
