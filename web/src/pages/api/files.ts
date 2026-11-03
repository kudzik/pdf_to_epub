import type { APIRoute } from 'astro';
import fs from 'fs';
import path from 'path';

export const GET: APIRoute = async () => {
  const rootDir = path.resolve(process.cwd(), '..');
  const mdDir = path.join(rootDir, 'output', 'markdown');
  const inputDir = path.join(rootDir, 'input');

  let mdFiles: string[] = [];
  let pdfFiles: string[] = [];

  if (fs.existsSync(mdDir)) {
    mdFiles = fs.readdirSync(mdDir).filter(f => f.endsWith('.md'));
  }
  if (fs.existsSync(inputDir)) {
    pdfFiles = fs.readdirSync(inputDir).filter(f => f.endsWith('.pdf'));
  }

  return new Response(JSON.stringify({ mdFiles, pdfFiles }), {
    headers: { 'Content-Type': 'application/json' },
  });
};

