import type { APIRoute } from 'astro'
import fs from 'node:fs'
import path from 'node:path'
import { randomUUID } from 'node:crypto'
import { getProjectRootDir } from '../../utils/paths.ts'
import { writeRepairs, computeSha256 } from '../../utils/repair-store.ts'

export const POST: APIRoute = async ({ request }) => {
  try {
    const { index, text, original, patches, file = 'ksiazka.md', expectedSha256, reason = 'Korekta z panelu Web Studio' } = await request.json()
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

    // Zapis do historii decyzji
    if (count > 0) {
      try {
        const book = file.replace(/\.md$/, '')
        const historyDir = path.join(getProjectRootDir(), 'output', 'history')
        fs.mkdirSync(historyDir, { recursive: true })
        const historyPath = path.join(historyDir, `${book}_history.json`)
        let ledger = { ledger_version: '1.0', book_name: book, records: [] as any[], last_updated_at: new Date().toISOString() }
        if (fs.existsSync(historyPath)) {
          try { ledger = JSON.parse(fs.readFileSync(historyPath, 'utf8')) } catch {}
        }
        for (const u of updates) {
          ledger.records.push({
            record_id: randomUUID().slice(0, 12),
            timestamp: new Date().toISOString(),
            book_name: book,
            paragraph_index: u.index,
            paragraph_sha256: computeSha256(u.text || u.original),
            document_sha256_before: expectedSha256 || '',
            document_sha256_after: newSha,
            source_engine: 'web:patch',
            original_text: u.original || '',
            proposed_text: u.text,
            decision: 'applied',
            decision_reason: reason,
          })
        }
        ledger.last_updated_at = new Date().toISOString()
        const tempHist = `${historyPath}.${randomUUID()}.tmp`
        fs.writeFileSync(tempHist, JSON.stringify(ledger, null, 2), 'utf8')
        fs.renameSync(tempHist, historyPath)
      } catch (histErr) {
        console.error('Błąd zapisu historii decyzji:', histErr)
      }
    }

    return Response.json({ success: true, count, changed: count > 0, sha256: newSha })
  } catch (error: any) {
    return Response.json({ success: false, error: error.message }, { status: error.status || 400 })
  }
}
