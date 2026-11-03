import type { APIRoute } from 'astro';
import fs from 'fs';
import path from 'path';
import { getProjectRootDir } from '../../utils/paths';

export const POST: APIRoute = async ({ request }) => {
  try {
    const { index, paragraph, reason, model = 'qwen2.5:14b', file = 'ksiazka.md', apply = false } = await request.json();

    if (!paragraph) {
      return new Response(JSON.stringify({ error: 'Brak tekstu akapitu do naprawy' }), { status: 400 });
    }

    const systemPrompt = `You are a professional culinary editor and OCR reconstruction expert.
You are given a cookbook paragraph with OCR scan corruptions (such as spurious '^' or '°' inserted between letters, merged words like 'ha^been' -> 'has been', broken fractions like '1 2 tsp' -> '1/2 tsp', OCR word confusions like 'die' -> 'the').
Repair the paragraph with 100% precision:
- Remove spurious OCR noise characters (^, °, |, etc.).
- Fix merged or split words.
- Restore proper culinary fractions (e.g. 1/2 tsp, 1/4 cup).
- Preserve exact original meaning, numbers, units, formatting, and language (English or Polish).
- Return ONLY the clean paragraph text, with NO explanations, NO quotes, and NO markdown code fences.`;

    const userPrompt = `Detected OCR anomaly: ${reason || 'OCR corruption'}

Corrupted paragraph:
${paragraph}`;

    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 60000); // 60s timeout for local LLM

    let ollamaResponse;
    try {
      ollamaResponse = await fetch('http://localhost:11434/api/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        signal: controller.signal,
        body: JSON.stringify({
          model: model,
          messages: [
            { role: 'system', content: systemPrompt },
            { role: 'user', content: userPrompt }
          ],
          stream: false,
          options: {
            temperature: 0.1,
          }
        }),
      });
    } catch (err: any) {
      clearTimeout(timeout);
      return new Response(JSON.stringify({
        success: false,
        error: `Ollama nie odpowiada na localhost:11434. Upewnij się, że silnik jest uruchomiony poleceniem: ollama run ${model}`,
      }), { status: 503, headers: { 'Content-Type': 'application/json' } });
    } finally {
      clearTimeout(timeout);
    }

    if (!ollamaResponse.ok) {
      const errText = await ollamaResponse.text();
      return new Response(JSON.stringify({
        success: false,
        error: `Błąd Ollama (${ollamaResponse.status}): ${errText}`,
      }), { status: 500, headers: { 'Content-Type': 'application/json' } });
    }

    const data = await ollamaResponse.json();
    let repairedText = data.message?.content?.trim() || '';

    // Usuwanie ewentualnych znaczników markdown code fence jeśli model dodał
    if (repairedText.startsWith('```') && repairedText.endsWith('```')) {
      repairedText = repairedText.replace(/^```[a-z]*\n/, '').replace(/\n```$/, '').trim();
    }

    // Jeśli poproszono o natychmiastowe zaaplikowanie do pliku markdown
    if (apply && index !== undefined) {
      const rootDir = getProjectRootDir();
      const cleanFileName = path.basename(file);
      const mdPath = path.join(rootDir, 'output', 'markdown', cleanFileName);

      if (fs.existsSync(mdPath)) {
        const bakPath = mdPath + '.bak';
        if (!fs.existsSync(bakPath)) {
          fs.copyFileSync(mdPath, bakPath);
        }

        const content = fs.readFileSync(mdPath, 'utf-8');
        const paragraphs = content.split('\n\n');
        const pIdx = parseInt(index, 10);
        if (pIdx >= 0 && pIdx < paragraphs.length) {
          paragraphs[pIdx] = repairedText;
          fs.writeFileSync(mdPath, paragraphs.join('\n\n'), 'utf-8');
        }
      }
    }

    return new Response(JSON.stringify({
      success: true,
      index,
      original: paragraph,
      repaired: repairedText,
      applied: apply,
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

