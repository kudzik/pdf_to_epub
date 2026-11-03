import fs from 'fs';
import path from 'path';

/**
 * Zwraca bezwzględną ścieżkę do głównego katalogu projektu (pdf_to_epub),
 * niezależnie od tego, czy serwer Astro został uruchomiony z katalogu głównego,
 * czy z podkatalogu web/.
 */
export function getProjectRootDir(): string {
  const cwd = process.cwd();

  // 1. Sprawdź, czy jesteśmy bezpośrednio w głównym katalogu projektu
  if (
    fs.existsSync(path.join(cwd, 'pyproject.toml')) ||
    fs.existsSync(path.join(cwd, 'output', 'markdown'))
  ) {
    return cwd;
  }

  // 2. Sprawdź, czy jesteśmy w podkatalogu web/
  const parent = path.resolve(cwd, '..');
  if (
    fs.existsSync(path.join(parent, 'pyproject.toml')) ||
    fs.existsSync(path.join(parent, 'output', 'markdown'))
  ) {
    return parent;
  }

  // Fallback do cwd
  return cwd;
}

