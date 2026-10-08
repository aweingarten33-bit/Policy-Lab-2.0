import React, { useEffect } from "react";
import { X } from "lucide-react";
import type { GapRow, VerificationEvidence } from "@/lib/api";
import { EVIDENCE_LABELS, checkedCitation, evidenceStatusOf, isChecking, type EvidenceStatus } from "@/lib/findings";

// ── The evidence badge on one finding ──
// Verified requirement / Needs source review / Recommendation, shown on the
// collapsed card next to the priority badge and independent of it. Clicking
// it opens the cited passage beside the finding's claim. Mirrors backend
// app/services/verification_badge.py, which the Word export uses.

export type BadgeState = EvidenceStatus | "checking";

const BADGE_STYLE: Record<BadgeState, { color: string; bg: string; border: string }> = {
  verified_requirement: { color: "hsl(152 60% 28%)", bg: "hsl(152 55% 40% / 0.12)", border: "hsl(152 55% 35% / 0.35)" },
  needs_source_review:  { color: "hsl(32 90% 32%)",  bg: "hsl(40 95% 50% / 0.15)",  border: "hsl(36 90% 42% / 0.4)" },
  recommendation:       { color: "hsl(215 25% 38%)", bg: "hsl(215 30% 50% / 0.10)", border: "hsl(215 25% 45% / 0.3)" },
  checking:             { color: "hsl(var(--muted-foreground))", bg: "hsl(var(--secondary))", border: "transparent" },
};

/** The evidence badge for a finding: one of three statuses, or "checking" while it runs. */
export function badgeFor(row: GapRow, verifying: boolean): { state: BadgeState; label: string } {
  if (isChecking(row, verifying)) return { state: "checking", label: "Checking the cited regulation…" };
  const status = evidenceStatusOf(row);
  const label = status === "verified_requirement"
    ? `${EVIDENCE_LABELS[status]} · ${checkedCitation(row)}`
    : EVIDENCE_LABELS[status];
  return { state: status, label };
}

function Dot({ state }: { state: BadgeState }) {
  return (
    <span
      aria-hidden="true"
      className={`inline-block w-1.5 h-1.5 rounded-full shrink-0 ${state === "checking" ? "animate-pulse" : ""}`}
      style={{ background: BADGE_STYLE[state].color }}
    />
  );
}

export function VerificationBadge({ state, label, onOpen }: { state: BadgeState; label: string; onOpen?: () => void }) {
  const style = BADGE_STYLE[state];
  const common = "inline-flex items-center gap-1.5 text-[10px] sm:text-[11px] font-medium px-2.5 py-1 rounded-full max-w-full";
  const css = { color: style.color, background: style.bg, boxShadow: `inset 0 0 0 1px ${style.border}` };
  if (state === "checking" || !onOpen) {
    return <span className={common} style={css}><Dot state={state} /><span className="truncate">{label}</span></span>;
  }
  return (
    <button
      type="button"
      onClick={onOpen}
      title="Read the cited passage next to this finding's claim"
      className={`${common} hover:brightness-95 active:opacity-80 touch-manipulation`}
      style={css}
    >
      <Dot state={state} />
      <span className="truncate">{label}</span>
      <span aria-hidden="true" className="opacity-70">›</span>
    </button>
  );
}

// ── Side panel: the cited passage beside the claim ──

const ROLE_LABEL: Record<string, string> = {
  cited: "Cited passage",
  also_cited: "Also cited",
  incorporated: "Incorporated by reference",
};

function whatItMeans(state: BadgeState, row: GapRow): string | null {
  if (state === "verified_requirement") return "The cited regulation's text was checked and it supports the requirement as stated.";
  if (state === "recommendation") return "This is a recommendation: good practice, not a legal requirement, whatever its priority.";
  if (state === "needs_source_review") {
    return row.evidence?.status === "partially_verified"
      ? "The cited regulation was located, but its text does not settle the requirement exactly as stated. Read the passage and confirm it applies to your situation."
      : "The cited text could not be confirmed to support this requirement. Review the regulation yourself before relying on it.";
  }
  return null;
}

function passagesOf(evidence?: VerificationEvidence | null) {
  const list = evidence?.source?.passages ?? [];
  if (list.length > 0) return list;
  const excerpt = evidence?.source?.excerpt;
  return excerpt ? [{ citation: evidence?.source?.name ?? "", role: "cited", text: excerpt }] : [];
}

export function VerificationSidePanel({
  row, badge, onClose,
}: { row: GapRow; badge: { state: BadgeState; label: string }; onClose: () => void }) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    document.addEventListener("keydown", onKey);
    const overflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => { document.removeEventListener("keydown", onKey); document.body.style.overflow = overflow; };
  }, [onClose]);

  const evidence = row.evidence;
  const isRec = badge.state === "recommendation";
  const claim = isRec ? (row.recommendations?.join(" ") || row.finding) : (row.regulatory_requirement || evidence?.claim_text || row.finding);
  const passages = passagesOf(evidence);
  const style = BADGE_STYLE[badge.state];
  const meaning = whatItMeans(badge.state, row);
  const url = evidence?.source?.url;

  return (
    <div className="fixed inset-0 z-50" role="dialog" aria-modal="true" aria-label="Cited passage">
      <div className="absolute inset-0 bg-black/30" onClick={onClose} />
      <aside className="absolute right-0 top-0 h-full w-full sm:w-[min(880px,92vw)] bg-background shadow-2xl flex flex-col">
        <header className="flex items-start justify-between gap-3 px-4 sm:px-6 py-4 border-b border-border">
          <div className="min-w-0 space-y-2">
            <p className="text-[13px] sm:text-sm font-semibold text-foreground">{row.clause}</p>
            <VerificationBadge state={badge.state} label={badge.label} />
          </div>
          <button type="button" onClick={onClose} aria-label="Close" className="p-1.5 rounded-lg hover:bg-secondary shrink-0">
            <X className="w-4 h-4" />
          </button>
        </header>

        <div className="flex-1 overflow-y-auto px-4 sm:px-6 py-4 space-y-4">
          {meaning && <p className="text-[12px] leading-relaxed text-foreground/75">{meaning}</p>}

          <div className="grid gap-4 md:grid-cols-2">
            <section>
              <p className="text-[10px] font-mono uppercase tracking-wider text-muted-foreground font-medium mb-1.5">
                {isRec ? "What the finding recommends" : "What the finding says the regulation requires"}
              </p>
              <div className="rounded-xl neu-inset p-3 sm:p-4">
                <p className="text-[13px] leading-relaxed text-foreground">{claim}</p>
              </div>
              {row.citation && (
                <p className="mt-2 text-[10px] font-mono text-muted-foreground break-words">Cited: {row.citation}</p>
              )}
            </section>

            <section>
              <p className="text-[10px] font-mono uppercase tracking-wider text-muted-foreground font-medium mb-1.5">
                What the regulation says
              </p>
              {passages.length === 0 ? (
                <div className="rounded-xl neu-inset p-3 sm:p-4">
                  <p className="text-[12px] leading-relaxed text-foreground/75">
                    No passage of the cited regulation was found in the stored regulatory text, so there is nothing to show here.
                    Look the citation up directly before relying on this finding.
                  </p>
                </div>
              ) : (
                <div className="space-y-3">
                  {passages.map((p, i) => (
                    <div key={i} className="rounded-xl p-3 sm:p-4 border-l-2" style={{ borderColor: style.border, background: "hsl(var(--secondary) / 0.6)" }}>
                      <p className="text-[10px] font-mono uppercase tracking-wider font-medium mb-1" style={{ color: style.color }}>
                        {ROLE_LABEL[p.role] ?? "Cited passage"}{p.citation ? ` — ${p.citation}` : ""}
                      </p>
                      <p className="text-[12px] sm:text-[13px] leading-relaxed text-foreground/90 whitespace-pre-wrap">&ldquo;{p.text}&rdquo;</p>
                    </div>
                  ))}
                </div>
              )}
              {url && (
                <a href={url} target="_blank" rel="noopener noreferrer" className="mt-2 inline-block text-[11px] font-mono text-primary hover:underline">
                  Open the full regulation ↗
                </a>
              )}
            </section>
          </div>

          {(evidence?.reason || row.verification_warning || row.obligation_note) && (
            <section className="space-y-1.5">
              <p className="text-[10px] font-mono uppercase tracking-wider text-muted-foreground font-medium">How it was checked</p>
              {evidence?.reason && <p className="text-[12px] leading-relaxed text-foreground/80">{evidence.reason}</p>}
              {row.verification_warning && <p className="text-[12px] leading-relaxed text-foreground/80">{row.verification_warning}</p>}
              {row.obligation_note && <p className="text-[12px] leading-relaxed text-foreground/80">{row.obligation_note}</p>}
              {evidence?.source?.version_date && (
                <p className="text-[10px] font-mono text-muted-foreground">Regulation text as of {evidence.source.version_date}</p>
              )}
            </section>
          )}
        </div>
      </aside>
    </div>
  );
}
