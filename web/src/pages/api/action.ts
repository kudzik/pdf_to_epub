import type { APIRoute } from 'astro';
import { exec } from 'child_process';
import path from 'path';
import util from 'util';

const execPromise = util.promisify(exec);

export const POST: APIRoute = async ({ request }) => {
  try {
    const { action, file } = await request.json();
    const rootDir = path.resolve(process.cwd(), '..');

    let command = '';
    if (action === 'clean') {
      command = file
        ? `uv run python -m src.pipeline clean --input-md output/markdown/${file}`
        : 'uv run python -m src.pipeline clean';
    } else if (action === 'audit') {
      command = file
        ? `uv run python -m src.pipeline audit --input-md output/markdown/${file}`
        : 'uv run python -m src.pipeline audit';
    } else if (action === 'build') {
      command = file
        ? `uv run python -m src.pipeline build --input-md output/markdown/${file}`
        : 'uv run python -m src.pipeline build';
    } else {
      return new Response(JSON.stringify({ error: 'Nieobsługiwana akcja' }), { status: 400 });
    }

    const { stdout, stderr } = await execPromise(command, { cwd: rootDir });
    return new Response(JSON.stringify({ success: true, stdout, stderr }), {
      headers: { 'Content-Type': 'application/json' },
    });
  } catch (error: any) {
    return new Response(JSON.stringify({ success: false, error: error.message }), {
      status: 500,
      headers: { 'Content-Type': 'application/json' },
    });
  }
};

