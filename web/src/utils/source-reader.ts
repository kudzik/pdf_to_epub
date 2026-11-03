import { execFile } from 'node:child_process';
import path from 'node:path';
import { getProjectRootDir } from './paths';

export interface SourceEvidence { text: string; pages: number[]; confidence: number; coverage: number; method: string }

export function readSource(file: string, fragment: string, signal: AbortSignal, context: string[] = []): Promise<SourceEvidence> {
  const root = getProjectRootDir();
  return new Promise((resolve, reject) => {
    const child = execFile(path.join(root, '.venv', 'bin', 'python'), ['-m', 'src.source_ocr'],
      { cwd: root, signal, timeout: 180_000, maxBuffer: 4 * 1024 * 1024 }, (error, stdout, stderr) => {
        try {
          const result = JSON.parse(stdout);
          if (result.error) return reject(new Error(result.error));
          if (error) return reject(error);
          resolve(result);
        } catch {
          reject(new Error(error ? `Nie udało się odczytać źródła PDF: ${error.message}` : `Nieprawidłowa odpowiedź odczytu PDF: ${stderr.slice(-300)}`));
        }
      });
    child.stdin?.end(JSON.stringify({ file, fragment, context }));
  });
}
