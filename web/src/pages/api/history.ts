import type { APIRoute } from 'astro'
import fs from 'node:fs'
import path from 'node:path'
import { randomUUID, createHash } from 'node:crypto'
import { getProjectRootDir } from '../../utils/paths.ts'

const computeSha = (text: string): string => {
  const clean = text.trim().replace(/\r\n/g, '\n')
  return createHash('sha256').update(clean).digest('hex')
}

export const GET: APIRoute = async ({ url }) => {
  try {
    const rootDir = getProjectRootDir()
    const book = url.searchParams.get('book') || 'ksiazka'
    const historyPath = path.join(rootDir, 'output', 'history', `${book}_history.json`)
    const mdPath = path.join(rootDir, 'output', 'markdown', `${book}.md`)

    let ledger = { ledger_version: '1.0', book_name: book, records: [] as any[] }
    if (fs.existsSync(historyPath)) {
      try {
        ledger = JSON.parse(fs.readFileSync(historyPath, 'utf8'))
      } catch {}
    }

    const anchors: Record<string, any> = {}
    if (fs.existsSync(mdPath) && ledger.records.length > 0) {
      const content = fs.readFileSync(mdPath, 'utf8')
      const paragraphs = content.split('\n\n')
      const hashes = paragraphs.map(p => computeSha(p))
      const hashToIndices: Record<string, number[]> = {}
      hashes.forEach((h, idx) => {
        if (!hashToIndices[h]) hashToIndices[h] = []
        hashToIndices[h].push(idx)
      })

      for (const rec of ledger.records) {
        const recordedIdx = rec.paragraph_index
        const recordedHash = rec.paragraph_sha256

        if (recordedIdx >= 0 && recordedIdx < paragraphs.length && hashes[recordedIdx] === recordedHash) {
          anchors[rec.record_id] = {
            status: 'valid',
            is_anchored: true,
            current_index: recordedIdx,
            shift_offset: 0,
          }
        } else if (hashToIndices[recordedHash]?.length) {
          const newIdx = hashToIndices[recordedHash][0]
          anchors[rec.record_id] = {
            status: 'shifted',
            is_anchored: false,
            current_index: newIdx,
            shift_offset: newIdx - recordedIdx,
          }
        } else {
          anchors[rec.record_id] = {
            status: 'displaced',
            is_anchored: false,
            current_index: null,
            shift_offset: null,
          }
        }
      }
    }

    return Response.json({ success: true, book, ledger, anchors })
  } catch (error: any) {
    return Response.json({ success: false, error: error.message }, { status: 500 })
  }
}

export const POST: APIRoute = async ({ request }) => {
  try {
    const rootDir = getProjectRootDir()
    const body = await request.json()
    const {
      book = 'ksiazka',
      index,
      original = '',
      text = '',
      decision = 'proposed',
      reason = '',
      engine = 'studio',
      metrics,
    } = body

    if (!Number.isInteger(index) || index < 0) {
      throw new Error('Nieprawidłowy indeks akapitu')
    }

    const historyDir = path.join(rootDir, 'output', 'history')
    fs.mkdirSync(historyDir, { recursive: true })
    const historyPath = path.join(historyDir, `${book}_history.json`)
    const mdPath = path.join(rootDir, 'output', 'markdown', `${book}.md`)

    let docSha = ''
    if (fs.existsSync(mdPath)) {
      docSha = createHash('sha256').update(fs.readFileSync(mdPath)).digest('hex')
    }

    let ledger = { ledger_version: '1.0', book_name: book, last_updated_at: new Date().toISOString(), records: [] as any[] }
    if (fs.existsSync(historyPath)) {
      try {
        ledger = JSON.parse(fs.readFileSync(historyPath, 'utf8'))
      } catch {}
    }

    const record = {
      record_id: randomUUID().slice(0, 12),
      timestamp: new Date().toISOString(),
      book_name: book,
      paragraph_index: index,
      paragraph_sha256: computeSha(text || original),
      document_sha256_before: docSha,
      source_engine: engine,
      original_text: original,
      proposed_text: text,
      decision,
      decision_reason: reason,
      quality_metrics: metrics || null,
    }

    if (decision === 'applied') {
      for (const r of ledger.records) {
        if (r.paragraph_index === index && (r.decision === 'proposed' || r.decision === 'accepted')) {
          r.decision = 'superseded'
        }
      }
    }

    ledger.records.push(record)
    ledger.last_updated_at = new Date().toISOString()

    const tempPath = `${historyPath}.${randomUUID()}.tmp`
    fs.writeFileSync(tempPath, JSON.stringify(ledger, null, 2), 'utf8')
    fs.renameSync(tempPath, historyPath)

    return Response.json({ success: true, record })
  } catch (error: any) {
    return Response.json({ success: false, error: error.message }, { status: 400 })
  }
}
