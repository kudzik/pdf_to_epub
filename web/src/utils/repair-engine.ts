import { repairPreservingAssets } from './repair-assets.ts';
import { assessRepair } from './repair-policy.ts';

export interface Evidence { text: string; pages: number[]; confidence: number; coverage: number; method: string }
export interface Completion { text: string; uncertain: boolean }
export interface RepairDependencies {
  complete: (fragment: string, evidence?: Evidence) => Promise<Completion>;
  source: (fragment: string) => Promise<Evidence>;
}

export async function reconstruct(original: string, deps: RepairDependencies) {
  const sources: Evidence[] = [];
  const issues: string[] = [];
  let usedSource = false;
  const repaired = await repairPreservingAssets(original, async fragment => {
    const heading = fragment.match(/^#{1,6}\s+/)?.[0] || '';
    const normalizeHeading = (candidate: Completion): Completion => ({ ...candidate, text: heading ? heading + candidate.text.replace(/^#{1,6}\s+/, '').replace(/\s*\n\s*/g, ' ') : candidate.text });
    let candidate = normalizeHeading(await deps.complete(fragment));
    let verdict = assessRepair(fragment, candidate.text, undefined, candidate.uncertain);
    // Visible noise can survive even in a highly similar answer. Large changes need evidence.
    if (!verdict.accepted || heading) {
      usedSource = true;
      let evidence: Evidence;
      try { evidence = await deps.source(fragment); }
      catch (error: any) {
        issues.push(`Brak potwierdzenia w PDF: ${error.message}`);
        return candidate.text || fragment;
      }
      sources.push(evidence);
      candidate = normalizeHeading(await deps.complete(fragment, evidence));
      verdict = assessRepair(fragment, candidate.text, evidence, candidate.uncertain);
    }
    issues.push(...verdict.issues);
    return candidate.text || fragment;
  });
  const overall = assessRepair(original, repaired);
  // Fragment decisions use source evidence where available; whole-paragraph number
  // differences are reported, not blindly waived or treated as proof of correctness.
  return {
    repaired,
    accepted: issues.length === 0 && repaired.trim() !== original.trim(),
    issues: [...new Set(issues)],
    metrics: overall.metrics,
    sources: sources.map(({ text, ...metadata }) => metadata),
    method: usedSource ? 'source-ocr' : 'text',
  };
}
