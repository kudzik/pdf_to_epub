import type { APIRoute } from 'astro'
import fs from 'node:fs'
import path from 'node:path'
import { getProjectRootDir } from '../../utils/paths'
import { writeRepairs, computeSha256 } from '../../utils/repair-store'

export const POST: APIRoute = async ({ request }) => {
  try {
    const { index, text, original, patches, file = 'ksiazka.md', expectedSha256 } = await request.json()
    if (typeof file !== 'string' || path.basename(file) !== file || !file.endsWith('.md')) {
      throw new Error('Nieprawidłowa nazwa pliku Markdown')
    }
    const mdPath = path.join(getProjectRootDir(), 'output', 'markdown', file)
    if (!fs.existsSync(mdPath)) {
      return Response.json({ success: false, error: `Plik ${file} nie istnieje` }, { status: 404 })
    }
    const updates = patches === undefined ? [{ index, text, original }] : patches
    if (!Array.isArray(updates) || updates.length === 0) {
      throw new Error('Brak poprawek do zapisania')
    }
    const count = writeRepairs(mdPath, updates, expectedSha256)
    const newSha = computeSha256(fs.readFileSync(mdPath, 'utf8'))
    return Response.json({ success: true, count, changed: count > 0, sha256: newSha })
  } catch (error: any) {
    return Response.json({ success: false, error: error.message }, { status: error.status || 400 })
  }
}
