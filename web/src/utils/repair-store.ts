import fs from 'node:fs'
import { randomUUID, createHash } from 'node:crypto'
import { assetsPreserved } from './repair-assets.ts'

export interface Patch {
  index: number
  text: string
  original?: string
}

export const computeSha256 = (content: string | Buffer): string => {
  return createHash('sha256').update(content).digest('hex')
}

export const acquireBookLock = (mdPath: string, timeoutMs = 5000, owner = 'web'): () => void => {
  const lockPath = `${mdPath}.lock`
  const startTime = Date.now()

  while (true) {
    try {
      const fd = fs.openSync(lockPath, 'wx')
      const payload = JSON.stringify({ pid: process.pid, owner, timestamp: new Date().toISOString() })
      fs.writeFileSync(fd, payload, 'utf8')
      fs.closeSync(fd)

      return () => {
        try {
          if (fs.existsSync(lockPath)) fs.unlinkSync(lockPath)
        } catch {}
      }
    } catch (err: any) {
      if (err.code === 'EEXIST') {
        try {
          const stat = fs.statSync(lockPath)
          if (Date.now() - stat.mtimeMs > 30000) {
            try { fs.unlinkSync(lockPath) } catch {}
          }
        } catch {}

        if (Date.now() - startTime >= timeoutMs) {
          const lockError = new Error('Zasób jest zablokowany przez inną operację zapisu (CLI/API)')
          Object.assign(lockError, { status: 423 })
          throw lockError
        }

        const waitTill = Date.now() + 50
        while (Date.now() < waitTill) {}
        continue
      }
      throw err
    }
  }
}

export const writeRepairs = (mdPath: string, patches: Patch[], expectedSha256?: string): number => {
  const unlock = acquireBookLock(mdPath)

  try {
    const rawContent = fs.readFileSync(mdPath, 'utf8')
    const currentSha = computeSha256(rawContent)

    if (expectedSha256 && expectedSha256.toLowerCase() !== currentSha.toLowerCase()) {
      const error = new Error('Konflikt wersji dokumentu. Plik uległ zmianie. Odśwież raport.')
      Object.assign(error, { status: 409 })
      throw error
    }

    const paragraphs = rawContent.split('\n\n')
    const indices = new Set<number>()
    let changed = 0

    for (const patch of patches) {
      const { index, text, original } = patch
      if (!Number.isInteger(index) || index < 0 || index >= paragraphs.length || indices.has(index)) {
        throw new Error('Nieprawidłowy lub powtórzony indeks akapitu')
      }
      indices.add(index)

      if (original !== undefined && paragraphs[index].trim() !== original.trim()) {
        const error = new Error('Konflikt zapisu. Odśwież raport.')
        Object.assign(error, { status: 409 })
        throw error
      }

      if (typeof text !== 'string' || !text.trim()) {
        throw new Error('Nie można zapisać pustej rekonstrukcji')
      }

      if (!assetsPreserved(paragraphs[index], text)) {
        throw new Error('Zapis zablokowany: zmieniono grafiki')
      }

      const replacement = text.trim().replace(/\r\n/g, '\n').replace(/\n\s*\n/g, '\n')
      if (paragraphs[index].trim() !== replacement) changed++
      paragraphs[index] = replacement
    }

    if (!changed) return 0

    if (!fs.existsSync(mdPath + '.bak')) {
      fs.copyFileSync(mdPath, mdPath + '.bak')
    }

    const temporary = `${mdPath}.${randomUUID()}.tmp`
    try {
      fs.writeFileSync(temporary, paragraphs.join('\n\n'), 'utf8')
      fs.renameSync(temporary, mdPath)
    } finally {
      if (fs.existsSync(temporary)) {
        try { fs.unlinkSync(temporary) } catch {}
      }
    }

    return changed
  } finally {
    unlock()
  }
}

export const writeRepair = (mdPath: string, index: number, original: string, replacement: string, expectedSha256?: string): number => {
  return writeRepairs(mdPath, [{ index, text: replacement, original }], expectedSha256)
}
