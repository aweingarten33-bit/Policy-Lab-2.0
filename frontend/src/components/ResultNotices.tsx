import React from "react";
import { AlertTriangle } from "lucide-react";
import type { DraftedPolicy, StateCoverage } from "@/lib/api";

// ── What a drafted policy does and does not establish ──
// (The analysis report has no such banner: its verification verdicts are the
// badges on each finding.)

function liveSearchLine(used: boolean | undefined): string {
  return used
    ? "A live search of government websites ran; those results are web pages, not codified text."
    : "No live government search ran: the stored federal regulations covered the request. Nothing newer than the stored text was checked.";
}

export function draftLimitations(draft: DraftedPolicy): string[] {
  const lines: string[] = [];
  if (draft.verification_overall) lines.push(draft.verification_overall);
  const decisions = draft.decisions_required?.length ?? 0;
  if (decisions > 0) {
    lines.push(
      `This is a draft with ${decisions} open decision${decisions !== 1 ? "s" : ""}. Bracketed placeholders like [ACCOUNTABLE ROLE 1] and [TIMEFRAME] are for your organization to fill in — they were not supplied, so nothing was invented for them.`,
    );
  }
  const missing = draft.missing_obligations?.length ?? 0;
  if (missing > 0) {
    lines.push(`${missing} regulatory obligation${missing !== 1 ? "s" : ""} for this topic ${missing !== 1 ? "do" : "does"} not appear in the draft (listed below).`);
  }
  lines.push(liveSearchLine(draft.live_research_used));
  if (draft.state_coverage) lines.push(`State law: ${draft.state_coverage.summary}`);
  return lines;
}

export const UNGROUNDED_DRAFT_NOTICE =
  "Drafted without federal regulation sources - treat every requirement as unverified and confirm what applies to your organization.";

/** Shown at the top of a draft only when no regulation source was retrieved for it. */
export function UngroundedDraftBanner({ grounded }: { grounded?: boolean }) {
  if (grounded !== false) return null;
  return (
    <div className="rounded-2xl p-4 sm:p-5 border-l-4" style={{ borderLeftColor: "hsl(8 75% 45%)", background: "hsl(8 80% 52% / 0.08)" }} role="alert">
      <div className="flex items-start gap-2">
        <AlertTriangle className="w-4 h-4 shrink-0 mt-0.5" style={{ color: "hsl(8 75% 40%)" }} />
        <p className="text-[13px] sm:text-[14px] font-semibold leading-relaxed" style={{ color: "hsl(8 70% 32%)" }}>{UNGROUNDED_DRAFT_NOTICE}</p>
      </div>
    </div>
  );
}

export function LimitationsBanner({ lines, title = "Read first — what this does and doesn't establish" }: { lines: string[]; title?: string }) {
  if (lines.length === 0) return null;
  return (
    <div className="rounded-2xl p-4 sm:p-5 border-l-4" style={{ borderLeftColor: "hsl(38 85% 44%)", background: "hsl(38 85% 52% / 0.08)" }} role="note">
      <div className="flex items-center gap-2 mb-2">
        <AlertTriangle className="w-4 h-4 shrink-0" style={{ color: "hsl(38 85% 38%)" }} />
        <p className="text-[11px] font-mono uppercase tracking-wider font-bold" style={{ color: "hsl(38 85% 34%)" }}>{title}</p>
      </div>
      <ul className="space-y-1.5">
        {lines.map((line, i) => (
          <li key={i} className="text-[12px] sm:text-[13px] leading-relaxed text-foreground/85 pl-3 border-l-2" style={{ borderColor: "hsl(38 85% 44% / 0.35)" }}>
            {line}
          </li>
        ))}
      </ul>
    </div>
  );
}

/** The state sources that were actually consulted, or a plain statement that none were. */
export function StateSourcesList({ coverage }: { coverage?: StateCoverage | null }) {
  if (!coverage) return null;
  return (
    <div className="rounded-xl neu-sm p-4">
      <p className="nyt-eyebrow mb-2">{coverage.jurisdiction} sources consulted</p>
      {coverage.sources_consulted.length === 0 ? (
        <p className="text-[12px] text-muted-foreground leading-relaxed">
          None. No {coverage.jurisdiction} law text was available, so {coverage.jurisdiction} requirements were not checked.
        </p>
      ) : (
        <ul className="space-y-1">
          {coverage.sources_consulted.map((src, i) => (
            <li key={i} className="text-[12px] leading-relaxed">
              {src.url ? (
                <a href={src.url} target="_blank" rel="noopener noreferrer" className="text-primary hover:underline">{src.name} ↗</a>
              ) : (
                <span>{src.name}</span>
              )}
              <span className="ml-2 text-[10px] font-mono text-muted-foreground">
                {src.kind === "live_search" ? "web search result — not codified text" : "stored text"}
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

// ── Minimal Markdown for chat replies ──
// The model answers in Markdown, and the chat showed it raw (**bold**, "- "
// bullets, "### headings"). This renders the handful of constructs it uses as
// React elements -- no HTML string is ever injected.

function renderInline(text: string, keyPrefix: string): React.ReactNode[] {
  const out: React.ReactNode[] = [];
  const pattern = /(\*\*[^*]+\*\*|__[^_]+__|`[^`]+`|\*[^*\s][^*]*\*|_[^_\s][^_]*_)/g;
  let last = 0;
  let match: RegExpExecArray | null;
  let n = 0;
  while ((match = pattern.exec(text)) !== null) {
    if (match.index > last) out.push(text.slice(last, match.index));
    const token = match[0];
    const key = `${keyPrefix}-${n++}`;
    if (token.startsWith("**") || token.startsWith("__")) out.push(<strong key={key}>{token.slice(2, -2)}</strong>);
    else if (token.startsWith("`")) out.push(<code key={key} className="font-mono text-[11px] px-1 rounded bg-foreground/5">{token.slice(1, -1)}</code>);
    else out.push(<em key={key}>{token.slice(1, -1)}</em>);
    last = match.index + token.length;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}

export function ChatMarkdown({ text }: { text: string }) {
  const blocks: React.ReactNode[] = [];
  const lines = text.replace(/\r\n/g, "\n").split("\n");
  let list: { ordered: boolean; items: string[] } | null = null;
  let paragraph: string[] = [];

  const flushParagraph = () => {
    if (paragraph.length) {
      const k = `p${blocks.length}`;
      blocks.push(<p key={k}>{renderInline(paragraph.join(" "), k)}</p>);
      paragraph = [];
    }
  };
  const flushList = () => {
    if (list) {
      const k = `l${blocks.length}`;
      const items = list.items.map((item, i) => <li key={i}>{renderInline(item, `${k}-${i}`)}</li>);
      blocks.push(list.ordered
        ? <ol key={k} className="list-decimal pl-5 space-y-0.5">{items}</ol>
        : <ul key={k} className="list-disc pl-5 space-y-0.5">{items}</ul>);
      list = null;
    }
  };

  for (const raw of lines) {
    const line = raw.trimEnd();
    const bullet = line.match(/^\s*[-*•]\s+(.*)$/);
    const numbered = line.match(/^\s*\d+[.)]\s+(.*)$/);
    const heading = line.match(/^\s*#{1,6}\s+(.*)$/);
    if (!line.trim()) { flushParagraph(); flushList(); continue; }
    if (heading) {
      flushParagraph(); flushList();
      const k = `h${blocks.length}`;
      blocks.push(<p key={k} className="font-bold">{renderInline(heading[1], k)}</p>);
    } else if (bullet || numbered) {
      flushParagraph();
      const ordered = !!numbered;
      if (!list || list.ordered !== ordered) { flushList(); list = { ordered, items: [] }; }
      list.items.push((bullet ?? numbered)![1]);
    } else if (/^\s*([-*_])\1{2,}\s*$/.test(line)) {
      flushParagraph(); flushList();
    } else {
      flushList();
      paragraph.push(line.trim());
    }
  }
  flushParagraph();
  flushList();
  return <div className="space-y-2">{blocks}</div>;
}
