import React, { useState, useRef, useCallback, useEffect, useMemo } from "react";
import { diffWordsWithSpace } from "diff";
import {
  FileDown, Loader2, Shield, AlertTriangle, CheckCircle2, ChevronDown, MapPin,
  FileText, RefreshCw, GitCompare, LayoutDashboard,
  Download, X, MessageCircle, Send, ChevronRight, ChevronLeft,
  MessageSquare, RotateCcw, Wand2, ArrowRight,
} from "lucide-react";
import { extractText } from "@/lib/extract-text";
import { linkifyRegulations, lookupRegulationUrl } from "@/lib/regulation-links";
import {
  startActionPackageJob, streamActionPackageJob, getActionPackageJobStatus, cancelActionPackageJob, exportGapAnalysis, exportDraftPolicy, exportUpdatedPolicy, fixAllGaps, healthCheck,
  getIndustries, startDraftJob, streamDraftJob, getDraftJobStatus, cancelDraftJob, sendChatMessage,
  type ComplianceActionPackage, type AnalysisResult, type GapRow,
  type IndustryOption,
  type DraftedPolicy, type ChatMessage, type RewrittenPolicy, type RewrittenPolicySection,
  type SourceSnippet,
  STATUS_LABELS,
} from "@/lib/api";
import { toast } from "sonner";
import {
  LimitationsBanner, StateSourcesList, ChatMarkdown, draftLimitations, UngroundedDraftBanner,
} from "@/components/ResultNotices";
import { VerificationBadge, VerificationSidePanel, badgeFor } from "@/components/VerificationBadge";
import { EVIDENCE_LABELS, PRIORITY_LABELS, analysisLimitations, evidenceStatusOf, findingsSummary, priorityOf, countByEvidence, mustFixCount, revisionSectionFor, policySectionRef, isSilentQuote } from "@/lib/findings";

// ── Style maps ──

const JOB_KEY = "tpl_active_job";
const DRAFT_JOB_KEY = "tpl_active_draft_job";
// Everything this page keeps in the browser. Start fresh clears all of it.
const STORED_KEYS = ["tpl_text", "tpl_fileName", "tpl_pkg", "tpl_mode", "tpl_draftDesc", "tpl_draftResult", "tpl_revisions", JOB_KEY, DRAFT_JOB_KEY];

// One analysis per document version. The original report lives in `pkg` and is
// never overwritten by a re-check; each re-check of a proposed revision is kept
// here under that revision's number. `originalId` ties them to the report they
// revise, so a new analysis can never show an old report's re-check.
type RevisionAnalysis = {
  pkg: ComplianceActionPackage;
  sections: RewrittenPolicySection[];
  checkedAt: string;
};
type VersionStore = { originalId: string; version: number; results: Record<number, RevisionAnalysis> };

function formatElapsed(totalSeconds: number): string {
  const m = Math.floor(totalSeconds / 60);
  const s = totalSeconds % 60;
  return m > 0 ? `${m}:${s.toString().padStart(2, "0")}` : `${s}s`;
}

// ── Demo samples ──
// Realistic-but-flawed sample policy so first-time visitors can see what
// the analyzer actually does on a real document.
const SAMPLE_POLICY_TEXTS: Record<string, string> = {
  healthcare: `HIPAA PRIVACY AND SECURITY POLICY
Northbridge Regional Hospital, P.C.
Adopted: January 2019  |  Last reviewed: March 2022

1. PURPOSE
Northbridge Regional Hospital is committed to protecting the privacy of patient health information. All employees must follow this policy.

2. SCOPE
This policy applies to all staff, contractors, and volunteers who handle patient information across our inpatient and outpatient departments.

3. PROTECTED HEALTH INFORMATION
Staff should keep patient information confidential and only share it when necessary for treatment, payment, or operations.

4. ACCESS CONTROLS
Each employee has a username and password to access our electronic health record system. Passwords should be changed periodically. The office manager maintains a list of who has access.

5. WORKFORCE TRAINING
New hires complete a one-time HIPAA training video during onboarding. Annual refresher training is encouraged but not mandatory.

6. BREACH RESPONSE
If a staff member becomes aware of a possible breach of patient information, they should notify the office manager as soon as possible. The office manager will investigate and take appropriate action.

7. BUSINESS ASSOCIATES
The practice uses outside vendors for billing, IT support, and our patient portal. We trust these vendors to handle patient data responsibly.

8. PATIENT RIGHTS
Patients may request a copy of their medical records by submitting a written request. The office will respond within a reasonable time.

9. SANCTIONS
Violations of this policy may result in disciplinary action, up to and including termination.

10. POLICY REVIEW
This policy is reviewed by the office manager every two to three years.`,

  home_health: `HOME HEALTH AIDE SUPERVISION & CARE COORDINATION POLICY
Meridian Home Health Services, LLC
Adopted: March 2020  |  Last reviewed: January 2023

1. PURPOSE
Meridian Home Health Services is committed to providing safe, high-quality skilled home health care in compliance with Medicare Conditions of Participation. All clinical and aide staff must follow this policy.

2. SCOPE
This policy applies to all registered nurses, home health aides, and clinical supervisors providing services to patients under an active plan of care.

3. COMPREHENSIVE ASSESSMENT
The admitting clinician completes a comprehensive assessment, including OASIS data collection where required, at the start of care. Reassessments are completed periodically to reflect the patient's condition.

4. PLAN OF CARE
Each patient has a physician-signed plan of care. The plan of care is reviewed by the care team as needed based on clinical judgment.

5. HOME HEALTH AIDE SUPERVISION
Registered nurses supervise home health aides providing hands-on care. Supervisory visits are conducted periodically to ensure quality of care and are documented in the patient's chart.

6. AIDE COMPETENCY & TRAINING
New home health aides complete an orientation covering basic caregiving skills before their first patient assignment. Ongoing training is available through the agency's online learning portal.

7. MISSED OR LATE VISITS
If a scheduled visit is missed, the assigned clinician or aide should notify the office. The scheduling coordinator will work to reschedule the visit.

8. QUALITY ASSESSMENT & PERFORMANCE IMPROVEMENT (QAPI)
The agency reviews clinical outcomes and patient satisfaction data periodically to identify opportunities for improvement.

9. PATIENT RIGHTS
Patients are provided a copy of their rights and responsibilities at the start of care, including the right to file a complaint.

10. SANCTIONS
Failure to follow this policy may result in corrective action, up to and including termination.

11. POLICY REVIEW
This policy is reviewed by the Director of Nursing on an as-needed basis.`,

  other: `WHISTLEBLOWER AND NON-RETALIATION POLICY
Fieldstone Nonprofit Alliance
Adopted: June 2021  |  Last reviewed: August 2023

1. PURPOSE
Fieldstone Nonprofit Alliance encourages staff and volunteers to report suspected wrongdoing without fear of retaliation.

2. SCOPE
This policy applies to all employees, board members, contractors, and volunteers.

3. PROTECTED DISCLOSURES
Staff may report concerns about financial misconduct, legal violations, or unsafe practices to their supervisor or the Executive Director.

4. REPORTING PROCESS
Reports should be made in writing when possible. The organization will look into reports as time allows.

5. CONFIDENTIALITY
The organization will try to keep the identity of the reporting individual confidential.

6. NON-RETALIATION
Retaliation against anyone who reports a concern in good faith is not tolerated.

7. INVESTIGATION
The Executive Director will determine how to handle each report on a case-by-case basis.

8. SANCTIONS
Violations of this policy may result in disciplinary action, up to and including termination.

9. POLICY REVIEW
This policy is reviewed periodically by the board.`,
};

const SAMPLE_DRAFT_DESCRIPTIONS: Record<string, string> = {
  healthcare:
    "HIPAA workforce sanctions policy for a 50-bed acute care hospital in NYC. Should cover progressive discipline for unauthorized PHI access, repeat offenders, and accidental vs. willful violations. Need to align with 45 CFR 164.530(e) and our state breach notification timelines.",
  home_health:
    "Home health aide supervision policy for a Medicare-certified home health agency in NY. Should cover RN supervisory visit cadence for aide-only patients, competency evaluation, documentation requirements, and what happens if a supervisory visit is missed. Align with 42 CFR 484.80 and NY home care licensure.",
  other:
    "Whistleblower and non-retaliation policy for a 30-person nonprofit. Cover protected disclosures, who reports go to, confidentiality, investigation steps, and protections against retaliation.",
};

const STATUS_MAP: Record<string, { label: string; color: string; bg: string }> = {
  compliant: { label: "Compliant", color: "hsl(160 60% 36%)", bg: "hsl(160 60% 42% / 0.1)" },
  partial:   { label: "Partial",   color: "hsl(38 85% 44%)",  bg: "hsl(38 85% 52% / 0.1)" },
  gap:       { label: "Gap",       color: "hsl(25 90% 44%)",  bg: "hsl(25 90% 50% / 0.1)" },
  missing:   { label: "Missing",   color: "hsl(0 72% 48%)",   bg: "hsl(0 72% 51% / 0.1)" },
};

// Two tiers, not four -- a "Moderate" bucket reads as safe to skip. Everything
// is either a real compliance/legal exposure (Must Fix) or a best-practice
// improvement (Should Fix); nothing gets a middle ground to hide in.
const RISK_MAP: Record<string, { label: string; color: string; bg: string }> = {
  critical: { label: "MUST FIX",    color: "hsl(0 72% 48%)",  bg: "hsl(0 72% 51% / 0.1)" },
  high:     { label: "MUST FIX",    color: "hsl(0 72% 48%)",  bg: "hsl(0 72% 51% / 0.1)" },
  moderate: { label: "SHOULD FIX",  color: "hsl(38 85% 44%)", bg: "hsl(38 85% 52% / 0.1)" },
  low:      { label: "SHOULD FIX",  color: "hsl(38 85% 44%)", bg: "hsl(38 85% 52% / 0.1)" },
  compliant:{ label: "COMPLIANT",   color: "hsl(160 60% 36%)", bg: "hsl(160 60% 42% / 0.1)" },
};

// What kind of duty a finding asserts. A compliance officer's first question
// about any finding is "do I have to do this, or are you recommending it?" —
// and until now the screen gave the same answer for both. Only
// "unverified_requirement" is set by the server rather than the model: the
// finding claimed a legal mandate the cited source does not establish.
const OBLIGATION_MAP: Record<string, { label: string; color: string; bg: string; title: string }> = {
  required: {
    label: "REQUIRED BY LAW", color: "hsl(0 72% 48%)", bg: "hsl(0 72% 51% / 0.1)",
    title: "The cited regulation imposes this duty in mandatory terms.",
  },
  guidance: {
    label: "AGENCY GUIDANCE", color: "hsl(220 60% 45%)", bg: "hsl(220 60% 50% / 0.1)",
    title: "An agency recommends or interprets this. It is not black-letter law.",
  },
  best_practice: {
    label: "BEST PRACTICE", color: "hsl(200 55% 38%)", bg: "hsl(200 55% 45% / 0.1)",
    title: "Sound compliance practice, but not legally mandated.",
  },
  organizational_choice: {
    label: "YOUR CHOICE", color: "hsl(240 10% 45%)", bg: "hsl(240 10% 50% / 0.09)",
    title: "A standard your organization may adopt. No regulation requires it.",
  },
};

function stripCiteTags(text: string): string {
  // Also drops bracketed verdict markers the model or older results put in
  // the text ("[MODEL INFERENCE — NOT VERIFIED FROM LOADED SOURCES]"): the
  // verdict is shown once, by the finding's badge.
  return text
    .replace(/<cite[^>]*>|<\/cite>/g, "")
    .replace(/\s*\[[^\]]*\bNOT VERIFIED\b[^\]]*\]\s*/gi, " ")
    .trim();
}

// ── Tab configuration ──

const TABS = [
  { key: "overview", label: "Overview", icon: LayoutDashboard },
  { key: "gap_analysis", label: "Gap Analysis", icon: AlertTriangle },
  { key: "corrected", label: "Proposed revision", icon: CheckCircle2 },
] as const;

type TabKey = typeof TABS[number]["key"];

// ── Gap Row Component ──

function GapRowItem({ row, urlMap, snippets, verifying = false, sectionLabel }: { row: GapRow; urlMap?: Record<string, string>; snippets?: SourceSnippet[] | null; verifying?: boolean; sectionLabel?: string | null }) {
  const [open, setOpen] = useState(false);
  const [panelOpen, setPanelOpen] = useState(false);
  // Two independent badges: how urgent (priority) and whether it is
  // established law (evidence). Neither is derived from the other.
  const badge = badgeFor(row, verifying);
  const priority = priorityOf(row);
  const isRecommendation = evidenceStatusOf(row) === "recommendation";
  const policySection = policySectionRef(row);
  const canOpen = badge.state !== "checking"
    && (!isRecommendation || (row.evidence?.source?.passages?.length ?? 0) > 0);
  const r = RISK_MAP[priority === "must_fix" ? "high" : "moderate"];

  return (
    <div className={`rounded-xl overflow-hidden mb-3 transition-shadow duration-200 ${open ? "neu-raised" : "neu-sm"}`}>
      <div className="flex flex-wrap items-center gap-2 px-4 sm:px-5 pt-3.5 sm:pt-4">
        <span className="text-[9px] sm:text-[10px] font-mono font-bold tracking-wider px-2 sm:px-2.5 py-1 rounded-full shrink-0" title="Priority: how urgent, independent of the evidence" style={{ color: r.color, border: `1.5px solid ${r.color}40`, background: r.bg }}>{PRIORITY_LABELS[priority].toUpperCase()}</span>
        <VerificationBadge state={badge.state} label={badge.label} onOpen={canOpen ? () => setPanelOpen(true) : undefined} />
        {sectionLabel && (
          <span className="text-[10px] font-mono px-2 py-0.5 rounded-full bg-secondary text-muted-foreground" title="The section of the proposed revision this finding concerns">
            Revision section: {sectionLabel}
          </span>
        )}
      </div>
      <button onClick={() => setOpen((o) => !o)} className="w-full flex items-start gap-2.5 sm:gap-3 px-4 sm:px-5 pt-2 pb-3.5 sm:pb-4 text-left active:opacity-80 transition-all touch-manipulation">
        <div className="flex-1 min-w-0">
          <p className="text-[13px] sm:text-sm font-semibold text-foreground">{stripCiteTags(row.clause)}</p>
          {/* The finding is written once: clipped while collapsed, in full when open. */}
          <p className={`text-[11px] sm:text-xs text-muted-foreground mt-1 leading-relaxed ${open ? "" : "line-clamp-2"}`}>{stripCiteTags(row.finding)}</p>
        </div>
        <ChevronDown className="w-4 h-4 text-muted-foreground shrink-0 mt-1 transition-transform duration-200" style={{ transform: open ? "rotate(180deg)" : "none" }} />
      </button>
      {panelOpen && <VerificationSidePanel row={row} badge={badge} onClose={() => setPanelOpen(false)} />}
      {open && (
        <div className="px-4 sm:px-5 pb-4 sm:pb-5 pt-0 space-y-3">
          <div className="h-px bg-border" />
          {/* What the user's own policy says about this, first, so the finding
              reads against the document it is about. */}
          {(row.current_state || policySection) && (
            <div className="rounded-xl p-3 sm:p-3.5 border-l-4" style={{ borderLeftColor: "hsl(var(--primary))", background: "hsl(var(--primary) / 0.06)" }}>
              <div className="flex items-center gap-2 flex-wrap mb-1">
                <p className="text-[10px] font-mono uppercase tracking-wider font-bold" style={{ color: "hsl(var(--primary))" }}>
                  {isSilentQuote(row.current_state) ? "Your policy" : "Your policy says"}
                </p>
                {policySection && (
                  <span className="text-[10px] font-mono px-2 py-0.5 rounded-full bg-background text-foreground/80">{policySection}</span>
                )}
              </div>
              {row.current_state && (
                isSilentQuote(row.current_state)
                  ? <p className="text-[13px] text-foreground/85 leading-relaxed">{stripCiteTags(row.current_state).replace(/^["“]|["”]$/g, "")}</p>
                  : <p className="text-[13px] sm:text-[14px] text-foreground leading-relaxed font-serif-display italic">“{linkifyRegulations(stripCiteTags(row.current_state).replace(/^["“]|["”]$/g, ""), urlMap, snippets)}”</p>
              )}
            </div>
          )}
          <div className="flex flex-wrap gap-2 items-center">
            <span className="text-[10px] font-mono uppercase tracking-wider text-muted-foreground">Priority:</span>
            <span className="text-[10px] font-mono font-bold px-2 py-0.5 rounded-full" style={{ color: r.color, background: r.bg }}>{PRIORITY_LABELS[priority]}</span>
            {row.remediation_priority && row.remediation_priority !== "N/A" && (
              <><span className="text-[10px] font-mono text-muted-foreground">Timeline:</span>
              <span className="text-[10px] font-mono font-medium px-2 py-0.5 rounded-full bg-secondary text-muted-foreground">{row.remediation_priority}</span></>
            )}
          </div>
          {row.regulations?.length > 0 && (
            <div>
              <p className="text-[10px] font-mono uppercase tracking-wider text-muted-foreground mb-1.5 font-medium">Applicable Regulations</p>
              <div className="flex flex-wrap gap-1.5">
                {row.regulations.map((reg, i) => {
                  const url = lookupRegulationUrl(reg, urlMap);
                  return url ? (
                    <a
                      key={i}
                      href={url}
                      target="_blank"
                      rel="noopener noreferrer"
                      title={`Open source: ${url}`}
                      className="text-[9px] sm:text-[10px] font-mono px-2 py-0.5 rounded-full bg-secondary text-primary neu-sm hover:underline inline-flex items-center gap-0.5"
                    >
                      {reg}<span aria-hidden="true">↗</span>
                    </a>
                  ) : (
                    <span key={i} className="text-[9px] sm:text-[10px] font-mono px-2 py-0.5 rounded-full bg-secondary text-muted-foreground neu-sm">{reg}</span>
                  );
                })}
              </div>
            </div>
          )}
          <div>
            <div className="flex items-center gap-2 mb-1 flex-wrap">
              <p className="text-[10px] font-mono uppercase tracking-wider text-muted-foreground font-medium">Finding type</p>
              <span
                title={row.finding_kind === "implementation_question"
                  ? "The document covers this; whether your organization actually does it is a question only you can answer."
                  : "What the document says or omits. No records or practices were inspected."}
                className="text-[9px] font-mono font-bold px-2 py-0.5 rounded-full tracking-wider bg-secondary text-muted-foreground"
              >
                {row.finding_kind === "implementation_question" ? "PRACTICE QUESTION" : "DOCUMENT GAP"}
              </span>
              {(() => {
                // Only a recommendation gets an obligation chip (best practice,
                // guidance, your choice). Whether something is required by law
                // is the evidence badge's job, so a recommendation can never
                // read "Required by law" and a requirement never gets two verdicts.
                if (!isRecommendation) return null;
                const o = OBLIGATION_MAP[row.obligation_type === "required" || row.obligation_type === "unverified_requirement" ? "best_practice" : (row.obligation_type || "best_practice")];
                if (!o) return null;
                return (
                  <span
                    title={o.title}
                    className="text-[9px] font-mono font-bold px-2 py-0.5 rounded-full tracking-wider"
                    style={{ color: o.color, background: o.bg }}
                  >
                    {o.label}
                  </span>
                );
              })()}
            </div>
            {row.regulatory_requirement && !isRecommendation && (
              <div className="rounded-lg p-3 mt-2 border-l-2" style={{ borderColor: "hsl(var(--primary) / 0.5)", background: "hsl(var(--primary) / 0.05)" }}>
                <p className="text-[10px] font-mono uppercase tracking-wider mb-1 font-medium" style={{ color: "hsl(var(--primary))" }}>
                  What the regulation requires
                </p>
                <p className="text-[12px] sm:text-[13px] text-foreground leading-relaxed">{linkifyRegulations(stripCiteTags(row.regulatory_requirement), urlMap, snippets)}</p>
              </div>
            )}
            {row.recommendations && row.recommendations.length > 0 && (
              <div className="rounded-lg p-3 mt-2 border-l-2 border-foreground/15">
                <p className="text-[10px] font-mono uppercase tracking-wider mb-1 font-medium text-muted-foreground">
                  Recommendations — best practice, not required by the cited regulation
                </p>
                <ul className="list-disc pl-4 space-y-0.5">
                  {row.recommendations.map((rec, i) => (
                    <li key={i} className="text-[12px] text-foreground/80 leading-relaxed">{rec}</li>
                  ))}
                </ul>
              </div>
            )}
            {row.implementation_question && (
              <p className="text-[12px] text-foreground/80 leading-relaxed mt-2">
                <span className="font-semibold">Question for your team: </span>{row.implementation_question}
              </p>
            )}
          </div>
          <div className="rounded-xl p-3 sm:p-4 neu-inset">
            <p className="text-[10px] font-mono uppercase tracking-wider mb-1.5 font-medium" style={{ color: "hsl(var(--primary))" }}>Suggested Policy Language</p>
            <p className="text-[13px] sm:text-sm text-foreground/85 italic leading-relaxed">"{linkifyRegulations(stripCiteTags(row.suggested_language), urlMap, snippets)}"</p>
          </div>
          <p className="text-[10px] font-mono text-muted-foreground break-all">Cite: {linkifyRegulations(stripCiteTags(row.citation), urlMap, snippets)}</p>
        </div>
      )}
    </div>
  );
}

// ── Main Page ──

// One plain sentence per industry for the landing card. The server's
// descriptions are written for configuration, not for a first-time reader;
// any industry not listed here falls back to them.
const INDUSTRY_BLURBS: Record<string, string> = {
  healthcare: "Hospitals and hospital systems.",
  home_health: "Home health and home care agencies.",
  pharmacy: "Retail, hospital, specialty and compounding pharmacies.",
};

const FALLBACK_INDUSTRIES: IndustryOption[] = [
  { slug: "healthcare", name: "Hospitals", icon: "🏥", description: "Acute care hospitals, hospital systems, hospital-based compliance and privacy programs" },
  { slug: "home_health", name: "Home Health", icon: "🏠", description: "Medicare-certified home health agencies, home care agencies" },
  { slug: "pharmacy", name: "Pharmacy", icon: "💊", description: "Retail, hospital, long-term care, specialty, and compounding pharmacies" },
];

export default function Index() {
  const [text, setText] = useState(() => { try { return localStorage.getItem("tpl_text") || ""; } catch { return ""; } });
  const [fileName, setFileName] = useState(() => { try { return localStorage.getItem("tpl_fileName") || ""; } catch { return ""; } });
  const [pkg, setPkg] = useState<ComplianceActionPackage | null>(() => {
    try { const s = localStorage.getItem("tpl_pkg"); return s ? JSON.parse(s) : null; } catch { return null; }
  });
  const [loading, setLoading] = useState(false);
  const [pkgStreaming, setPkgStreaming] = useState(false);
  const [showRedline, setShowRedline] = useState(false);
  const [draftStreamText, setDraftStreamText] = useState("");
  const [loadSec, setLoadSec] = useState(0);
  const [error, setError] = useState("");
  const [drag, setDrag] = useState(false);
  const [parsing, setParsing] = useState(false);
  const [exporting, setExporting] = useState(false);
  const [fixingGaps, setFixingGaps] = useState(false);
  const [correctedExporting, setCorrectedExporting] = useState(false);
  // A fresh analysis of the proposed revision. The only thing allowed to say
  // the revision resolves the findings.
  const [rechecking, setRechecking] = useState(false);
  const [versions, setVersions] = useState<VersionStore | null>(() => {
    try { const s = localStorage.getItem("tpl_revisions"); return s ? JSON.parse(s) : null; } catch { return null; }
  });
  // Which analysis the banner, Overview and Gap Analysis show.
  const [analysisView, setAnalysisView] = useState<"original" | "revision">("original");
  const [industry, setIndustry] = useState("healthcare");
  const [industries, setIndustries] = useState<IndustryOption[]>(FALLBACK_INDUSTRIES);
  const [stateCode, setStateCode] = useState("");
  const jurisdiction = stateCode;
  const [backendOnline, setBackendOnline] = useState(true);
  const [activeTab, setActiveTab] = useState<TabKey>("overview");
  // Set true the instant the user hits Cancel. Any generation in flight
  // (whether from run() or the resume-on-mount reattach) checks this before
  // applying its result, so an abandoned job can't pop a stale result/toast
  // back onto the screen after the user has already left.
  const cancelledRef = useRef(false);
  // Severity filter for the Gap Analysis tab's filter chips (Critical/High/Moderate).
  const [severityFilter, setSeverityFilter] = useState<"must_fix" | "should_fix" | null>(null);
  const [retryCount, setRetryCount] = useState(0);
  const DRAFT_PLACEHOLDERS: Record<string, string> = {
    healthcare: [
      'e.g. "HIPAA workforce training policy for a hospital"',
      'e.g. "Security incident response policy for a medical practice"',
    ].join("\n"),
    home_health: [
      'e.g. "Home health aide supervision policy for a Medicare-certified agency"',
      'e.g. "Patient rights policy for a home health agency per 42 CFR 484.50"',
    ].join("\n"),
    other: [
      'e.g. "Remote work policy for a small business"',
      'e.g. "Whistleblower and non-retaliation policy for a nonprofit"',
    ].join("\n"),
  };

  const [mode, setMode] = useState<"analyze" | "draft">(() => {
    try {
      const stored = localStorage.getItem("tpl_mode");
      return stored === "analyze" || stored === "draft" ? stored : "draft";
    } catch { return "draft"; }
  });
  const [draftDesc, setDraftDesc] = useState(() => {
    try { return localStorage.getItem("tpl_draftDesc") || ""; } catch { return ""; }
  });
  const [draftResult, setDraftResult] = useState<DraftedPolicy | null>(() => {
    try { const s = localStorage.getItem("tpl_draftResult"); return s ? JSON.parse(s) : null; } catch { return null; }
  });
  const [draftExporting, setDraftExporting] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);
  const analyzeButtonRef = useRef<HTMLButtonElement>(null);
  // Whether this page opened with work restored from browser storage, so the
  // user can see that it was kept and clear it.
  const [restoredFromBrowser] = useState(() => {
    try {
      return ["tpl_text", "tpl_pkg", "tpl_draftDesc", "tpl_draftResult"].some((k) => !!localStorage.getItem(k));
    } catch { return false; }
  });

  // ── Chat state ──
  const [chatOpen, setChatOpen] = useState(false);
  const [chatMode, setChatMode] = useState<"analysis" | "draft">("analysis");
  const [chatHistory, setChatHistory] = useState<ChatMessage[]>([]);
  const [chatInput, setChatInput] = useState("");
  const [chatLoading, setChatLoading] = useState(false);
  const chatEndRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const check = () => healthCheck().then(setBackendOnline);
    check();
    const interval = setInterval(check, 15000);
    return () => clearInterval(interval);
  }, []);

  // Resume in-flight analysis on mount (or after page reload). If the user
  // started an analysis, then tabbed away / refreshed / closed and reopened
  // the page, we reattach to the same server-side job and pick up exactly
  // where it is right now. The job keeps running on the server regardless of
  // what the browser does — this effect just rewires the UI to it.
  useEffect(() => {
    let cancelled = false;
    const resumeJob = async () => {
      let jobId: string | null = null;
      try { jobId = localStorage.getItem(JOB_KEY); } catch { return; }
      if (!jobId) return;

      try {
        const snapshot = await getActionPackageJobStatus(jobId);
        if (cancelled || cancelledRef.current) return;

        // Job expired or unknown — clear and move on.
        if (!snapshot) {
          try { localStorage.removeItem(JOB_KEY); } catch {}
          return;
        }

        // Already finished while we were away — load the result and stop.
        if (snapshot.status === "complete") {
          if (snapshot.package) {
            setDraftResult(null);
            setPkg(snapshot.package);
            setActiveTab("overview");
            toast.success("Analysis ready", { description: "Picked up where you left off." });
          }
          try { localStorage.removeItem(JOB_KEY); } catch {}
          return;
        }

        if (snapshot.status === "error") {
          setError(snapshot.error || "Analysis failed while you were away.");
          toast.error("Analysis Failed", { description: snapshot.error || "" });
          try { localStorage.removeItem(JOB_KEY); } catch {}
          return;
        }

        // Still running — show whatever progress exists and reattach to the stream.
        setMode("analyze");
        setActiveTab("overview");
        if (snapshot.package) {
          setDraftResult(null);
          setPkg(snapshot.package);
          setPkgStreaming(true);
          setLoading(false);
        } else {
          setLoading(true);
        }
        toast.info("Resuming analysis", { description: "Your analysis is still running on the server." });

        let firstUpdate = !snapshot.package;
        const onUpdate = (partialPkg: ComplianceActionPackage) => {
          if (cancelled || cancelledRef.current) return;
          setDraftResult(null);
          setPkg(partialPkg);
          if (firstUpdate) {
            firstUpdate = false;
            setLoading(false);
            setPkgStreaming(true);
          }
        };
        try {
          await streamActionPackageJob(jobId, onUpdate);
          if (cancelled || cancelledRef.current) return;
          try { localStorage.removeItem(JOB_KEY); } catch {}
          toast.success("Analysis complete", { description: "All outputs ready" });
        } catch (e: any) {
          if (cancelled || cancelledRef.current) return;
          const msg = e.message || "Generation failed.";
          setError(msg);
          toast.error("Analysis Failed", { description: msg });
          setLoading(false);
          try { localStorage.removeItem(JOB_KEY); } catch {}
        } finally {
          if (!cancelled && !cancelledRef.current) setPkgStreaming(false);
        }
      } catch {
        // Network blip during resume — leave the job key in place so a future
        // mount can try again.
      }
    };
    resumeJob();
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Same resume-on-mount pattern as the analysis job above, for drafting.
  useEffect(() => {
    let cancelled = false;
    const resumeDraftJob = async () => {
      let jobId: string | null = null;
      try { jobId = localStorage.getItem(DRAFT_JOB_KEY); } catch { return; }
      if (!jobId) return;

      try {
        const snapshot = await getDraftJobStatus(jobId);
        if (cancelled || cancelledRef.current) return;

        if (!snapshot) {
          try { localStorage.removeItem(DRAFT_JOB_KEY); } catch {}
          return;
        }

        if (snapshot.status === "complete") {
          if (snapshot.policy) {
            setPkg(null);
            setDraftResult(snapshot.policy);
            setMode("draft");
            toast.success("Draft ready", { description: "Picked up where you left off." });
          }
          try { localStorage.removeItem(DRAFT_JOB_KEY); } catch {}
          return;
        }

        if (snapshot.status === "error") {
          setError(snapshot.error || "Draft failed while you were away.");
          toast.error("Draft Failed", { description: snapshot.error || "" });
          try { localStorage.removeItem(DRAFT_JOB_KEY); } catch {}
          return;
        }

        // Still running — reattach to the stream.
        setMode("draft");
        setLoading(true);
        setDraftStreamText(snapshot.partial_text || "");
        toast.info("Resuming draft", { description: "Your draft is still generating on the server." });

        try {
          const data = await streamDraftJob(jobId, (fullTextSoFar) => {
            if (cancelled || cancelledRef.current) return;
            setDraftStreamText(fullTextSoFar);
          });
          if (cancelled || cancelledRef.current) return;
          try { localStorage.removeItem(DRAFT_JOB_KEY); } catch {}
          setPkg(null);
          setDraftResult(data);
          toast.success("Policy drafted", { description: data.policy_title });
        } catch (e: any) {
          if (cancelled || cancelledRef.current) return;
          const msg = e.message || "Draft failed.";
          setError(msg);
          toast.error("Draft Failed", { description: msg });
          try { localStorage.removeItem(DRAFT_JOB_KEY); } catch {}
        } finally {
          if (!cancelled && !cancelledRef.current) setLoading(false);
        }
      } catch {
        // Network blip during resume — leave the job key in place so a future
        // mount can try again.
      }
    };
    resumeDraftJob();
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Persist results across tab switches and page reloads
  useEffect(() => { try { localStorage.setItem("tpl_mode", mode); } catch {} }, [mode]);
  useEffect(() => { try { localStorage.setItem("tpl_draftDesc", draftDesc); } catch {} }, [draftDesc]);
  useEffect(() => { try { localStorage.setItem("tpl_text", text); } catch {} }, [text]);
  useEffect(() => { try { localStorage.setItem("tpl_fileName", fileName); } catch {} }, [fileName]);
  useEffect(() => {
    try {
      if (draftResult) localStorage.setItem("tpl_draftResult", JSON.stringify(draftResult));
      else localStorage.removeItem("tpl_draftResult");
    } catch {}
  }, [draftResult]);
  useEffect(() => {
    try {
      if (pkg) localStorage.setItem("tpl_pkg", JSON.stringify(pkg));
      else localStorage.removeItem("tpl_pkg");
    } catch {}
  }, [pkg]);
  useEffect(() => {
    try {
      if (versions) localStorage.setItem("tpl_revisions", JSON.stringify(versions));
      else localStorage.removeItem("tpl_revisions");
    } catch {}
  }, [versions]);

  // The re-check of the current proposed revision, if it has been run -- and
  // only for this report.
  const versionsForPkg = versions && pkg && versions.originalId === pkg.package_id ? versions : null;
  const revisionVersion = versionsForPkg?.version ?? 0;
  const revisionAnalysis = versionsForPkg?.results[revisionVersion] ?? null;
  const showingRevision = analysisView === "revision" && !!revisionAnalysis;
  const viewedPkg = showingRevision ? revisionAnalysis!.pkg : pkg;

  useEffect(() => {
    getIndustries().then((list) => { if (list.length > 0) setIndustries(list); });
  }, []);

  useEffect(() => {
    if (chatOpen && chatEndRef.current) {
      chatEndRef.current.scrollIntoView({ behavior: "smooth" });
    }
  }, [chatHistory, chatOpen]);

  useEffect(() => {
    if (!loading) { setLoadSec(0); return; }
    setLoadSec(0);
    const iv = setInterval(() => setLoadSec(s => s + 1), 1000);
    return () => clearInterval(iv);
  }, [loading]);

  const buildChatContext = () => {
    if (chatMode === "draft" && draftResult) {
      return `Policy Title: ${draftResult.policy_title}\nRegulations Applied: ${draftResult.regulations_applied.join(", ")}\n\nFull Policy:\n${draftResult.full_text}`;
    }
    if (pkg) {
      const ga = pkg.gap_analysis;
      return [
        `Policy Type: ${ga.policy_type}`,
        `Compliance Score: ${ga.compliance_score != null ? ga.compliance_score.toFixed(1) + "%" : "N/A"}`,
        `Findings: ${ga.critical_count} critical, ${ga.gap_count} gaps, ${ga.partial_count} partial, ${ga.compliant_count} compliant`,
        `Regulations Reviewed: ${ga.regulations_applied?.slice(0, 8).join(", ")}`,
        // The same summary and labels the report shows, so the chat cannot
        // call a recommendation mandatory either.
        `Summary: ${findingsSummary(ga.gap_table ?? [])}`,
        `Findings (evidence · priority):\n${(ga.gap_table ?? []).map((r, i) => `${i + 1}. ${r.clause} — ${EVIDENCE_LABELS[evidenceStatusOf(r)]} · ${PRIORITY_LABELS[priorityOf(r)]}`).join("\n")}`,
      ].filter(Boolean).join("\n");
    }
    return "";
  };

  const handleSendChat = async (messageOverride?: string) => {
    const override = typeof messageOverride === "string" ? messageOverride : undefined;
    const msg = (override ?? chatInput).trim();
    if (!msg || chatLoading) return;
    setChatInput("");
    const userMsg: ChatMessage = { role: "user", content: msg };
    setChatHistory((h) => [...h, userMsg]);
    setChatLoading(true);
    try {
      const ctx = buildChatContext();
      const res = await sendChatMessage(msg, chatMode, industry, jurisdiction, ctx, chatHistory);
      const assistantMsg: ChatMessage = { role: "assistant", content: res.response };
      setChatHistory((h) => [...h, assistantMsg]);
    } catch (e: any) {
      toast.error("Chat failed", { description: e.message });
    } finally {
      setChatLoading(false);
    }
  };

  const openChat = (mode: "analysis" | "draft") => {
    setChatMode(mode);
    setChatOpen(true);
  };

  const handleFile = useCallback(async (file: File) => {
    setParsing(true);
    setError("");
    try {
      const maxSize = 10 * 1024 * 1024;
      if (file.size > maxSize) throw new Error(`File too large (${(file.size / 1024 / 1024).toFixed(1)}MB). Max 10MB.`);
      setFileName(file.name);
      const extracted = await extractText(file);
      const isPdf = file.name.toLowerCase().endsWith(".pdf");
      const chars = extracted?.trim().length ?? 0;

      // A scanned PDF is a picture of a document: there is no text to read,
      // and the generic "could not extract readable text" gave no clue why or
      // what to do about it. Naming the actual cause turns a dead end into a
      // next step.
      if (chars < 50) {
        throw new Error(
          isPdf
            ? chars === 0
              ? "This PDF has no selectable text — it's almost certainly a scan or an image. Try a PDF exported from Word, or paste the policy text into the box below."
              : `This PDF only yielded ${chars} characters of selectable text, so it's likely a scan. Paste the policy text below instead.`
            : `This file only has ${chars} characters of readable text — need at least 50 to analyze.`,
        );
      }
      setText(extracted);
      toast.success("File loaded", { description: `${file.name} — ${extracted.length.toLocaleString()} chars` });
    } catch (e: any) {
      setError(e.message || "File parse failed.");
      setFileName("");
      toast.error("File error", { description: e.message });
    } finally {
      setParsing(false);
    }
  }, []);

  const run = async (isRetry = false) => {
    if (loading) return;
    cancelledRef.current = false;
    setError("");
    setLoading(true);
    if (isRetry) setRetryCount((c) => c + 1);
    else setRetryCount(0);

    if (mode === "draft") {
      if (!draftDesc.trim()) { setLoading(false); return; }
      setDraftStreamText("");
      let jobId: string | null = null;
      try {
        // Kick off the draft as a background job so it survives tabbing away,
        // backgrounding the app, or navigating off this screen. The job_id is
        // persisted to localStorage so we can reattach on remount/reload.
        jobId = await startDraftJob(draftDesc, industry, jurisdiction);
        if (cancelledRef.current) return;
        try { localStorage.setItem(DRAFT_JOB_KEY, jobId); } catch {}
        const data = await streamDraftJob(jobId, (fullTextSoFar) => {
          if (cancelledRef.current) return;
          setDraftStreamText(fullTextSoFar);
        });
        if (cancelledRef.current) return;
        try { localStorage.removeItem(DRAFT_JOB_KEY); } catch {}
        setPkg(null);
        setDraftResult(data);
        toast.success("Policy drafted", { description: data.policy_title });
      } catch (e: any) {
        if (cancelledRef.current) return;
        // The SSE connection can drop (tab backgrounded, network blip) without
        // the server-side job actually failing -- check the real state before
        // declaring failure and discarding the job key.
        let resolved = false;
        try {
          const snapshot = jobId ? await getDraftJobStatus(jobId) : null;
          if (cancelledRef.current) return;
          if (snapshot?.status === "complete" && snapshot.policy) {
            setPkg(null);
            setDraftResult(snapshot.policy);
            toast.success("Policy drafted", { description: snapshot.policy.policy_title });
            try { localStorage.removeItem(DRAFT_JOB_KEY); } catch {}
            resolved = true;
          } else if (snapshot?.status === "running") {
            toast.info("Connection lost", { description: "Still generating on the server — check back in a bit and it'll pick up where it left off." });
            resolved = true;
          } else if (snapshot?.status === "error") {
            const msg = snapshot.error || "Draft failed.";
            setError(msg);
            toast.error("Draft Failed", { description: msg });
            try { localStorage.removeItem(DRAFT_JOB_KEY); } catch {}
            resolved = true;
          }
        } catch {
          // Status check itself failed too -- fall through to generic handling.
        }
        if (!resolved) {
          const msg = e.message || "Draft failed.";
          setError(msg);
          toast.error("Draft Failed", { description: msg });
          try { localStorage.removeItem(DRAFT_JOB_KEY); } catch {}
        }
      } finally {
        if (!cancelledRef.current) {
          setLoading(false);
          setDraftStreamText("");
        }
      }
    } else {
      if (!text.trim()) { setLoading(false); return; }
      setActiveTab("overview");
      setPkgStreaming(false);
      let firstUpdate = true;
      const onUpdate = (partialPkg: ComplianceActionPackage) => {
        if (cancelledRef.current) return;
        setDraftResult(null);
        setPkg(partialPkg);
        if (firstUpdate) {
          firstUpdate = false;
          setLoading(false);
          setPkgStreaming(true);
        }
      };
      let jobId: string | null = null;
      try {
        // Kick off the analysis as a background job on the server. The job_id is
        // persisted to localStorage so we can reattach if the user tabs away,
        // navigates, or reloads the page mid-analysis.
        jobId = await startActionPackageJob(text, fileName, industry, jurisdiction, true);
        if (cancelledRef.current) return;
        try { localStorage.setItem(JOB_KEY, jobId); } catch {}
        await streamActionPackageJob(jobId, onUpdate);
        if (cancelledRef.current) return;
        try { localStorage.removeItem(JOB_KEY); } catch {}
        toast.success("Analysis complete", { description: "All outputs ready" });
      } catch (e: any) {
        if (cancelledRef.current) return;
        // The SSE connection can drop (tab backgrounded, network blip) without
        // the server-side job actually failing -- check the real state before
        // declaring failure and discarding the job key.
        let resolved = false;
        try {
          const snapshot = jobId ? await getActionPackageJobStatus(jobId) : null;
          if (cancelledRef.current) return;
          if (snapshot?.status === "complete" && snapshot.package) {
            setDraftResult(null);
            setPkg(snapshot.package);
            setLoading(false);
            toast.success("Analysis complete", { description: "All outputs ready" });
            try { localStorage.removeItem(JOB_KEY); } catch {}
            resolved = true;
          } else if (snapshot?.status === "running") {
            toast.info("Connection lost", { description: "Still running on the server — check back in a bit and it'll pick up where it left off." });
            setLoading(false);
            resolved = true;
          } else if (snapshot?.status === "error") {
            const msg = snapshot.error || "Generation failed.";
            setError(msg);
            toast.error("Generation Failed", { description: msg });
            setLoading(false);
            try { localStorage.removeItem(JOB_KEY); } catch {}
            resolved = true;
          }
        } catch {
          // Status check itself failed too -- fall through to generic handling.
        }
        if (!resolved) {
          const msg = e.message || "Generation failed.";
          setError(msg);
          toast.error("Generation Failed", { description: msg });
          setLoading(false);
          try { localStorage.removeItem(JOB_KEY); } catch {}
        }
      } finally {
        if (!cancelledRef.current) setPkgStreaming(false);
      }
    }
  };

  /** Cancel button on the loading screen — stops waiting immediately and
   * tells the server to actually stop the generation (not just abandon it). */
  const cancelGeneration = () => {
    cancelledRef.current = true;
    if (mode === "draft") {
      let jobId: string | null = null;
      try { jobId = localStorage.getItem(DRAFT_JOB_KEY); } catch {}
      try { localStorage.removeItem(DRAFT_JOB_KEY); } catch {}
      if (jobId) cancelDraftJob(jobId);
      setDraftStreamText("");
    } else {
      let jobId: string | null = null;
      try { jobId = localStorage.getItem(JOB_KEY); } catch {}
      try { localStorage.removeItem(JOB_KEY); } catch {}
      if (jobId) cancelActionPackageJob(jobId);
      setPkgStreaming(false);
    }
    setLoading(false);
    setError("");
  };

  const reset = () => {
    setText("");
    setFileName("");
    setPkg(null);
    setPkgStreaming(false);
    setDraftResult(null);
    setDraftDesc("");
    setError("");
    setRetryCount(0);
    setActiveTab("overview");
    setIndustry("healthcare");
    setChatOpen(false);
    setChatHistory([]);
    setChatInput("");
    setVersions(null);
    setAnalysisView("original");
    setStateCode("");
    if (fileRef.current) fileRef.current.value = "";
    // Clear the browser copy too, not just the screen -- otherwise the
    // previous policy and results reappear the next time the page opens.
    try { STORED_KEYS.forEach((k) => localStorage.removeItem(k)); } catch {}
  };

  const handleDownloadGapAnalysis = async () => {
    if (!pkg || exporting) return;
    setExporting(true);
    try {
      // The report on screen: the original analysis or the revision's re-check.
      await exportGapAnalysis(viewedPkg ?? pkg);
      toast.success("Gap analysis downloaded", { description: "Report .docx saved" });
    } catch (e: any) {
      toast.error("Download failed", { description: e.message });
    } finally {
      setExporting(false);
    }
  };

  const handleFixAllGaps = async () => {
    if (!pkg?.gap_analysis || fixingGaps) return;
    setFixingGaps(true);
    try {
      const rewritten = await fixAllGaps(text, pkg.gap_analysis, industry, jurisdiction);
      setPkg((prev) => prev ? { ...prev, rewritten_policy: rewritten } : prev);
      // A new revision is a new version: it has not been re-checked, and the
      // previous revision's re-check (kept under its own number) no longer
      // describes the text on screen.
      const originalId = pkg.package_id;
      setVersions((prev) => prev && prev.originalId === originalId
        ? { ...prev, version: prev.version + 1 }
        : { originalId, version: 1, results: {} });
      setAnalysisView("original");
      setActiveTab("corrected");
      toast.info("Proposed revision ready", { description: "Not re-checked yet. Use Re-check to analyze it before relying on it." });
    } catch (e: any) {
      toast.error("Revision failed", { description: e.message });
    } finally {
      setFixingGaps(false);
    }
  };

  // Runs a fresh analysis of the proposed revision. An all-clear is shown only
  // when that analysis actually finds nothing left -- never just because a
  // revision was generated.
  const handleRecheck = async () => {
    const revised = pkg?.rewritten_policy?.full_text;
    if (!pkg || !revised || rechecking) return;
    const originalId = pkg.package_id;
    const version = versionsForPkg?.version || 1;
    const sections = pkg.rewritten_policy?.sections ?? [];
    setRechecking(true);
    try {
      const jobId = await startActionPackageJob(revised, fileName ? `${fileName} (proposed revision)` : "Proposed revision", industry, jurisdiction, true);
      const result = await streamActionPackageJob(jobId, () => {});
      // Stored beside the original, never in place of it.
      setVersions((prev) => {
        const base = prev && prev.originalId === originalId ? prev : { originalId, version, results: {} };
        return { ...base, version, results: { ...base.results, [version]: { pkg: result, sections, checkedAt: new Date().toISOString() } } };
      });
      const rows = result.gap_analysis?.gap_table ?? [];
      const remaining = rows.length;
      const mustFix = mustFixCount(rows);
      if (remaining === 0) {
        toast.success("Re-check found no remaining gaps", { description: "A fresh analysis of the revision found nothing left. It still needs review by counsel." });
      } else {
        toast.info(`Re-check found ${remaining} remaining issue${remaining !== 1 ? "s" : ""}`, { description: mustFix ? `${mustFix} marked must-fix.` : "None marked must-fix." });
      }
    } catch (e: any) {
      toast.error("Re-check failed", { description: e.message });
    } finally {
      setRechecking(false);
    }
  };

  // Determine which tabs have data
  const availableTabs = pkg ? TABS.filter((t) => {
    if (t.key === "overview") return true;
    if (t.key === "gap_analysis") return !!pkg.gap_analysis;
    if (t.key === "corrected") return !!pkg.rewritten_policy;
    return false;
  }) : [];

  return (
    <div className="min-h-screen text-foreground">
      {/* Header */}
      <header className="aqua-kiss aqua-bar sticky top-0 z-10 px-4 sm:px-8 py-3.5 flex items-center justify-between gap-2 relative">
        <div className="flex flex-col min-w-0 flex-1">
          <span className="nyt-masthead text-[26px] sm:text-3xl text-foreground whitespace-nowrap overflow-visible leading-[1.3] py-0.5 inline-block">
            The Policy Lab
          </span>
        </div>
        <div className="flex items-center gap-2 shrink-0">
          {!backendOnline && (
            <span className="text-[9px] font-mono px-2 py-1 rounded-full bg-destructive/10 text-destructive border border-destructive/20">BACKEND OFFLINE</span>
          )}
          <a
            href="/guide"
            target="_blank"
            rel="noopener noreferrer"
            className="text-[10px] sm:text-[11px] font-mono font-medium text-muted-foreground hover:text-foreground transition-colors underline-offset-2 hover:underline whitespace-nowrap"
          >
            How It Works
          </a>
        </div>
      </header>

      <main className="max-w-4xl mx-auto px-4 sm:px-8 py-5 sm:py-8 pb-8 sm:pb-14">
        {/* ─── Input View ─── */}
        {!pkg && !draftResult && !loading && (
          <>
            {restoredFromBrowser && (text.trim() || draftDesc.trim()) && (
              <div className="mb-6 rounded-xl neu-sm px-4 py-3 flex items-center justify-between gap-3 flex-wrap">
                <p className="text-[12px] text-muted-foreground leading-relaxed">
                  Your last session's text was restored from this browser.
                </p>
                <button type="button" onClick={reset} className="font-mono text-[10px] font-bold tracking-wider px-3 py-1.5 rounded-xl neu-btn">
                  START FRESH
                </button>
              </div>
            )}
            {/* Hero kept short so the two paths and the sample are visible on a
                laptop screen (1280x720) and a phone without scrolling. */}
            <div className="mb-5 sm:mb-6">
              {mode === "analyze" && (
                <>
                  <h1 className="font-serif-display text-3xl sm:text-4xl lg:text-5xl font-black text-foreground mb-2 sm:mb-3 leading-[1.05] tracking-tight">
                    Upload a policy. See the gaps.
                  </h1>
                  <p className="text-[14px] sm:text-base text-muted-foreground max-w-2xl leading-relaxed">
                    We check your policy against the federal regulation text and list what it's missing. You get a gap report and a proposed revision, with blanks left for your organization's own choices.
                  </p>
                </>
              )}
              {mode === "draft" && (
                <>
                  <h1 className="font-serif-display text-3xl sm:text-4xl lg:text-5xl font-black text-foreground mb-2 sm:mb-3 leading-[1.05] tracking-tight">
                    Describe a policy. Get a first draft.
                  </h1>
                  <p className="text-[14px] sm:text-base text-muted-foreground max-w-2xl leading-relaxed">
                    We draft it from federal regulation text and real policy templates. Who does what, deadlines, and start dates are left blank for you to fill in.
                  </p>
                </>
              )}
            </div>

            {/* Two paths, and the sample one click away from either. */}
            <div className="mb-5 flex flex-col sm:flex-row sm:items-stretch gap-2.5" role="group" aria-label="What do you want to do?">
              <div className="grid grid-cols-2 gap-2.5 flex-1">
                {([
                  { key: "analyze", label: "Analyze an existing policy", hint: "Upload or paste it; get a policy gap analysis report" },
                  { key: "draft",   label: "Draft a new policy",         hint: "Describe it; get a first draft" },
                ] as const).map((m) => (
                  <button
                    key={m.key}
                    type="button"
                    aria-pressed={mode === m.key}
                    onClick={() => setMode(m.key)}
                    className={`text-left rounded-xl px-3.5 py-2.5 border-2 transition-all touch-manipulation ${mode === m.key ? "bg-card border-primary shadow-sm" : "border-transparent neu-sm opacity-80 hover:opacity-100"}`}
                  >
                    <span className="block text-[13px] sm:text-[14px] font-semibold text-foreground leading-snug">{m.label}</span>
                    <span className="block text-[11px] sm:text-[12px] text-muted-foreground leading-snug mt-0.5">{m.hint}</span>
                  </button>
                ))}
              </div>
              <button
                type="button"
                onClick={() => {
                  // Pre-fills only: running it is still a real analysis.
                  setMode("analyze");
                  setIndustry("healthcare");
                  setText(SAMPLE_POLICY_TEXTS.healthcare);
                  setFileName("sample-hospital-policy.txt");
                  setError("");
                  toast.success("Hospital sample loaded", { description: "Click Analyze Policy to run it." });
                  // Bring the Analyze button into view so it is the next click.
                  setTimeout(() => analyzeButtonRef.current?.scrollIntoView({ behavior: "smooth", block: "center" }), 50);
                }}
                className="rounded-xl px-4 py-2.5 bg-primary text-primary-foreground neu-btn touch-manipulation inline-flex items-center justify-center gap-2 text-[13px] sm:text-[14px] font-semibold whitespace-nowrap"
              >
                <Wand2 className="w-4 h-4" strokeWidth={1.75} />
                Try the hospital sample
              </button>
            </div>

            {/* Unified card — Industry & Location → divider → Describe / Upload → Generate button */}
            <div className="rounded-2xl neu-raised p-5 sm:p-6">

              {/* — Industry & Location — */}
              <p className="nyt-eyebrow mb-3">Your industry and state</p>
              <div className="flex flex-col gap-2.5">
                <div className="relative">
                  <select
                    id="industry-select"
                    value={industry}
                    onChange={(e) => setIndustry(e.target.value)}
                    className="aqua-rain w-full text-[14px] font-medium px-4 py-3 pr-10 rounded-xl focus:outline-none cursor-pointer appearance-none"
                  >
                    {industries.map((ind) => (
                      <option key={ind.slug} value={ind.slug}>{ind.icon} {ind.name}</option>
                    ))}
                  </select>
                  <ChevronDown className="w-4 h-4 absolute right-3 top-1/2 -translate-y-1/2 text-muted-foreground pointer-events-none" />
                </div>
                {(INDUSTRY_BLURBS[industry] ?? industries.find((ind) => ind.slug === industry)?.description) && (
                  <p className="text-[11px] text-muted-foreground/80 leading-relaxed px-0.5">
                    {INDUSTRY_BLURBS[industry] ?? industries.find((ind) => ind.slug === industry)?.description}
                  </p>
                )}
                <div className="relative">
                  <select
                    value={stateCode}
                    onChange={(e) => setStateCode(e.target.value)}
                    className="aqua-rain w-full text-[14px] px-4 py-3 pr-10 rounded-xl text-foreground focus:outline-none cursor-pointer appearance-none"
                  >
                    <option value="">State (optional)</option>
                    {["AL","AK","AZ","AR","CA","CO","CT","DC","DE","FL","GA","HI","ID","IL","IN","IA","KS","KY","LA","ME","MD","MA","MI","MN","MS","MO","MT","NE","NV","NH","NJ","NM","NY","NC","ND","OH","OK","OR","PA","RI","SC","SD","TN","TX","UT","VT","VA","WA","WV","WI","WY"].map(s => (
                      <option key={s} value={s}>{s}</option>
                    ))}
                  </select>
                  <ChevronDown className="w-4 h-4 absolute right-3 top-1/2 -translate-y-1/2 text-muted-foreground pointer-events-none" />
                </div>
                {jurisdiction && (
                  <span className="text-[10px] font-mono text-primary/70 font-medium">{jurisdiction}</span>
                )}
                <p className="text-[11px] text-muted-foreground/80 leading-relaxed px-0.5">
                  {stateCode
                    ? `${stateCode} law isn't stored here, so ${stateCode} rules won't be verified — check them separately.`
                    : "No state picked, so only federal rules are checked."}
                </p>
              </div>

              <hr className="pl-divider" />

              {/* — Draft: Describe the policy — */}
              {mode === "draft" && (
                <>
                  <p className="nyt-eyebrow mb-3">What policy do you need?</p>
                  <div className="aqua-rain rounded-xl">
                    <textarea
                      value={draftDesc}
                      onChange={(e) => setDraftDesc(e.target.value.slice(0, 2000))}
                      placeholder={DRAFT_PLACEHOLDERS[industry] ?? DRAFT_PLACEHOLDERS.other}
                      className="w-full min-h-[160px] sm:min-h-[180px] bg-transparent rounded-xl px-4 py-3 text-[14px] leading-relaxed text-foreground placeholder:text-muted-foreground/50 focus:outline-none resize-y font-sans"
                    />
                  </div>
                  <div className="mt-3 flex items-center justify-between gap-3 flex-wrap">
                    <button
                      type="button"
                      onClick={() => {
                        const sample = SAMPLE_DRAFT_DESCRIPTIONS[industry] ?? SAMPLE_DRAFT_DESCRIPTIONS.other;
                        setDraftDesc(sample.slice(0, 2000));
                        setError("");
                        toast.success("Sample request loaded — click Generate Policy to run it");
                      }}
                      className="pl-sample-link"
                    >
                      <Wand2 className="w-3.5 h-3.5" strokeWidth={1.75} />
                      Load a sample
                    </button>
                    <span className="text-[12px] font-mono text-muted-foreground/70">{draftDesc.length} / 2000</span>
                  </div>
                </>
              )}

              {/* — Analyze: Upload a policy — */}
              {mode === "analyze" && (
                <>
                  <p className="nyt-eyebrow mb-3">Upload your policy</p>
                  <div
                    onDragOver={(e) => { e.preventDefault(); setDrag(true); }}
                    onDragLeave={() => setDrag(false)}
                    onDrop={(e) => { e.preventDefault(); setDrag(false); const f = e.dataTransfer.files?.[0]; if (f) handleFile(f); }}
                    onClick={() => !parsing && fileRef.current?.click()}
                    className={`aqua-rain rounded-xl px-6 py-8 text-center cursor-pointer transition-all touch-manipulation active:scale-[0.99] ${parsing ? "cursor-wait opacity-60" : ""}`}
                  >
                    <input ref={fileRef} type="file" accept=".txt,.md,.docx,.pdf,.rtf,.doc" onChange={(e) => { const f = e.target.files?.[0]; if (f) handleFile(f); }} className="hidden" />
                    <div className="w-10 h-10 mx-auto mb-2 rounded-full bg-[hsl(220_14%_94%)] flex items-center justify-center"><span className="text-lg">📄</span></div>
                    <p className="font-medium text-[14px] text-foreground">{parsing ? "Parsing file..." : fileName || "Tap to upload file"}</p>
                    <p className="text-[11px] text-muted-foreground mt-1">.txt .md .docx .pdf .rtf</p>
                  </div>
                  <p className="nyt-eyebrow mt-5 mb-2">Or paste the policy text</p>
                  <div className="aqua-rain rounded-xl">
                    <textarea
                      value={text}
                      onChange={(e) => { setText(e.target.value); if (fileName && !e.target.value) setFileName(""); }}
                      placeholder="Paste the full text of your policy here."
                      aria-label="Policy text"
                      className="w-full min-h-[160px] bg-transparent rounded-xl px-4 py-3 text-[14px] leading-relaxed text-foreground placeholder:text-muted-foreground/50 focus:outline-none resize-y font-sans"
                    />
                  </div>
                  <div className="mt-3 flex items-center justify-between gap-3 flex-wrap">
                    <span className="text-[12px] font-mono text-muted-foreground/70">{text.length.toLocaleString()} characters</span>
                    <button
                      type="button"
                      onClick={() => {
                        setText(SAMPLE_POLICY_TEXTS[industry] ?? SAMPLE_POLICY_TEXTS.healthcare);
                        setFileName(`sample-${industry === "healthcare" ? "hospital" : industry}-policy.txt`);
                        setError("");
                        toast.success("Sample policy loaded — click Analyze Policy to run it");
                      }}
                      className="pl-sample-link"
                    >
                      <Wand2 className="w-3.5 h-3.5" strokeWidth={1.75} />
                      Load a sample
                    </button>
                  </div>
                </>
              )}

              {/* Shown at the point of entry rather than only on the Legal
                  page: what to leave out is decided while typing or uploading,
                  and a disclosure found later arrives too late. */}
              <div className="mt-4 rounded-xl p-3" style={{ background: "hsl(0 72% 51% / 0.05)" }}>
                <p className="text-[11px] leading-relaxed text-foreground/80">
                  <span className="font-semibold text-foreground">Don't enter patient information (PHI) or anything confidential about a client.</span>{" "}
                  Your text is sent to outside AI providers, and no HIPAA business associate agreement covers them.
                  If we search the web, a short excerpt goes to a search engine. Use de-identified or sample policies.
                </p>
                <p className="text-[11px] leading-relaxed text-muted-foreground mt-1.5">
                  Your work stays saved in this browser until you click{" "}
                  <button type="button" onClick={reset} className="underline underline-offset-2 hover:text-foreground">Start fresh</button>.
                  Our server keeps it in memory for up to 30 minutes, never on disk.
                </p>
              </div>

              {/* — Generate button (inside the card) — */}
              {mode === "draft" && (
                <button
                  onClick={() => run(false)}
                  disabled={!draftDesc.trim()}
                  className="pl-button-dark mt-5 w-full font-medium text-[15px] px-6 py-4 rounded-xl flex items-center justify-center gap-2"
                >
                  Generate Policy <ArrowRight className="w-4 h-4" strokeWidth={2} />
                </button>
              )}
              {mode === "analyze" && (
                <button
                  ref={analyzeButtonRef}
                  onClick={() => run(false)}
                  disabled={!text.trim() || parsing}
                  className="pl-button-dark mt-5 w-full font-medium text-[15px] px-6 py-4 rounded-xl flex items-center justify-center gap-2"
                >
                  Analyze Policy <ArrowRight className="w-4 h-4" strokeWidth={2} />
                </button>
              )}

            </div>

            {error && (
              <div className="mt-4 rounded-2xl neu-raised p-4" style={{ background: "hsl(0 72% 51% / 0.06)" }}>
                <div className="flex flex-col sm:flex-row sm:items-start justify-between gap-2 sm:gap-3">
                  <div className="min-w-0">
                    <p className="text-sm font-bold text-destructive">Generation Failed</p>
                    <p className="text-xs text-destructive/70 mt-0.5">{error}</p>
                    {retryCount > 0 && <p className="text-[10px] font-mono text-muted-foreground mt-1">Attempt {retryCount + 1}</p>}
                  </div>
                  {retryCount < 5 && (
                    <button onClick={() => run(true)} className="shrink-0 self-start font-mono text-[10px] font-bold tracking-wider px-5 py-2.5 rounded-xl text-destructive neu-btn active:neu-pressed transition-all touch-manipulation">RETRY</button>
                  )}
                </div>
              </div>
            )}

            <p className="mt-4 text-center text-[11px] text-muted-foreground/70">
              Every result opens with a note on what it could and couldn't check.
            </p>

          </>
        )}

        {/* ─── Loading Pipeline ─── */}
        {loading && (
          <div className="flex flex-col items-center justify-center gap-4 py-16">
            <Loader2 className="w-8 h-8 animate-spin" style={{ color: "hsl(var(--primary))" }} />
            <p className="font-mono text-xs font-medium text-center px-4" style={{ color: "hsl(var(--primary))" }}>
              {mode === "draft"
                ? (draftStreamText ? "writing your policy..." : loadSec < 6 ? "reading your request..." : "looking up the regulations...")
                : (loadSec < 8 ? "reading your policy..." : loadSec < 18 ? "comparing it with the regulations..." : loadSec < 30 ? "listing the gaps..." : "almost done...")}
            </p>
            {/* Elapsed time counts UP, not down -- generation time genuinely varies
                (document complexity, model load), so a countdown could hit zero
                while still running, which reads as broken. An honest range instead. */}
            <p className="font-mono text-[11px] text-muted-foreground tabular-nums">
              {formatElapsed(loadSec)} elapsed — usually takes {mode === "draft" ? "30s–2 min" : "1–3 min"}
            </p>
            {mode === "draft" && draftStreamText && (
              <div className="w-full max-w-xl mx-4 max-h-48 overflow-y-auto rounded-xl neu-inset px-4 py-3">
                <p className="font-mono text-[10px] leading-relaxed text-muted-foreground whitespace-pre-wrap break-words">
                  {draftStreamText.slice(-1200)}
                </p>
              </div>
            )}
            <p className="text-[10px] text-muted-foreground/70 text-center px-4 max-w-sm">
              This keeps running on the server even if you leave this screen — you can safely come back later.
            </p>
            <button
              onClick={cancelGeneration}
              className="font-mono text-[10px] font-bold tracking-wider px-5 py-2.5 rounded-xl neu-btn active:neu-pressed transition-all touch-manipulation"
            >
              CANCEL
            </button>
          </div>
        )}

        {/* ─── Draft Result View ─── */}
        {draftResult && !loading && (
          <div className="space-y-4">
            <div className="flex items-center justify-between gap-3 flex-wrap">
              <div>
                <p className="text-[10px] font-mono uppercase tracking-widest text-primary font-medium mb-1">Drafted Policy</p>
                <h2 className="text-xl sm:text-2xl font-bold text-foreground leading-tight">{draftResult.policy_title}</h2>
                <div className="flex flex-wrap gap-3 mt-1 text-[11px] text-muted-foreground font-mono">
                  {draftResult.effective_date && <span>Effective: {draftResult.effective_date}</span>}
                  {draftResult.version && <span>v{draftResult.version}</span>}
                </div>
              </div>
              <div className="flex gap-2 flex-wrap">
                <button
                  disabled={draftExporting}
                  onClick={async () => {
                    setDraftExporting(true);
                    try {
                      await exportDraftPolicy(draftResult);
                      toast.success("Policy downloaded");
                    } catch (e: any) {
                      toast.error("Download failed", { description: e.message });
                    } finally {
                      setDraftExporting(false);
                    }
                  }}
                  title="Downloads this complete policy document, formatted and ready to review, as a Word file."
                  className="font-mono text-[10px] font-bold tracking-wider px-4 py-2 rounded-xl bg-primary text-primary-foreground neu-btn active:neu-pressed touch-manipulation disabled:opacity-60 inline-flex items-center gap-1.5"
                >
                  <FileDown className="w-3.5 h-3.5" />
                  {draftExporting ? "DOWNLOADING..." : "DOWNLOAD POLICY (.DOCX)"}
                </button>
                <button
                  onClick={() => { navigator.clipboard.writeText(draftResult.full_text); toast.success("Policy copied to clipboard"); }}
                  title="Copies the full policy text to your clipboard instead of downloading a file."
                  className="font-mono text-[10px] font-bold tracking-wider px-4 py-2 rounded-xl neu-btn touch-manipulation"

                >
                  COPY TEXT
                </button>
                <button onClick={reset} className="font-mono text-[10px] font-bold tracking-wider px-4 py-2 rounded-xl neu-sm text-muted-foreground hover:text-foreground touch-manipulation">
                  START FRESH
                </button>
              </div>
            </div>
            <UngroundedDraftBanner grounded={draftResult.grounded} />
            <LimitationsBanner lines={draftLimitations(draftResult)} />

            {(draftResult.decisions_required?.length ?? 0) > 0 && (
              <div className="rounded-xl neu-sm p-4">
                <p className="nyt-eyebrow mb-2">Decisions you need to make</p>
                <ul className="list-disc pl-5 space-y-1">
                  {draftResult.decisions_required!.map((d, i) => (
                    <li key={i} className="text-[12px] text-foreground leading-relaxed">{d}</li>
                  ))}
                </ul>
              </div>
            )}

            {(draftResult.missing_obligations?.length ?? 0) > 0 && (
              <div className="rounded-xl p-4 border-l-4 border-l-destructive" style={{ background: "hsl(0 72% 51% / 0.05)" }}>
                <p className="text-[11px] font-mono uppercase tracking-wider font-bold text-destructive mb-2">Obligations this draft does not appear to cover</p>
                <ul className="list-disc pl-5 space-y-1">
                  {draftResult.missing_obligations!.map((o, i) => (
                    <li key={i} className="text-[12px] text-foreground leading-relaxed">{o}</li>
                  ))}
                </ul>
              </div>
            )}

            <StateSourcesList coverage={draftResult.state_coverage} />

            {draftResult.scope && (
              <div className="rounded-xl neu-sm p-4">
                <p className="nyt-eyebrow mb-1">Scope</p>
                <p className="text-[13px] text-foreground leading-relaxed">{draftResult.scope}</p>
              </div>
            )}

            {draftResult.regulations_applied.length > 0 && (
              <div className="rounded-xl neu-sm p-4">
                <p className="nyt-eyebrow mb-3">Regulations Applied</p>
                <div className="flex flex-wrap gap-2">
                  {draftResult.regulations_applied.map((reg, i) => {
                    const url = lookupRegulationUrl(reg);
                    return url ? (
                      <a
                        key={i}
                        href={url}
                        target="_blank"
                        rel="noopener noreferrer"
                        title={`Open source: ${url}`}
                        className="text-[10px] font-mono px-2 py-1 rounded-lg inline-flex items-center gap-1 hover:underline"
                        style={{ background: "hsl(var(--primary)/0.08)", color: "hsl(var(--primary))" }}
                      >
                        {reg}
                        <span aria-hidden="true">↗</span>
                      </a>
                    ) : (
                      <span key={i} className="text-[10px] font-mono px-2 py-1 rounded-lg" style={{ background: "hsl(var(--primary)/0.08)", color: "hsl(var(--primary))" }}>{reg}</span>
                    );
                  })}
                </div>
              </div>
            )}

            <div className="rounded-xl neu-raised p-6">
              <p className="nyt-eyebrow mb-4">Full Policy Document</p>
              <div className="space-y-5">
                {draftResult.sections.length > 0 ? draftResult.sections.map((sec, i) => (
                  <div key={i}>
                    <h3 className="text-[13px] font-bold text-foreground mb-2 uppercase tracking-wide">{sec.title}</h3>
                    <p className="text-[13px] text-foreground leading-relaxed whitespace-pre-wrap">{linkifyRegulations(sec.content, undefined, draftResult.source_snippets)}</p>
                  </div>
                )) : (
                  <p className="text-[13px] text-foreground leading-relaxed whitespace-pre-wrap">{linkifyRegulations(draftResult.full_text, undefined, draftResult.source_snippets)}</p>
                )}
              </div>
            </div>

            {draftResult.drafting_notes && (
              <div className="rounded-xl neu-sm p-4">
                <p className="nyt-eyebrow mb-1">Drafting Notes</p>
                <p className="text-[12px] text-muted-foreground leading-relaxed italic">{draftResult.drafting_notes}</p>
              </div>
            )}

            {/* Ask AI button for draft */}
            <button
              onClick={() => openChat("draft")}
              style={{ background: "hsl(var(--primary))", color: "hsl(var(--primary-foreground))" }}
              className="fixed bottom-4 right-4 sm:bottom-6 sm:right-6 z-30 flex items-center gap-2 px-4 py-3 rounded-2xl shadow-lg font-mono text-[10px] font-bold tracking-wider touch-manipulation"
            >
              <MessageSquare className="w-4 h-4" />
              <span>ASK AI</span>
            </button>
          </div>
        )}

        {/* ─── Results with Tabs ─── */}
        {pkg && !loading && (
          <div className="space-y-4">
            <div className="flex items-center justify-between gap-3 flex-wrap">
              <div>
                <p className="text-[10px] font-mono uppercase tracking-widest text-primary font-medium mb-1">Gap Analysis</p>
                <h2 className="text-xl sm:text-2xl font-bold text-foreground leading-tight">{fileName || pkg.gap_analysis?.policy_type || "Analyzed Policy"}</h2>
              </div>
              <div className="flex gap-2 flex-wrap">
                <button
                  onClick={handleFixAllGaps}
                  disabled={fixingGaps || !pkg.gap_analysis}
                  title="Writes a proposed revision of the policy aimed at the findings. It is not re-checked until you run Re-check on it."
                  className="font-mono text-[10px] font-bold tracking-wider px-4 py-2 rounded-xl bg-primary text-primary-foreground neu-btn active:neu-pressed touch-manipulation disabled:opacity-60 inline-flex items-center gap-1.5"
                >
                  {fixingGaps ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Wand2 className="w-3.5 h-3.5" />}
                  {fixingGaps ? "DRAFTING REVISIONS..." : "DRAFT REVISIONS"}
                </button>
                <button
                  onClick={handleDownloadGapAnalysis}
                  disabled={exporting}
                  title="Downloads the full gap analysis report as a Word document — opens with a one-page compliance certificate summary (regulations reviewed), followed by every finding, citation, and suggested policy language."
                  className="font-mono text-[10px] font-bold tracking-wider px-4 py-2 rounded-xl neu-sm text-muted-foreground hover:text-foreground touch-manipulation disabled:opacity-60 inline-flex items-center gap-1.5"
                >
                  {exporting ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <FileDown className="w-3.5 h-3.5" />}
                  {exporting ? "DOWNLOADING..." : "DOWNLOAD REPORT (.DOCX)"}
                </button>
                <button onClick={reset} className="font-mono text-[10px] font-bold tracking-wider px-4 py-2 rounded-xl neu-sm text-muted-foreground hover:text-foreground touch-manipulation">
                  START FRESH
                </button>
              </div>
            </div>

            <LimitationsBanner lines={analysisLimitations(viewedPkg!, showingRevision ? false : pkgStreaming)} />
            <StateSourcesList coverage={pkg.state_coverage} />

            {/* Tab bar */}
            <div className="flex gap-1 overflow-x-auto pb-1 -mx-4 px-4 sm:mx-0 sm:px-0">
              {availableTabs.map((tab) => {
                const Icon = tab.icon;
                const isActive = activeTab === tab.key;
                return (
                  <button key={tab.key} onClick={() => setActiveTab(tab.key)} className={`flex items-center gap-1.5 px-3 py-2 rounded-xl font-mono text-[10px] font-bold tracking-wider whitespace-nowrap transition-all shrink-0 ${isActive ? "bg-primary text-primary-foreground neu-btn" : "neu-btn text-foreground hover:bg-[hsl(220_14%_94%)]"}`}>
                    <Icon className="w-3.5 h-3.5" />
                    {tab.label}
                  </button>
                );
              })}
            </div>

            {/* Tab content */}
            {(activeTab === "overview" || activeTab === "gap_analysis") && revisionAnalysis && (
              <AnalysisVersionSwitch
                view={showingRevision ? "revision" : "original"}
                onChange={(v) => { setAnalysisView(v); setSeverityFilter(null); }}
                originalName={fileName || "the uploaded policy"}
                revisionVersion={revisionVersion}
                checkedAt={revisionAnalysis.checkedAt}
              />
            )}
            {activeTab === "overview" && <OverviewTab pkg={viewedPkg!} verifying={showingRevision ? false : pkgStreaming} />}
            {activeTab === "gap_analysis" && viewedPkg!.gap_analysis && (
              <GapAnalysisTab
                key={showingRevision ? `revision-${revisionVersion}` : "original"}
                result={viewedPkg!.gap_analysis}
                verifying={showingRevision ? false : pkgStreaming}
                urlMap={viewedPkg!.kb_source_urls}
                snippets={viewedPkg!.source_snippets}
                severityFilter={severityFilter}
                onChangeFilter={setSeverityFilter}
                revisionSections={showingRevision ? revisionAnalysis!.sections : undefined}
              />
            )}
            {activeTab === "corrected" && pkg.rewritten_policy && (
              <div className="space-y-4">
                <div className="flex items-center justify-between gap-3 flex-wrap">
                  <div>
                    {pkg.rewritten_policy.effective_date && (
                      <p className="text-[11px] text-muted-foreground font-mono">Effective: {pkg.rewritten_policy.effective_date}</p>
                    )}
                  </div>
                  <div className="flex items-center gap-2">
                    <button
                      onClick={() => setShowRedline((s) => !s)}
                      title={showRedline ? "Show the proposed revision as clean text" : "Show exactly what changed vs. the original, tracked-changes style"}
                      className={`font-mono text-[10px] font-bold tracking-wider px-3 py-2 rounded-xl neu-btn touch-manipulation inline-flex items-center gap-1.5 ${showRedline ? "neu-pressed text-primary" : "text-foreground"}`}
                    >
                      <GitCompare className="w-3.5 h-3.5" />
                      {showRedline ? "CLEAN VIEW" : "REDLINE VIEW"}
                    </button>
                    <button
                      disabled={correctedExporting}
                      onClick={async () => {
                        setCorrectedExporting(true);
                        try {
                          await exportUpdatedPolicy(pkg);
                          toast.success("Proposed revision downloaded");
                        } catch (e: any) {
                          toast.error("Download failed", { description: e.message });
                        } finally {
                          setCorrectedExporting(false);
                        }
                      }}
                      title="Downloads this proposed revision as a Word file, for review."
                      className="font-mono text-[10px] font-bold tracking-wider px-4 py-2 rounded-xl bg-primary text-primary-foreground neu-btn active:neu-pressed touch-manipulation disabled:opacity-60 inline-flex items-center gap-1.5"
                    >
                      {correctedExporting ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <FileDown className="w-3.5 h-3.5" />}
                      {correctedExporting ? "DOWNLOADING..." : "DOWNLOAD PROPOSED REVISION (.DOCX)"}
                    </button>
                  </div>
                </div>
                <div className="rounded-xl neu-sm p-4 flex items-center justify-between gap-3 flex-wrap">
                  <div className="max-w-md space-y-1">
                    {revisionAnalysis === null ? (
                      <p className="text-[12px] text-foreground/80 leading-relaxed">
                        Not re-checked. This revision was written to address the findings, but nothing has confirmed it does. Re-check runs a fresh analysis on it.
                      </p>
                    ) : (
                      <>
                        {/* Counted from the same rows the cards show. */}
                        <p className="text-[13px] font-semibold text-foreground">
                          Original: {mustFixCount(pkg.gap_analysis?.gap_table)} must-fix → Revision: {mustFixCount(revisionAnalysis.pkg.gap_analysis?.gap_table)} must-fix
                        </p>
                        <p className="text-[12px] text-foreground/75 leading-relaxed">
                          {(() => {
                            const n = revisionAnalysis.pkg.gap_analysis?.gap_table?.length ?? 0;
                            return n === 0
                              ? "A fresh analysis of this revision found no remaining findings. It still needs review by counsel."
                              : `A fresh analysis of this revision found ${n} remaining finding${n !== 1 ? "s" : ""}. The original analysis is kept unchanged.`;
                          })()}
                        </p>
                      </>
                    )}
                  </div>
                  <button
                    onClick={handleRecheck}
                    disabled={rechecking}
                    className="font-mono text-[10px] font-bold tracking-wider px-4 py-2 rounded-xl neu-btn touch-manipulation disabled:opacity-60 inline-flex items-center gap-1.5"
                  >
                    {rechecking ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <RefreshCw className="w-3.5 h-3.5" />}
                    {rechecking ? "RE-CHECKING..." : revisionAnalysis ? "RE-CHECK AGAIN" : "RE-CHECK REVISION"}
                  </button>
                  {revisionAnalysis && (
                    <button
                      onClick={() => { setAnalysisView("revision"); setSeverityFilter(null); setActiveTab("gap_analysis"); }}
                      className="font-mono text-[10px] font-bold tracking-wider px-4 py-2 rounded-xl bg-primary text-primary-foreground neu-btn touch-manipulation inline-flex items-center gap-1.5"
                    >
                      VIEW REMAINING FINDINGS
                    </button>
                  )}
                </div>
                {pkg.rewritten_policy.change_summary && (
                  <div className="rounded-xl neu-sm p-4">
                    <p className="nyt-eyebrow mb-1">What Changed</p>
                    <p className="text-[13px] text-foreground leading-relaxed">{pkg.rewritten_policy.change_summary}</p>
                  </div>
                )}
                {showRedline ? (
                  <div className="rounded-xl neu-raised p-6">
                    <p className="nyt-eyebrow mb-1">Redline — Original vs. Proposed</p>
                    <p className="text-[11px] text-muted-foreground mb-4">
                      <del className="rounded px-0.5" style={{ background: "hsl(0 72% 51% / 0.12)", color: "hsl(0 72% 38%)" }}>Removed</del>
                      {" "}·{" "}
                      <ins className="no-underline rounded px-0.5" style={{ background: "hsl(160 60% 42% / 0.18)", color: "hsl(160 60% 24%)" }}>Added</ins>
                    </p>
                    <RedlineView original={text} corrected={pkg.rewritten_policy.full_text} />
                  </div>
                ) : (
                <div className="rounded-xl neu-raised p-6">
                  <p className="nyt-eyebrow mb-4">Proposed Revision</p>
                  <div className="space-y-5">
                    {pkg.rewritten_policy.sections.length > 0 ? pkg.rewritten_policy.sections.map((sec, i) => (
                      <div key={i}>
                        <h3 className="text-[13px] font-bold text-foreground mb-2 uppercase tracking-wide">{sec.section_title}</h3>
                        <p className="text-[13px] text-foreground leading-relaxed whitespace-pre-wrap">{linkifyRegulations(sec.rewritten_text, undefined, pkg.source_snippets)}</p>
                        {(sec.changes_summary || sec.regulation_refs?.length > 0) && (
                          <div className="mt-2 pl-3 border-l-2" style={{ borderColor: "hsl(0 72% 51% / 0.4)" }}>
                            {sec.changes_summary && (
                              <p className="text-[11px] text-foreground/70 leading-relaxed">
                                <span className="font-mono font-bold uppercase tracking-wide mr-1" style={{ color: "hsl(0 72% 48%)" }}>Changed:</span>
                                {sec.changes_summary}
                              </p>
                            )}
                            {sec.regulation_refs?.length > 0 && (
                              <div className="flex flex-wrap gap-1.5 mt-1.5">
                                {sec.regulation_refs.map((reg, ri) => {
                                  const url = lookupRegulationUrl(reg);
                                  return url ? (
                                    <a
                                      key={ri}
                                      href={url}
                                      target="_blank"
                                      rel="noopener noreferrer"
                                      title={`Open source: ${url}`}
                                      className="text-[9px] font-mono px-1.5 py-0.5 rounded-lg inline-flex items-center gap-1 hover:underline"
                                      style={{ background: "hsl(var(--primary)/0.08)", color: "hsl(var(--primary))" }}
                                    >
                                      {reg}
                                      <span aria-hidden="true">↗</span>
                                    </a>
                                  ) : (
                                    <span key={ri} className="text-[9px] font-mono px-1.5 py-0.5 rounded-lg" style={{ background: "hsl(var(--primary)/0.08)", color: "hsl(var(--primary))" }}>{reg}</span>
                                  );
                                })}
                              </div>
                            )}
                          </div>
                        )}
                      </div>
                    )) : (
                      <p className="text-[13px] text-foreground leading-relaxed whitespace-pre-wrap">{linkifyRegulations(pkg.rewritten_policy.full_text, undefined, pkg.source_snippets)}</p>
                    )}
                  </div>
                </div>
                )}
              </div>
            )}
            {/* Ask AI button */}
            <button
              onClick={() => openChat("analysis")}
              style={{ background: "hsl(var(--primary))", color: "hsl(var(--primary-foreground))" }}
              className="fixed bottom-4 right-4 sm:bottom-6 sm:right-6 z-30 flex items-center gap-2 px-4 py-3 rounded-2xl shadow-lg font-mono text-[10px] font-bold tracking-wider touch-manipulation"
            >
              <MessageSquare className="w-4 h-4" />
              <span>ASK AI</span>
            </button>

          </div>
        )}

        {/* Chat Panel */}
        {(pkg || draftResult) && (
          <ChatPanel
            open={chatOpen}
            onClose={() => setChatOpen(false)}
            history={chatHistory}
            input={chatInput}
            setInput={setChatInput}
            onSend={handleSendChat}
            chatLoading={chatLoading}
            chatEndRef={chatEndRef}
          />
        )}
      </main>

      <footer className="border-t border-foreground/8 px-4 py-8 sm:py-10 text-center space-y-2.5">
        <p className="text-[10px] text-muted-foreground/60 font-mono leading-relaxed max-w-sm mx-auto">
          Disclaimer: This tool uses AI to generate content and may produce inaccuracies. All output must be reviewed before official use. Final judgment and responsibility remain with the end user.
        </p>
        <p className="text-[10px] text-muted-foreground/35 font-mono tracking-wider">
          <a href="/guide" target="_blank" rel="noopener noreferrer" className="hover:text-muted-foreground transition-colors underline-offset-2 hover:underline">About &amp; How To Use</a>
          <span className="mx-2">·</span>
          <a href="/legal" target="_blank" rel="noopener noreferrer" className="hover:text-muted-foreground transition-colors underline-offset-2 hover:underline">Legal &amp; Disclaimers</a>
          <span className="mx-2">·</span>
          Built by Andrew Weingarten
        </p>
      </footer>
    </div>
  );
}

// ──────────────────────────────────────────────
// Tab Components
// ──────────────────────────────────────────────

// Computed word-level diff between the originally uploaded policy and the
// corrected one -- pure post-processing on two strings that already exist,
// not a second model generation, so it can't reintroduce the truncation
// bug that duplicate model-written output caused elsewhere in this app.
function RedlineView({ original, corrected }: { original: string; corrected: string }) {
  const parts = useMemo(() => diffWordsWithSpace(original, corrected), [original, corrected]);
  return (
    <div className="text-[13px] text-foreground leading-relaxed whitespace-pre-wrap">
      {parts.map((part, i) => {
        if (part.added) {
          return (
            <ins key={i} className="no-underline rounded px-0.5" style={{ background: "hsl(160 60% 42% / 0.18)", color: "hsl(160 60% 24%)" }}>
              {part.value}
            </ins>
          );
        }
        if (part.removed) {
          return (
            <del key={i} className="rounded px-0.5" style={{ background: "hsl(0 72% 51% / 0.12)", color: "hsl(0 72% 38%)" }}>
              {part.value}
            </del>
          );
        }
        return <span key={i}>{part.value}</span>;
      })}
    </div>
  );
}

// "Original analysis | Revision analysis", and a plain statement of which
// document the report below describes.
function AnalysisVersionSwitch({ view, onChange, originalName, revisionVersion, checkedAt }: {
  view: "original" | "revision"; onChange: (v: "original" | "revision") => void;
  originalName: string; revisionVersion: number; checkedAt: string;
}) {
  const time = (() => { try { return new Date(checkedAt).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" }); } catch { return ""; } })();
  return (
    <div className="rounded-xl neu-sm p-3 sm:p-4 space-y-2">
      <div className="inline-flex rounded-xl p-1 bg-secondary" role="tablist" aria-label="Which analysis to show">
        {(["original", "revision"] as const).map((v) => (
          <button
            key={v}
            role="tab"
            aria-selected={view === v}
            onClick={() => onChange(v)}
            className={`px-3 py-1.5 rounded-lg font-mono text-[10px] font-bold tracking-wider transition-all ${view === v ? "bg-background text-foreground shadow-sm" : "text-muted-foreground hover:text-foreground"}`}
          >
            {v === "original" ? "Original analysis" : "Revision analysis"}
          </button>
        ))}
      </div>
      <p className="text-[12px] text-foreground/75 leading-relaxed">
        {view === "original"
          ? `Showing the original analysis of ${originalName}.`
          : `Showing the revision analysis: a fresh analysis of proposed revision ${revisionVersion}${time ? `, re-checked at ${time}` : ""}. The original analysis is unchanged.`}
      </p>
    </div>
  );
}

function OverviewTab({ pkg, verifying = false }: { pkg: ComplianceActionPackage; verifying?: boolean }) {
  const ga = pkg.gap_analysis;
  const rows = ga.gap_table ?? [];
  const totalIssues = rows.length;
  // Built from the finding objects, never from the model's free-text summary,
  // so it can only say what the cards say.
  const stillChecking = verifying && rows.some((r) => !r.evidence && evidenceStatusOf(r) !== "recommendation");
  const counts = countByEvidence(rows);

  return (
    <div className="space-y-4">
      {/* Status banner */}
      <div className="rounded-2xl bg-card neu-raised p-4 sm:p-5">
        <div className="flex items-center gap-2 mb-3">
          {totalIssues > 0 ? <AlertTriangle className="w-4 h-4 text-destructive" /> : <CheckCircle2 className="w-4 h-4 text-green-600" />}
          <span className="text-[10px] font-mono uppercase tracking-wider font-medium text-muted-foreground">The Policy Lab — Compliance Report</span>
        </div>
        <p className="text-[13px] sm:text-sm text-foreground/85 leading-relaxed">
          {stillChecking
            ? `This review produced ${totalIssues} finding${totalIssues !== 1 ? "s" : ""}. Each finding's citation is still being checked against the regulation text.`
            : findingsSummary(rows)}
        </p>
        <div className="flex flex-wrap gap-2 mt-3">
          <span className="text-[10px] font-mono px-2 py-0.5 rounded-full bg-secondary text-muted-foreground">{ga.policy_type}</span>
          <span className="text-[10px] font-mono px-2 py-0.5 rounded-full bg-secondary text-muted-foreground">{totalIssues} finding{totalIssues !== 1 ? "s" : ""}</span>
          {!stillChecking && (Object.keys(EVIDENCE_LABELS) as (keyof typeof EVIDENCE_LABELS)[]).filter((k) => counts[k] > 0).map((k) => (
            <span key={k} className="text-[10px] font-mono px-2 py-0.5 rounded-full bg-secondary text-muted-foreground">{counts[k]} {EVIDENCE_LABELS[k]}</span>
          ))}
        </div>
      </div>
    </div>
  );
}


function GapAnalysisTab({ result, urlMap, snippets, severityFilter, onChangeFilter, verifying = false, revisionSections }: { result: AnalysisResult; urlMap?: Record<string, string>; snippets?: SourceSnippet[] | null; severityFilter?: "must_fix" | "should_fix" | null; onChangeFilter?: (s: "must_fix" | "should_fix" | null) => void; verifying?: boolean; revisionSections?: RewrittenPolicySection[] }) {
  const isMustFix = (r: typeof result.gap_table[number]) => priorityOf(r) === "must_fix";
  const mustFixItems = result.gap_table.filter(isMustFix);

  // Inline filter chips — one row of buttons, each shows count, tap to narrow the list. "All" resets.
  // Two tiers only, no middle ground: it's either a real compliance/legal
  // exposure or a best-practice improvement.
  const chips = [
    { key: null,               label: "All",         count: result.gap_table.length, color: "hsl(220 14% 22%)" },
    { key: "must_fix" as const,   label: "Must Fix",    count: mustFixItems.length,                              color: "hsl(0 72% 48%)" },
    { key: "should_fix" as const, label: "Should Fix",  count: result.gap_table.length - mustFixItems.length,    color: "hsl(38 85% 44%)" },
  ];

  const filterPredicate = severityFilter
    ? (r: typeof result.gap_table[number]) => severityFilter === "must_fix" ? isMustFix(r) : !isMustFix(r)
    : null;
  const filteredRows = filterPredicate ? result.gap_table.filter(filterPredicate) : null;

  return (
    <div className="space-y-4">
      {/* Filter chips — surgical drilldown without leaving the page */}
      <div className="flex flex-wrap gap-2">
        {chips.filter((chip) => chip.count > 0).map((chip) => {
          const active = (severityFilter ?? null) === chip.key;
          return (
            <button
              key={chip.label}
              type="button"
              onClick={() => onChangeFilter?.(chip.key)}
              className={`inline-flex items-center gap-1.5 px-3 py-1.5 rounded-full font-mono text-[10px] font-medium tracking-wider transition-all touch-manipulation ${active ? "neu-pressed" : "neu-sm hover:text-foreground"}`}
              style={active ? { color: chip.color, background: `${chip.color.replace(")", " / 0.12)")}`, boxShadow: `inset 0 0 0 1.5px ${chip.color}40` } : { color: "hsl(var(--muted-foreground))" }}
            >
              {chip.label}
              <span className={`text-[9px] font-bold tabular-nums ${active ? "" : "opacity-60"}`}>{chip.count}</span>
            </button>
          );
        })}
      </div>

      {filteredRows ? (
        filteredRows.length === 0 ? (
          <div className="rounded-xl p-6 text-center neu-sm">
            <p className="text-sm text-muted-foreground">No items in this category.</p>
          </div>
        ) : (
          filteredRows.map((row, i) => <GapRowItem key={i} row={row} urlMap={urlMap} snippets={snippets} verifying={verifying} sectionLabel={revisionSectionFor(row, revisionSections)} />)
        )
      ) : (
        <>
          {mustFixItems.length > 0 && (
            <div className="rounded-2xl p-4 neu-raised border-l-4 border-l-destructive" style={{ background: "hsl(0 72% 51% / 0.04)" }}>
              <p className="text-[11px] font-mono uppercase tracking-wider font-bold text-destructive mb-2">
                Must Fix — {mustFixItems.length} Finding{mustFixItems.length !== 1 ? "s" : ""}
              </p>
              {/* Listed from the findings themselves, each with its evidence status,
                  so urgency never reads as legal standing. */}
              {mustFixItems.map((f, i) => (
                <p key={i} className="text-[11px] sm:text-xs text-foreground/80 leading-relaxed pl-3 border-l-2 border-l-destructive/30 mb-1">
                  {stripCiteTags(f.clause)} <span className="text-muted-foreground">· {verifying && !f.evidence && evidenceStatusOf(f) !== "recommendation" ? "checking source" : EVIDENCE_LABELS[evidenceStatusOf(f)]}</span>
                </p>
              ))}
            </div>
          )}

          {result.gap_table.length > 0 && (
            <p className="font-mono text-[10px] uppercase tracking-widest text-muted-foreground font-medium px-1">
              Fix these {result.gap_table.length} issue{result.gap_table.length !== 1 ? "s" : ""}
            </p>
          )}

          {result.gap_table.map((row, i) => <GapRowItem key={i} row={row} urlMap={urlMap} snippets={snippets} verifying={verifying} sectionLabel={revisionSectionFor(row, revisionSections)} />)}
        </>
      )}
    </div>
  );
}


function RewrittenPolicyTab({ policy }: { policy: RewrittenPolicy }) {
  const [showFullText, setShowFullText] = useState(false);
  const [expandedSections, setExpandedSections] = useState<Set<number>>(new Set());

  const toggleSection = (idx: number) => {
    setExpandedSections((prev) => {
      const next = new Set(prev);
      if (next.has(idx)) next.delete(idx);
      else next.add(idx);
      return next;
    });
  };

  return (
    <div className="space-y-4">
      <div className="rounded-2xl bg-card neu-raised p-4 sm:p-5">
        <h3 className="text-sm font-bold text-foreground mb-1">{policy.policy_title}</h3>
        <p className="text-xs text-muted-foreground">Effective: {policy.effective_date}  |  {policy.version_note}</p>
        <p className="text-xs text-muted-foreground mt-2">{policy.change_summary}</p>
      </div>

      <div className="flex gap-2">
        <button onClick={() => setShowFullText(!showFullText)} className={`font-mono text-[10px] font-medium px-3 py-2 rounded-xl transition-all ${showFullText ? "bg-primary text-primary-foreground neu-btn" : "bg-card text-muted-foreground neu-sm"}`}>
          {showFullText ? "Section View" : "Full Text View"}
        </button>
      </div>

      {showFullText ? (
        <div className="rounded-2xl bg-card neu-raised p-4 sm:p-5">
          <pre className="text-xs text-foreground/85 whitespace-pre-wrap leading-relaxed font-sans">{policy.full_text}</pre>
        </div>
      ) : (
        <div className="space-y-3">
          {policy.sections.map((section, idx) => (
            <div key={idx} className="rounded-xl neu-sm overflow-hidden">
              <button onClick={() => toggleSection(idx)} className="w-full flex items-center justify-between px-4 py-3 text-left">
                <span className="text-[12px] font-semibold text-foreground">{section.section_title}</span>
                <ChevronDown className="w-4 h-4 text-muted-foreground transition-transform duration-200" style={{ transform: expandedSections.has(idx) ? "rotate(180deg)" : "none" }} />
              </button>
              {expandedSections.has(idx) && (
                <div className="px-4 pb-4 space-y-3">
                  {section.original_text && section.original_text !== "NEW SECTION" && (
                    <div className="rounded-lg p-3" style={{ background: "hsl(0 72% 51% / 0.06)" }}>
                      <p className="text-[10px] font-mono uppercase tracking-wider mb-1" style={{ color: "hsl(0 72% 48%)" }}>Original</p>
                      <p className="text-xs text-foreground/80 leading-relaxed">{section.original_text}</p>
                    </div>
                  )}
                  <div className="rounded-lg p-3" style={{ background: "hsl(160 60% 42% / 0.06)" }}>
                    <p className="text-[10px] font-mono uppercase tracking-wider mb-1" style={{ color: "hsl(160 60% 36%)" }}>Rewritten</p>
                    <p className="text-xs text-foreground/80 leading-relaxed">{section.rewritten_text}</p>
                  </div>
                  {section.changes_summary && <p className="text-[10px] text-muted-foreground italic">{section.changes_summary}</p>}
                  {section.regulation_refs?.length > 0 && <p className="text-[9px] font-mono text-muted-foreground">Regs: {section.regulation_refs.join(", ")}</p>}
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}


// ──────────────────────────────────────────────
// Chat Panel Component
// ──────────────────────────────────────────────

interface ChatPanelProps {
  open: boolean;
  onClose: () => void;
  history: ChatMessage[];
  input: string;
  setInput: (v: string) => void;
  onSend: () => void;
  chatLoading: boolean;
  chatEndRef: React.RefObject<HTMLDivElement>;
}

function ChatPanel({ open, onClose, history, input, setInput, onSend, chatLoading, chatEndRef }: ChatPanelProps) {
  const inputRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    if (open && inputRef.current) inputRef.current.focus();
  }, [open]);

  if (!open) return null;

  return (
    <>
      {/* Backdrop */}
      <div className="fixed inset-0 z-40 bg-black/30 backdrop-blur-sm" onClick={onClose} />

      {/* Panel */}
      <div className="fixed bottom-0 left-0 right-0 sm:bottom-4 sm:right-4 sm:left-auto sm:w-[420px] z-50 flex flex-col rounded-t-2xl sm:rounded-2xl shadow-2xl overflow-hidden" style={{ background: "hsl(var(--background))", maxHeight: "75dvh" }}>
        {/* Header */}
        <div className="flex items-center justify-between px-4 py-3 border-b" style={{ borderColor: "hsl(var(--border))" }}>
          <div className="flex items-center gap-2">
            <div className="w-6 h-6 rounded-full flex items-center justify-center" style={{ background: "hsl(var(--primary)/0.12)" }}>
              <MessageSquare className="w-3.5 h-3.5" style={{ color: "hsl(var(--primary))" }} />
            </div>
            <p className="text-[11px] font-mono font-bold tracking-wider" style={{ color: "hsl(var(--primary))" }}>Ask the AI</p>
          </div>
          <button onClick={onClose} className="p-1 rounded-lg hover:bg-secondary transition-colors">
            <X className="w-4 h-4 text-muted-foreground" />
          </button>
        </div>

        {/* Messages */}
        <div className="flex-1 overflow-y-auto px-4 py-3 space-y-3 min-h-0">
          {history.length === 0 && (
            <p className="text-[12px] text-muted-foreground leading-relaxed">Ask a question about your results.</p>
          )}
          {history.map((msg, i) => (
            <div key={i} className={`flex flex-col ${msg.role === "user" ? "items-end" : "items-start"}`}>
              <div
                className={`max-w-[85%] px-3 py-2.5 rounded-2xl text-[12px] leading-relaxed ${msg.role === "user" ? "whitespace-pre-wrap" : ""}`}
                style={msg.role === "user"
                  ? { background: "hsl(var(--primary))", color: "hsl(var(--primary-foreground))" }
                  : { background: "hsl(var(--secondary))", color: "hsl(var(--foreground))" }
                }
              >
                {msg.role === "assistant" ? <ChatMarkdown text={msg.content} /> : msg.content}
              </div>
            </div>
          ))}
          {chatLoading && (
            <div className="flex justify-start">
              <div className="px-3 py-2.5 rounded-2xl flex items-center gap-2" style={{ background: "hsl(var(--secondary))" }}>
                <Loader2 className="w-3 h-3 animate-spin text-muted-foreground" />
                <span className="text-[11px] font-mono text-muted-foreground">Thinking…</span>
              </div>
            </div>
          )}
          <div ref={chatEndRef} />
        </div>

        {/* Input */}
        <div className="px-3 py-3 border-t" style={{ borderColor: "hsl(var(--border))" }}>
          <div className="flex items-end gap-2 rounded-xl neu-inset p-1">
            <textarea
              ref={inputRef}
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); onSend(); } }}
              placeholder="Ask about your compliance results…"
              rows={1}
              className="flex-1 resize-none bg-transparent text-[12px] leading-relaxed px-2 py-1.5 focus:outline-none placeholder:text-muted-foreground/50"
              style={{ maxHeight: "80px", overflowY: "auto" }}
            />
            <button
              onClick={() => onSend()}
              disabled={!input.trim() || chatLoading}
              className="p-2 rounded-lg transition-all disabled:opacity-40 shrink-0"
              style={{ background: "hsl(var(--primary))", color: "hsl(var(--primary-foreground))" }}
            >
              <Send className="w-3.5 h-3.5" />
            </button>
          </div>
          <p className="text-[9px] font-mono text-muted-foreground/50 text-center mt-1.5">AI responses may contain errors. Not legal advice.</p>
        </div>
      </div>
    </>
  );
}
