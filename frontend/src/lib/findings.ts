import type { ComplianceActionPackage, GapRow } from "@/lib/api";

// ── Evidence status and priority: two independent fields per finding ──
// "Is it established law?" and "how urgent is it?" used to be read off the
// same signals, so every finding said MUST FIX whether or not its citation
// held up, and the overview (model free text) could call a recommendation
// mandatory while its own card said best practice. Now each finding has both
// fields, and the summary and the limitations banner are built from them.
// Mirrors backend app/services/verification_badge.py word for word.

export type EvidenceStatus = "verified_requirement" | "needs_source_review" | "recommendation";
export type Priority = "must_fix" | "should_fix";

export const EVIDENCE_LABELS: Record<EvidenceStatus, string> = {
  verified_requirement: "Verified requirement",
  needs_source_review: "Needs source review",
  recommendation: "Recommendation",
};

export const PRIORITY_LABELS: Record<Priority, string> = { must_fix: "Must fix", should_fix: "Should fix" };

const UNCITED = /no (specific )?regulatory citation applies|organizational best practice/i;
const HAS_CFR = /\bC\.?\s*F\.?\s*R\b/i;
const NOT_LAW = new Set(["best_practice", "organizational_choice", "guidance"]);

export function evidenceStatusOf(row: GapRow): EvidenceStatus {
  if (row.evidence_status === "verified_requirement" || row.evidence_status === "needs_source_review"
      || row.evidence_status === "recommendation") return row.evidence_status;
  const citation = row.citation || "";
  if (!citation.trim() || (UNCITED.test(citation) && !HAS_CFR.test(citation)) || NOT_LAW.has(row.obligation_type ?? "")) {
    return "recommendation";
  }
  return row.evidence?.status === "verified" ? "verified_requirement" : "needs_source_review";
}

/** True while a cited finding is still waiting for its evidence check. */
export function isChecking(row: GapRow, verifying: boolean): boolean {
  return verifying && !row.evidence && evidenceStatusOf(row) !== "recommendation";
}

export function priorityOf(row: GapRow): Priority {
  if (row.priority === "must_fix" || row.priority === "should_fix") return row.priority;
  return row.risk_level === "critical" || row.risk_level === "high" ? "must_fix" : "should_fix";
}

export function checkedCitation(row: GapRow): string {
  const first = row.evidence?.source?.passages?.[0]?.citation;
  if (first) return first;
  return (row.citation || "").split(/[;—\[(]/)[0].trim();
}

export function countByEvidence(rows: GapRow[]): Record<EvidenceStatus, number> {
  const counts: Record<EvidenceStatus, number> = { verified_requirement: 0, needs_source_review: 0, recommendation: 0 };
  for (const r of rows) counts[evidenceStatusOf(r)] += 1;
  return counts;
}

const n = (count: number, one: string, many: string) => `${count} ${count === 1 ? one : many}`;
const join = (parts: string[]) => (parts.length === 1 ? parts[0] : `${parts.slice(0, -1).join(", ")} and ${parts[parts.length - 1]}`);

/** The report summary, written from the finding objects only (no model prose). */
export function findingsSummary(rows: GapRow[]): string {
  if (rows.length === 0) return "This review produced no findings: the document addresses each obligation reviewed.";
  const { verified_requirement: v, needs_source_review: nr, recommendation: r } = countByEvidence(rows);
  const parts: string[] = [];
  if (v) parts.push(`${n(v, "verified requirement", "verified requirements")} (the cited regulation was checked and supports ${v === 1 ? "it" : "them"})`);
  if (nr) parts.push(`${n(nr, "finding needs", "findings need")} source review (a regulation is cited, but its text did not confirm the requirement as stated)`);
  if (r) parts.push(`${n(r, "recommendation", "recommendations")} (good practice, not ${r === 1 ? "a legal requirement" : "legal requirements"})`);
  const m = rows.filter((x) => priorityOf(x) === "must_fix").length;
  const s = rows.length - m;
  const urgency = join([m ? `${m} ${m === 1 ? "is" : "are"} Must fix` : "", s ? `${s} ${s === 1 ? "is" : "are"} Should fix` : ""].filter(Boolean));
  return `This review produced ${n(rows.length, "finding", "findings")}: ${join(parts)}. By priority, ${urgency}.`;
}

// ── Limitations disclaimer, in the same words as the cards ──

const DOCUMENT_ONLY =
  "Only the policy document was read. No records, logs or practices were inspected, so findings describe what the document says or omits, not whether your organization complies.";

export function analysisLimitations(pkg: ComplianceActionPackage, verifying: boolean): string[] {
  const rows = pkg.gap_analysis?.gap_table ?? [];
  const lines: string[] = [];
  if (verifying && rows.some((r) => isChecking(r, true))) {
    lines.push("Each finding's citation is still being checked against the regulation text. Until its badge settles, treat it as needing source review.");
  } else if (rows.length > 0) {
    const c = countByEvidence(rows);
    const counted = (Object.keys(EVIDENCE_LABELS) as EvidenceStatus[])
      .filter((k) => c[k] > 0)
      .map((k) => `${c[k]} ${EVIDENCE_LABELS[k]}`);
    lines.push(`Evidence, per finding badge: ${counted.join(" · ")}.`);
    if (c.verified_requirement) lines.push("Verified requirement: the cited regulation's text was checked and supports the requirement as stated.");
    if (c.needs_source_review) lines.push("Needs source review: a regulation is cited but its text did not confirm the requirement as stated. Click the badge to read the passage before relying on it.");
    if (c.recommendation) lines.push("Recommendation: good practice, not a legal requirement, whatever its priority.");
  }
  lines.push(DOCUMENT_ONLY);
  lines.push(
    pkg.live_research_used
      ? "A live search of government websites ran; those results are web pages, not codified text."
      : "No live government search ran: the stored federal regulations covered the request. Nothing newer than the stored text was checked.",
  );
  if (pkg.state_coverage) lines.push(`State law: ${pkg.state_coverage.summary}`);
  return lines;
}

// ── Re-check: which revision section a remaining finding belongs to ──
// A re-check analyses the proposed revision, so a finding's current_state
// quotes (or closely paraphrases) the revision. A quote found in a section's
// text places it exactly; otherwise the finding's topic must share a word with
// a section title. Anything less certain gets no label rather than a guess.

const STOP = new Set(["policy", "section", "procedure", "procedures", "requirements", "requirement", "with", "from", "that", "this", "their", "shall", "must", "will", "each", "other", "within"]);
const norm = (t: string) => t.toLowerCase().replace(/[“”"']/g, "").replace(/\s+/g, " ").trim();
const words = (t: string) => new Set((norm(t).match(/[a-z]{4,}/g) ?? []).filter((w) => !STOP.has(w)));

export function revisionSectionFor(
  row: GapRow,
  sections: { section_title: string; rewritten_text: string }[] | undefined,
): string | null {
  if (!sections?.length) return null;
  const quote = norm(row.current_state || "");
  if (quote.length >= 20 && !quote.startsWith("policy is silent")) {
    const probe = quote.slice(0, 60);
    const hit = sections.find((s) => norm(s.rewritten_text || "").includes(probe));
    if (hit) return hit.section_title;
  }
  const topic = words(row.clause || "");
  let best: string | null = null;
  let bestScore = 0;
  for (const s of sections) {
    const title = words(s.section_title || "");
    const score = [...topic].filter((w) => title.has(w)).length;
    if (score > bestScore) { best = s.section_title; bestScore = score; }
  }
  return bestScore > 0 ? best : null;
}

export function mustFixCount(rows: GapRow[] | undefined): number {
  return (rows ?? []).filter((r) => priorityOf(r) === "must_fix").length;
}

// ── Where in the user's policy a finding is ──
// The analysis has no dedicated section field, but its quote of the policy and
// its finding often name one ("Section 4.2 references…"). Only policy-style
// references count: "Section 4.2", "Section IV", "Page 3" -- not statutes
// ("Section 1557 of the ACA", "Section 5 of HIPAA") or CFR citations, which use "§".
const SECTION_RE = /\bSection\s+((?:\d{1,2}(?:\.\d{1,3})*[A-Za-z]?)|(?:[IVX]{1,6}))\b(?!\s+of\s+(?:the\s+)?(?![Pp]olicy\b)[A-Z])/;
const PAGE_RE = /\bpage\s+(\d{1,3})\b/i;

export function policySectionRef(row: GapRow): string | null {
  for (const text of [row.current_state, row.finding, row.clause]) {
    if (!text) continue;
    const s = text.match(SECTION_RE);
    if (s) return `Policy section: ${s[1]}`;
    const p = text.match(PAGE_RE);
    if (p) return `Policy page: ${p[1]}`;
  }
  return null;
}

/** "Policy is silent — …" is a statement about the policy, not a quote from it. */
export function isSilentQuote(text?: string | null): boolean {
  return /^\s*["“]?policy is silent/i.test(text ?? "");
}
