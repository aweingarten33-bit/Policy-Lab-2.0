import React, { useEffect, useRef, useState } from "react";
import type { SourceSnippet } from "@/lib/api";

type Rule = { pattern: RegExp; url: (m: RegExpExecArray) => string };

// ── Curated rules: well-known regulations get the canonical authority URL ──
const CURATED_RULES: Rule[] = [
  // 45 CFR §164.XXX (or 164.XXX(a)(1)) — most common HIPAA cite
  {
    pattern: /\b45\s*C\.?F\.?R\.?\s*§?\s*164\.(\d{3})(?:\([a-z0-9]+\))*/gi,
    url: (m) => `https://www.ecfr.gov/current/title-45/section-164.${m[1]}`,
  },
  // 45 CFR §160.XXX
  {
    pattern: /\b45\s*C\.?F\.?R\.?\s*§?\s*160\.(\d{3})/gi,
    url: (m) => `https://www.ecfr.gov/current/title-45/section-160.${m[1]}`,
  },
  // 45 CFR §162.XXX
  {
    pattern: /\b45\s*C\.?F\.?R\.?\s*§?\s*162\.(\d{3})/gi,
    url: (m) => `https://www.ecfr.gov/current/title-45/section-162.${m[1]}`,
  },
  // 45 CFR Part 164 / Subpart C, D, E
  {
    pattern: /\b45\s*C\.?F\.?R\.?\s*Part\s*164(?:,?\s*Subpart\s*([A-E]))?/gi,
    url: (m) =>
      m[1]
        ? `https://www.ecfr.gov/current/title-45/subtitle-A/subchapter-C/part-164/subpart-${m[1].toUpperCase()}`
        : `https://www.ecfr.gov/current/title-45/subtitle-A/subchapter-C/part-164`,
  },
  // 45 CFR Part 160 / 162
  {
    pattern: /\b45\s*C\.?F\.?R\.?\s*Part\s*(160|162)\b/gi,
    url: (m) => `https://www.ecfr.gov/current/title-45/subtitle-A/subchapter-C/part-${m[1]}`,
  },
  // 42 CFR Part 2 (substance use confidentiality)
  {
    pattern: /\b42\s*C\.?F\.?R\.?\s*Part\s*2\b/gi,
    url: () => `https://www.ecfr.gov/current/title-42/chapter-I/subchapter-A/part-2`,
  },
  // 42 CFR §484.XX (Home Health Conditions of Participation)
  {
    pattern: /\b42\s*C\.?F\.?R\.?\s*§?\s*484\.(\d{1,3})(?:\([a-z0-9]+\))*/gi,
    url: (m) => `https://www.ecfr.gov/current/title-42/section-484.${m[1]}`,
  },
  // 42 CFR Part 484
  {
    pattern: /\b42\s*C\.?F\.?R\.?\s*Part\s*484\b/gi,
    url: () => `https://www.ecfr.gov/current/title-42/part-484`,
  },
  // 42 CFR §424.XX (Conditions for Medicare Payment, incl. face-to-face encounter)
  {
    pattern: /\b42\s*C\.?F\.?R\.?\s*§?\s*424\.(\d{1,3})(?:\([a-z0-9]+\))*/gi,
    url: (m) => `https://www.ecfr.gov/current/title-42/section-424.${m[1]}`,
  },
  // 29 CFR §1630.XX (ADA Employment Regulations)
  {
    pattern: /\b29\s*C\.?F\.?R\.?\s*§?\s*1630\.(\d{1,3})(?:\([a-z0-9]+\))*/gi,
    url: (m) => `https://www.ecfr.gov/current/title-29/section-1630.${m[1]}`,
  },
  // 29 CFR Part 1630 / 1604
  {
    pattern: /\b29\s*C\.?F\.?R\.?\s*Part\s*(1630|1604)\b/gi,
    url: (m) => `https://www.ecfr.gov/current/title-29/part-${m[1]}`,
  },
  // 29 CFR §825.XX (FMLA)
  {
    pattern: /\b29\s*C\.?F\.?R\.?\s*§?\s*825\.(\d{1,3})(?:\([a-z0-9]+\))*/gi,
    url: (m) => `https://www.ecfr.gov/current/title-29/section-825.${m[1]}`,
  },
  // 29 CFR Part 825
  {
    pattern: /\b29\s*C\.?F\.?R\.?\s*Part\s*825\b/gi,
    url: () => `https://www.ecfr.gov/current/title-29/part-825`,
  },
  // Named rules
  {
    pattern: /\bHIPAA\s+Privacy\s+Rule\b/gi,
    url: () => `https://www.hhs.gov/hipaa/for-professionals/privacy/index.html`,
  },
  {
    pattern: /\bHIPAA\s+Security\s+Rule\b/gi,
    url: () => `https://www.hhs.gov/hipaa/for-professionals/security/index.html`,
  },
  {
    pattern: /\bHIPAA\s+Breach\s+Notification(?:\s+Rule)?\b/gi,
    url: () => `https://www.hhs.gov/hipaa/for-professionals/breach-notification/index.html`,
  },
  {
    pattern: /\bHITECH\s+Act\b/gi,
    url: () => `https://www.hhs.gov/hipaa/for-professionals/special-topics/hitech-act-enforcement-interim-final-rule/index.html`,
  },
  {
    pattern: /\bOmnibus\s+Rule\b/gi,
    url: () => `https://www.hhs.gov/hipaa/for-professionals/privacy/laws-regulations/combined-regulation-text/omnibus-hipaa-rulemaking/index.html`,
  },
  // 42 USC §1320d-XX (HIPAA statutory base)
  {
    pattern: /\b42\s*U\.?S\.?C\.?\s*§?\s*1320d(?:-\d+)?/gi,
    url: () => `https://www.law.cornell.edu/uscode/text/42/1320d`,
  },
];

// ── Permissive fallback: any cite-shaped string gets an ecfr search link ──
// These run AFTER curated rules; overlap-resolution (below) keeps the curated
// match when both fire on the same span.
const FALLBACK_RULES: Rule[] = [
  // Any "<digits> CFR ..." reference we didn't recognize specifically
  {
    pattern: /\b\d+\s*C\.?F\.?R\.?\s*(?:Part\s*\d+|§\s*\d+\.\d+(?:\([a-z0-9]+\))*|\d+\.\d+(?:\([a-z0-9]+\))*)/gi,
    url: (m) => `https://www.ecfr.gov/search?search%5Bquery%5D=${encodeURIComponent(m[0])}`,
  },
  // Any "<digits> U.S.C. ..." reference we didn't recognize
  {
    pattern: /\b\d+\s*U\.?S\.?C\.?\s*§?\s*\d+[A-Za-z0-9\-]*/gi,
    url: (m) => `https://www.law.cornell.edu/uscode/search?q=${encodeURIComponent(m[0])}`,
  },
  // OCR / HHS sub-regulatory guidance references
  {
    pattern: /\bOCR\s+(?:Guidance|Bulletin|FAQ)(?:\s+on\s+[A-Z][\w\s]{0,40})?/gi,
    url: (m) => `https://www.hhs.gov/hipaa/for-professionals/index.html?q=${encodeURIComponent(m[0])}`,
  },
  // NIST publication numbers (e.g., NIST SP 800-66)
  {
    pattern: /\bNIST\s+SP\s+\d{3}-\d+(?:r\d+)?/gi,
    url: (m) => `https://csrc.nist.gov/publications/search?keywords-lg=${encodeURIComponent(m[0])}`,
  },
];

const escapeRegex = (s: string) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

// ── Grounded citation matching ──
// Reduces a citation-like string to its "title-part" key (e.g. "45 CFR
// §164.312(a)(1)" -> "45-164") so an inline citation can be matched against
// a retrieved source snippet even when the exact section/subsection differs
// -- we're proving the *regulation* was actually retrieved, not claiming
// that specific subsection was verified word-for-word.
function citationKey(s: string): string | null {
  const m = /(\d+)\s*C\.?F\.?R\.?[^\d]{0,15}?(\d+)/i.exec(s);
  if (m) return `${m[1]}-${m[2]}`;
  const u = /(\d+)\s*U\.?S\.?C\.?[^\d]{0,10}?(\d+)/i.exec(s);
  if (u) return `usc-${u[1]}-${u[2]}`;
  return null;
}

function buildSnippetIndex(snippets?: SourceSnippet[] | null): Map<string, SourceSnippet> {
  const index = new Map<string, SourceSnippet>();
  if (!snippets) return index;
  for (const snip of snippets) {
    const key = snip.citation ? citationKey(snip.citation) : null;
    if (key && !index.has(key)) index.set(key, snip);
  }
  return index;
}

// Popover showing the actual retrieved passage behind a citation -- proof
// the model didn't just invent the reference, not just a source-name badge.
function GroundedCitation({ label, url, snippet }: { label: string; url: string; snippet: SourceSnippet }) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLSpanElement>(null);

  useEffect(() => {
    if (!open) return;
    const onClick = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onClick);
    return () => document.removeEventListener("mousedown", onClick);
  }, [open]);

  return (
    <span ref={ref} className="relative inline-block">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="text-primary hover:underline underline decoration-dotted decoration-1 underline-offset-2 font-medium"
        title="Click to see the actual retrieved source text"
      >
        {label}
      </button>
      {open && (
        <span className="absolute z-50 left-0 top-full mt-1.5 w-80 max-w-[85vw] rounded-xl neu-raised bg-card p-3.5 text-left block">
          <span className="block text-[9px] font-mono uppercase tracking-wider text-primary mb-1.5">
            🌐 Retrieved Source Material
          </span>
          <span className="block text-[9px] font-mono text-muted-foreground mb-2">{snippet.source_name}</span>
          <span className="block text-[11px] leading-relaxed text-foreground/85 whitespace-pre-wrap">
            &ldquo;{snippet.text}&rdquo;
          </span>
          {(snippet.url || url) && (
            <a
              href={snippet.url || url}
              target="_blank"
              rel="noopener noreferrer"
              className="mt-2 inline-block text-[10px] font-mono text-primary hover:underline"
            >
              Open full source ↗
            </a>
          )}
        </span>
      )}
    </span>
  );
}

// Build a per-call ruleset that includes any source names from the provided
// urlMap as exact-string patterns. This lets named KB sources like
// "Stony Brook Medicine HIPAA Privacy Policy" become clickable wherever they
// appear in the body text — not just in the source badge.
function buildRules(urlMap?: Record<string, string>): Rule[] {
  const dynamic: Rule[] = [];
  if (urlMap) {
    // Sort by length DESC so longer names win over substrings.
    const entries = Object.entries(urlMap).sort((a, b) => b[0].length - a[0].length);
    for (const [name, url] of entries) {
      if (!name || !url) continue;
      const safe = escapeRegex(name);
      dynamic.push({
        pattern: new RegExp(safe, "gi"),
        url: () => url,
      });
    }
  }
  // Order matters: curated wins on overlap, then dynamic urlMap, then permissive fallback.
  return [...CURATED_RULES, ...dynamic, ...FALLBACK_RULES];
}

/**
 * Look up a URL for a single string (e.g. a "regulations applied" chip).
 * Checks the urlMap (exact + case-insensitive) first, then curated patterns,
 * then permissive fallbacks. Returns null only if nothing matches.
 */
export function lookupRegulationUrl(
  text: string,
  urlMap?: Record<string, string>,
): string | null {
  if (!text) return null;
  // Exact + case-insensitive urlMap hit (chip values often equal a source name)
  if (urlMap) {
    if (urlMap[text]) return urlMap[text];
    const lower = text.toLowerCase();
    for (const [k, v] of Object.entries(urlMap)) {
      if (k.toLowerCase() === lower) return v;
    }
  }
  for (const rule of buildRules(urlMap)) {
    rule.pattern.lastIndex = 0;
    const m = rule.pattern.exec(text);
    if (m) return rule.url(m);
  }
  return null;
}

/**
 * Walk a block of text, find every recognized regulation/source reference,
 * and return a React fragment with those references rendered as clickable
 * external links (new-tab) and the rest of the text untouched.
 */
export function linkifyRegulations(
  text: string,
  urlMap?: Record<string, string>,
  snippets?: SourceSnippet[] | null,
): React.ReactNode {
  if (!text) return text;
  const snippetIndex = buildSnippetIndex(snippets);

  type Hit = { start: number; end: number; url: string; label: string };
  const hits: Hit[] = [];
  for (const rule of buildRules(urlMap)) {
    rule.pattern.lastIndex = 0;
    let m: RegExpExecArray | null;
    while ((m = rule.pattern.exec(text)) !== null) {
      if (!m[0]) continue;
      hits.push({
        start: m.index,
        end: m.index + m[0].length,
        url: rule.url(m),
        label: m[0],
      });
    }
  }

  if (hits.length === 0) return text;

  // Sort by start ASC, then by length DESC so longer matches win on overlap.
  hits.sort((a, b) => (a.start - b.start) || (b.end - b.start) - (a.end - a.start));
  const filtered: Hit[] = [];
  let cursor = 0;
  for (const h of hits) {
    if (h.start < cursor) continue;
    filtered.push(h);
    cursor = h.end;
  }

  const parts: React.ReactNode[] = [];
  let last = 0;
  filtered.forEach((h, i) => {
    if (h.start > last) parts.push(text.slice(last, h.start));
    const key = citationKey(h.label);
    const snippet = key ? snippetIndex.get(key) : undefined;
    parts.push(
      snippet ? (
        <GroundedCitation key={`reg-${i}-${h.start}`} label={h.label} url={h.url} snippet={snippet} />
      ) : (
        <a
          key={`reg-${i}-${h.start}`}
          href={h.url}
          target="_blank"
          rel="noopener noreferrer"
          className="text-primary hover:underline"
          title={`Open source: ${h.url}`}
        >
          {h.label}
        </a>
      )
    );
    last = h.end;
  });
  if (last < text.length) parts.push(text.slice(last));
  return <>{parts}</>;
}
