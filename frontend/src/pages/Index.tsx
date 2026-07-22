import React, { useState, useRef, useCallback, useEffect } from "react";
import {
  FileDown, Loader2, Shield, AlertTriangle, CheckCircle2, ChevronDown, MapPin,
  FileText, RefreshCw, GitCompare, LayoutDashboard,
  Download, X, MessageCircle, Send, ChevronRight, ChevronLeft,
  CalendarClock, MessageSquare, RotateCcw, Wand2, HelpCircle, ArrowRight,
} from "lucide-react";
import { extractText } from "@/lib/extract-text";
import { linkifyRegulations, lookupRegulationUrl } from "@/lib/regulation-links";
import {
  generateActionPackage, generateActionPackageStream, startActionPackageJob, streamActionPackageJob, getActionPackageJobStatus, cancelActionPackageJob, exportGapAnalysis, exportDraftPolicy, healthCheck,
  getIndustries, draftPolicy, startDraftJob, streamDraftJob, getDraftJobStatus, cancelDraftJob, sendChatMessage,
  type ComplianceActionPackage, type AnalysisResult, type GapRow,
  type SourceAttribution, type SourceType, type VerificationStatus, type IndustryOption,
  type DraftedPolicy, type ChatMessage, type RewrittenPolicy, type RewrittenPolicySection, type RedlineChange,
  STATUS_LABELS,
  getSourceTypeLabel, getSourceTypeColor, getSourceTypeBg, getVerificationIcon,
} from "@/lib/api";
import { toast } from "sonner";

// ── Style maps ──

const JOB_KEY = "tpl_active_job";
const DRAFT_JOB_KEY = "tpl_active_draft_job";

function formatElapsed(totalSeconds: number): string {
  const m = Math.floor(totalSeconds / 60);
  const s = totalSeconds % 60;
  return m > 0 ? `${m}:${s.toString().padStart(2, "0")}` : `${s}s`;
}

// ── Demo samples ──
// Realistic-but-flawed sample policy so first-time visitors can see what
// the analyzer actually does on a real document.
const SAMPLE_POLICY_TEXT = `HIPAA PRIVACY AND SECURITY POLICY
Northbridge Family Medicine, P.C.
Adopted: January 2019  |  Last reviewed: March 2022

1. PURPOSE
Northbridge Family Medicine is committed to protecting the privacy of patient health information. All employees must follow this policy.

2. SCOPE
This policy applies to all staff, contractors, and volunteers who handle patient information at our two clinic locations.

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
This policy is reviewed by the office manager every two to three years.`;

const SAMPLE_DRAFT_DESCRIPTIONS: Record<string, string> = {
  healthcare:
    "HIPAA workforce sanctions policy for a 50-bed acute care hospital in NYC. Should cover progressive discipline for unauthorized PHI access, repeat offenders, and accidental vs. willful violations. Need to align with 45 CFR 164.530(e) and our state breach notification timelines.",
  education:
    "Mandatory child abuse reporting policy for a licensed childcare center in NYC. Should cover what staff must observe, who they must call (NY SCR), how fast, what documentation we keep, and confidentiality. Align with NY Social Services Law 413 and NYC DOHMH Article 47.",
  hoa:
    "55+ age verification policy for a HOPA-qualified condo association in Florida. Cover initial verification at sale, biennial recertification, exceptions for spouses and surviving residents, and documentation we must keep on file. Align with HOPA and Florida condo statutes.",
  other:
    "Whistleblower and non-retaliation policy for a 30-person nonprofit. Cover protected disclosures, who reports go to, confidentiality, investigation steps, and protections against retaliation.",
};

const STATUS_MAP: Record<string, { label: string; color: string; bg: string }> = {
  compliant: { label: "Compliant", color: "hsl(160 60% 36%)", bg: "hsl(160 60% 42% / 0.1)" },
  partial:   { label: "Partial",   color: "hsl(38 85% 44%)",  bg: "hsl(38 85% 52% / 0.1)" },
  gap:       { label: "Gap",       color: "hsl(25 90% 44%)",  bg: "hsl(25 90% 50% / 0.1)" },
  missing:   { label: "Missing",   color: "hsl(0 72% 48%)",   bg: "hsl(0 72% 51% / 0.1)" },
};

const RISK_MAP: Record<string, { label: string; color: string; bg: string }> = {
  critical: { label: "CRITICAL", color: "hsl(0 72% 48%)", bg: "hsl(0 72% 51% / 0.1)" },
  high:     { label: "HIGH",     color: "hsl(25 90% 44%)", bg: "hsl(25 90% 50% / 0.1)" },
  moderate: { label: "MODERATE", color: "hsl(38 85% 44%)", bg: "hsl(38 85% 52% / 0.1)" },
  low:      { label: "LOW",      color: "hsl(200 60% 44%)", bg: "hsl(200 60% 50% / 0.1)" },
  compliant:{ label: "COMPLIANT", color: "hsl(160 60% 36%)", bg: "hsl(160 60% 42% / 0.1)" },
};

function stripCiteTags(text: string): string {
  return text.replace(/<cite[^>]*>|<\/cite>/g, "");
}

// ── Tab configuration ──

const TABS = [
  { key: "overview", label: "Overview", icon: LayoutDashboard },
  { key: "gap_analysis", label: "Gap Analysis", icon: AlertTriangle },
] as const;

type TabKey = typeof TABS[number]["key"];

// ── Gap Row Component ──

function GapRowItem({ row, urlMap }: { row: GapRow; urlMap?: Record<string, string> }) {
  const [open, setOpen] = useState(false);
  const s = STATUS_MAP[row.status] || STATUS_MAP.gap;
  const r = RISK_MAP[row.risk_level] || RISK_MAP.moderate;

  return (
    <div className={`rounded-xl overflow-hidden mb-3 transition-shadow duration-200 ${open ? "neu-raised" : "neu-sm"}`}>
      <button onClick={() => setOpen((o) => !o)} className="w-full flex items-start gap-2.5 sm:gap-3 px-4 sm:px-5 py-3.5 sm:py-4 text-left active:opacity-80 transition-all touch-manipulation">
        <span className="text-[9px] sm:text-[10px] font-mono font-bold tracking-wider px-2 sm:px-2.5 py-1 rounded-full shrink-0 mt-0.5" style={{ color: r.color, border: `1.5px solid ${r.color}40`, background: r.bg }}>{r.label}</span>
        <div className="flex-1 min-w-0">
          <p className="text-[13px] sm:text-sm font-semibold text-foreground">{stripCiteTags(row.clause)}</p>
          <p className="text-[11px] sm:text-xs text-muted-foreground mt-1 line-clamp-2 leading-relaxed">{stripCiteTags(row.finding)}</p>
        </div>
        <ChevronDown className="w-4 h-4 text-muted-foreground shrink-0 mt-1 transition-transform duration-200" style={{ transform: open ? "rotate(180deg)" : "none" }} />
      </button>
      {open && (
        <div className="px-4 sm:px-5 pb-4 sm:pb-5 pt-0 space-y-3">
          <div className="h-px bg-border" />
          <div className="flex flex-wrap gap-2 items-center">
            <span className="text-[10px] font-mono uppercase tracking-wider text-muted-foreground">Risk:</span>
            <span className="text-[10px] font-mono font-bold px-2 py-0.5 rounded-full" style={{ color: r.color, background: r.bg }}>{r.label}</span>
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
          {row.oig_element && (
            <div className="flex items-center gap-1.5">
              <span className="text-[9px] font-mono uppercase tracking-wider text-muted-foreground">OIG Element:</span>
              <span className="text-[9px] font-mono font-bold px-2 py-0.5 rounded-full bg-primary/10 text-primary">{row.oig_element}</span>
            </div>
          )}
          {row.current_state && (
            <div>
              <p className="text-[10px] font-mono uppercase tracking-wider text-muted-foreground mb-1 font-medium">Current Policy Language</p>
              <p className="text-[12px] sm:text-[13px] text-foreground/70 italic leading-relaxed border-l-2 border-muted-foreground/30 pl-3">"{linkifyRegulations(stripCiteTags(row.current_state))}"</p>
            </div>
          )}
          <div>
            <p className="text-[10px] font-mono uppercase tracking-wider text-muted-foreground mb-1 font-medium">Finding</p>
            <p className="text-[13px] sm:text-sm text-foreground leading-relaxed">{linkifyRegulations(stripCiteTags(row.finding))}</p>
          </div>
          <div className="rounded-xl p-3 sm:p-4 neu-inset">
            <p className="text-[10px] font-mono uppercase tracking-wider mb-1.5 font-medium" style={{ color: "hsl(var(--primary))" }}>Suggested Policy Language</p>
            <p className="text-[13px] sm:text-sm text-foreground/85 italic leading-relaxed">"{linkifyRegulations(stripCiteTags(row.suggested_language))}"</p>
          </div>
          <p className="text-[10px] font-mono text-muted-foreground break-all">Cite: {linkifyRegulations(stripCiteTags(row.citation))}</p>
          {row.source_attribution && <SourceBadge attribution={row.source_attribution} urlMap={urlMap} />}
        </div>
      )}
    </div>
  );
}

// ── Source Attribution Badge Component ──

function SourceBadge({ attribution, urlMap }: { attribution?: SourceAttribution; urlMap?: Record<string, string> }) {
  if (!attribution) return null;
  const color = getSourceTypeColor(attribution.source_type);
  const bg = getSourceTypeBg(attribution.source_type);
  const icon = getVerificationIcon(attribution.verification_status);
  const label = getSourceTypeLabel(attribution.source_type);
  const sourceUrl =
    attribution.source_url ||
    (attribution.source_name && urlMap ? urlMap[attribution.source_name] : undefined);

  return (
    <div className="flex flex-wrap items-center gap-1.5">
      <span className="text-[9px] font-mono font-bold px-2 py-0.5 rounded-full" style={{ color, background: bg }}>
        {icon} {label}
      </span>
      {attribution.source_name && (
        sourceUrl ? (
          <a
            href={sourceUrl}
            target="_blank"
            rel="noopener noreferrer"
            className="text-[9px] font-mono text-primary hover:underline inline-flex items-center gap-0.5"
            title={`Open source: ${sourceUrl}`}
          >
            {attribution.source_name}
            <span aria-hidden="true">↗</span>
          </a>
        ) : (
          <span className="text-[9px] font-mono text-muted-foreground">{attribution.source_name}</span>
        )
      )}
      {attribution.warning && (
        <span className="text-[9px] font-mono text-destructive/80 italic">{attribution.warning}</span>
      )}
    </div>
  );
}

// ── Main Page ──

const FALLBACK_INDUSTRIES: IndustryOption[] = [
  { slug: "healthcare", name: "Healthcare", icon: "🏥", description: "Hospitals, clinics, health plans, covered entities" },
  { slug: "education", name: "Education / Childcare", icon: "🏫", description: "Private schools, preschools, childcare centers, franchises" },
  { slug: "hoa", name: "HOA / 55+ Communities", icon: "🏘️", description: "Homeowners associations, age-restricted 55+ communities" },
  { slug: "other", name: "Other / General", icon: "📋", description: "Any organization — best practices and inferred regulations" },
];

export default function Index() {
  const [text, setText] = useState(() => { try { return localStorage.getItem("tpl_text") || ""; } catch { return ""; } });
  const [fileName, setFileName] = useState(() => { try { return localStorage.getItem("tpl_fileName") || ""; } catch { return ""; } });
  const [pkg, setPkg] = useState<ComplianceActionPackage | null>(() => {
    try { const s = localStorage.getItem("tpl_pkg"); return s ? JSON.parse(s) : null; } catch { return null; }
  });
  const [loading, setLoading] = useState(false);
  const [pkgStreaming, setPkgStreaming] = useState(false);
  const [draftStreamText, setDraftStreamText] = useState("");
  const [loadSec, setLoadSec] = useState(0);
  const [error, setError] = useState("");
  const [drag, setDrag] = useState(false);
  const [parsing, setParsing] = useState(false);
  const [exporting, setExporting] = useState(false);
  const [industry, setIndustry] = useState("healthcare");
  const [industries, setIndustries] = useState<IndustryOption[]>(FALLBACK_INDUSTRIES);
  const [city, setCity] = useState("");
  const [stateCode, setStateCode] = useState("");
  const jurisdiction = [city.trim(), stateCode].filter(Boolean).join(", ");
  const [backendOnline, setBackendOnline] = useState(true);
  const [activeTab, setActiveTab] = useState<TabKey>("overview");
  // Set true the instant the user hits Cancel. Any generation in flight
  // (whether from run() or the resume-on-mount reattach) checks this before
  // applying its result, so an abandoned job can't pop a stale result/toast
  // back onto the screen after the user has already left.
  const cancelledRef = useRef(false);
  // Severity filter: when user taps a tile (Critical/Gap/Partial/Compliant) on the overview, jump to gap tab and filter rows.
  const [severityFilter, setSeverityFilter] = useState<"critical" | "gap" | "partial" | "compliant" | null>(null);
  const [retryCount, setRetryCount] = useState(0);
  const DRAFT_PLACEHOLDERS: Record<string, string> = {
    healthcare: [
      'e.g. "HIPAA workforce training policy for a hospital"',
      'e.g. "Security incident response policy for a medical practice"',
    ].join("\n"),
    education: [
      'e.g. "Code of conduct for students and families at a preschool"',
      'e.g. "Mandatory child abuse reporting policy for a childcare center"',
    ].join("\n"),
    hoa: [
      'e.g. "55+ age verification policy for an HOA community"',
      'e.g. "Grievance and dispute resolution policy for a homeowners association"',
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
        `Priority Findings:\n${ga.priority_findings?.slice(0, 5).map((f, i) => `${i + 1}. ${f}`).join("\n")}`,
        `Executive Summary: ${ga.audit_ready_summary}`,
        ga.review_frequency ? `Recommended Review: ${ga.review_frequency} — ${ga.next_review_recommended || ""}` : "",
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

  /**
   * Replace the active policy's full text with a chat assistant message.
   * Andrew's workflow: ask chat to rewrite a section → click "apply to policy"
   * on the response → the Rewritten Policy / Drafted Policy tab actually updates.
   * Explicit click (not auto-apply) so he keeps control.
   */
  const handleApplyChatToPolicy = (newText: string) => {
    const trimmed = (newText || "").trim();
    if (!trimmed) return;
    const stamp = new Date().toLocaleString();

    if (draftResult) {
      setDraftResult((prev) => prev ? {
        ...prev,
        full_text: trimmed,
        version: `${prev.version || "1.0"} (chat-refined)`,
        drafting_notes: `${prev.drafting_notes || ""}${prev.drafting_notes ? "\n\n" : ""}Refined via chat on ${stamp}.`.trim(),
      } : prev);
      toast.success("Draft updated", { description: "Drafted policy now shows the chat version." });
      return;
    }

    toast.error("No policy to update", { description: "Run an analysis or draft a policy first." });
  };

  const openChat = (mode: "analysis" | "draft") => {
    setChatMode(mode);
    if (chatHistory.length === 0) {
      const greeting: ChatMessage = {
        role: "assistant",
        content: mode === "draft"
          ? "I have your drafted policy in context. Ask me to rewrite sections, add clauses, or tailor language. When my reply is the version you want, click 'apply to policy' under the message — it replaces the drafted policy text."
          : "I have your full compliance analysis in context. Ask me anything — which gap to fix first, what a specific regulation actually requires, what the regulator looks for in an audit, or how to phrase remediation language for your board.",
      };
      setChatHistory([greeting]);
    }
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
      if (!extracted || extracted.trim().length < 50) throw new Error("Could not extract readable text.");
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
    if (fileRef.current) fileRef.current.value = "";
    try { localStorage.removeItem(JOB_KEY); } catch {}
  };

  const handleDownloadGapAnalysis = async () => {
    if (!pkg || exporting) return;
    setExporting(true);
    try {
      await exportGapAnalysis(pkg);
      toast.success("Gap analysis downloaded", { description: "Report .docx saved" });
    } catch (e: any) {
      toast.error("Download failed", { description: e.message });
    } finally {
      setExporting(false);
    }
  };

  // Determine which tabs have data
  const availableTabs = pkg ? TABS.filter((t) => {
    if (t.key === "overview") return true;
    if (t.key === "gap_analysis") return !!pkg.gap_analysis;
    return false;
  }) : [];

  return (
    <div className="min-h-screen text-foreground">
      {/* Header */}
      <header className="aqua-kiss aqua-bar sticky top-0 z-10 px-4 sm:px-8 py-3.5 flex items-center justify-between gap-2 relative">
        <div className="flex items-center gap-2.5 min-w-0 flex-1">
          <span className="nyt-masthead text-[26px] sm:text-3xl text-foreground truncate leading-none">
            The Policy Lab
          </span>
        </div>
        <div className="flex items-center gap-2">
          {!backendOnline && (
            <span className="text-[9px] font-mono px-2 py-1 rounded-full bg-destructive/10 text-destructive border border-destructive/20">BACKEND OFFLINE</span>
          )}
          {pkg && !loading && (
            <>
              <button onClick={handleDownloadGapAnalysis} disabled={exporting} title="Downloads the full gap analysis report as a Word document — opens with a one-page compliance certificate summary (score, rating, finding counts), followed by every finding, citation, and suggested policy language." className="font-mono text-[10px] font-medium px-3 py-2 rounded-xl bg-card neu-btn active:neu-pressed text-foreground transition-all tracking-wider touch-manipulation disabled:opacity-50 disabled:cursor-not-allowed inline-flex items-center gap-1.5">
                {exporting ? <Loader2 className="w-3 h-3 animate-spin" /> : <FileDown className="w-3 h-3" />}Report (.docx)
              </button>
              <button onClick={reset} className="font-mono text-[10px] font-medium px-3 py-2 rounded-xl bg-card neu-btn active:neu-pressed text-muted-foreground transition-all tracking-wider touch-manipulation">New</button>
            </>
          )}
          {draftResult && !loading && (
            <button onClick={reset} className="font-mono text-[10px] font-medium px-3 py-2 rounded-xl bg-card neu-btn active:neu-pressed text-muted-foreground transition-all tracking-wider touch-manipulation">New</button>
          )}
          {!pkg && !draftResult && !loading && (
            <button
              type="button"
              aria-label="Help"
              title="The Policy Lab — source-grounded policy drafting & gap analysis"
              className="w-8 h-8 rounded-full border border-[hsl(220_13%_85%)] flex items-center justify-center text-muted-foreground hover:text-foreground hover:bg-[hsl(220_14%_96%)] transition-colors"
            >
              <HelpCircle className="w-4 h-4" strokeWidth={1.5} />
            </button>
          )}
        </div>
      </header>

      <main className="max-w-4xl mx-auto px-4 sm:px-8 py-8 sm:py-12 pb-8 sm:pb-14">
        {/* ─── Input View ─── */}
        {!pkg && !draftResult && !loading && (
          <>
            <div className="mb-12 sm:mb-16">
              {mode === "analyze" && (
                <>
                  <h1 className="font-serif-display text-4xl sm:text-6xl lg:text-7xl font-black text-foreground mb-6 sm:mb-8 leading-[1.02] tracking-tight">
                    Upload your policy.<br />Know exactly where you stand.
                  </h1>
                  <p className="text-base sm:text-lg text-muted-foreground max-w-xl leading-relaxed">
                    Gap analysis, rewritten policy, and redline — all from one upload. Complete picture. Nothing left to guess.
                  </p>
                </>
              )}
              {mode === "draft" && (
                <>
                  <h1 className="font-serif-display text-4xl sm:text-6xl lg:text-7xl font-black text-foreground mb-6 sm:mb-8 leading-[1.02] tracking-tight">
                    Tell us what you need.<br />We'll write you a policy.
                  </h1>
                  <p className="text-base sm:text-lg text-muted-foreground max-w-xl leading-relaxed">
                    A policy generator that writes every section from scratch, specific to your situation. The more detail you give, the better the output. Every regulation cited.
                  </p>
                </>
              )}
            </div>

            {/* Mode toggle — NYT-style underline tabs */}
            <div className="mb-6 nyt-tabs">
              {([
                { key: "draft",   label: "Draft" },
                { key: "analyze", label: "Analyze" },
              ] as const).map((m) => (
                <button
                  key={m.key}
                  onClick={() => setMode(m.key)}
                  className={`nyt-tab ${mode === m.key ? "is-active" : ""}`}
                >
                  {m.label}
                </button>
              ))}
            </div>

            {/* Unified card — Industry & Location → divider → Describe / Upload → Generate button */}
            <div className="rounded-2xl neu-raised p-5 sm:p-6">

              {/* — Industry & Location — */}
              <p className="nyt-eyebrow mb-3">Industry &amp; location</p>
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
                <div className="flex items-stretch gap-2.5">
                  <input
                    type="text"
                    value={city}
                    onChange={(e) => setCity(e.target.value)}
                    placeholder="City (optional)"
                    className="aqua-rain text-[14px] px-4 py-3 rounded-xl text-foreground placeholder:text-muted-foreground/60 focus:outline-none flex-1 min-w-0"
                  />
                  <div className="relative flex-1 min-w-0">
                    <select
                      value={stateCode}
                      onChange={(e) => setStateCode(e.target.value)}
                      className="aqua-rain w-full text-[14px] px-4 py-3 pr-10 rounded-xl text-foreground focus:outline-none cursor-pointer appearance-none"
                    >
                      <option value="">State</option>
                      {["AL","AK","AZ","AR","CA","CO","CT","DC","DE","FL","GA","HI","ID","IL","IN","IA","KS","KY","LA","ME","MD","MA","MI","MN","MS","MO","MT","NE","NV","NH","NJ","NM","NY","NC","ND","OH","OK","OR","PA","RI","SC","SD","TN","TX","UT","VT","VA","WA","WV","WI","WY"].map(s => (
                        <option key={s} value={s}>{s}</option>
                      ))}
                    </select>
                    <ChevronDown className="w-4 h-4 absolute right-3 top-1/2 -translate-y-1/2 text-muted-foreground pointer-events-none" />
                  </div>
                </div>
                {jurisdiction && (
                  <span className="text-[10px] font-mono text-primary/70 font-medium">{jurisdiction}</span>
                )}
              </div>

              <hr className="pl-divider" />

              {/* — Draft: Describe the policy — */}
              {mode === "draft" && (
                <>
                  <p className="nyt-eyebrow mb-3">Describe the policy you need</p>
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
                        toast.success("Sample request loaded — hit Generate to run it");
                      }}
                      className="pl-sample-link"
                    >
                      <Wand2 className="w-3.5 h-3.5" strokeWidth={1.75} />
                      Load a sample
                    </button>
                    <span className="text-[12px] font-mono text-muted-foreground/70">{draftDesc.length} / 2000</span>
                  </div>
                  {industry === "other" && (
                    <p className="text-[10px] text-muted-foreground mt-2 leading-relaxed">
                      Mention the type of business or operation in your description for best results.
                    </p>
                  )}
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
                  <div className="mt-3 flex items-center justify-end">
                    <button
                      type="button"
                      onClick={() => {
                        setText(SAMPLE_POLICY_TEXT);
                        setFileName("sample-policy.txt");
                        setError("");
                        toast.success("Sample policy loaded — hit Generate to run it");
                      }}
                      className="pl-sample-link"
                    >
                      <Wand2 className="w-3.5 h-3.5" strokeWidth={1.75} />
                      Load a sample
                    </button>
                  </div>
                </>
              )}

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
              Checks live regulatory sources for the latest guidance
            </p>

          </>
        )}

        {/* ─── Loading Pipeline ─── */}
        {loading && (
          <div className="flex flex-col items-center justify-center gap-4 py-16">
            <Loader2 className="w-8 h-8 animate-spin" style={{ color: "hsl(var(--primary))" }} />
            <p className="font-mono text-xs font-medium text-center px-4" style={{ color: "hsl(var(--primary))" }}>
              {mode === "draft"
                ? (draftStreamText ? "writing your policy..." : loadSec < 6 ? "reviewing your requirements..." : "structuring the policy...")
                : (loadSec < 8 ? "reading your policy..." : loadSec < 18 ? "finding the gaps..." : loadSec < 30 ? "scoring exposure..." : "finalizing the analysis...")}
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
                  START OVER
                </button>
              </div>
            </div>
            <p className="text-[11px] text-muted-foreground/80 leading-relaxed -mt-2">
              <strong className="text-foreground/80 font-medium">Download Policy</strong> gets you the full drafted document as an editable Word file (.docx), ready to review, revise, and adopt.
            </p>

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
                    <p className="text-[13px] text-foreground leading-relaxed whitespace-pre-wrap">{linkifyRegulations(sec.content)}</p>
                  </div>
                )) : (
                  <p className="text-[13px] text-foreground leading-relaxed whitespace-pre-wrap">{linkifyRegulations(draftResult.full_text)}</p>
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
              {pkgStreaming && (
                <div className="flex items-center gap-1.5 px-3 py-2 rounded-xl font-mono text-[10px] font-medium text-foreground/80 neu-btn shrink-0">
                  <Loader2 className="w-3 h-3 animate-spin" />
                  more loading…
                </div>
              )}
            </div>

            <p className="text-[11px] text-muted-foreground/80 leading-relaxed -mt-2">
              <strong className="text-foreground/80 font-medium">Report (.docx)</strong> — opens with a one-page compliance certificate (score, rating, finding counts), followed by every finding, citation, and drop-in policy language.
            </p>

            {/* Tab content */}
            {activeTab === "overview" && <OverviewTab pkg={pkg} />}
            {activeTab === "gap_analysis" && pkg.gap_analysis && (
              <GapAnalysisTab
                result={pkg.gap_analysis}
                urlMap={pkg.kb_source_urls}
                severityFilter={severityFilter}
                onChangeFilter={setSeverityFilter}
              />
            )}
            {/* Ask AI button */}
            <button
              onClick={() => openChat("analysis")}
              style={{ background: "hsl(var(--primary))", color: "hsl(var(--primary-foreground))" }}
              className="fixed bottom-20 right-4 sm:bottom-6 sm:right-6 z-30 flex items-center gap-2 px-4 py-3 rounded-2xl shadow-lg font-mono text-[10px] font-bold tracking-wider touch-manipulation"
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
            onApplyToPolicy={handleApplyChatToPolicy}
            canApply={Boolean(pkg?.rewritten_policy || draftResult)}
          />
        )}
      </main>

      <footer className="border-t border-foreground/8 px-4 py-8 sm:py-10 text-center space-y-2.5">
        <p className="text-[10px] text-muted-foreground/60 font-mono leading-relaxed max-w-sm mx-auto">
          Disclaimer: This tool uses AI to generate content and may produce inaccuracies. All output must be reviewed before official use. Final judgment and responsibility remain with the end user.
        </p>
        <p className="text-[10px] text-muted-foreground/35 font-mono tracking-wider">
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

function ComplianceGauge({ score }: { score: number }) {
  const clamped = Math.max(0, Math.min(100, score));
  const radius = 36;
  const circ = 2 * Math.PI * radius;
  const dash = (clamped / 100) * circ;
  const color = clamped >= 70 ? "#22c55e" : clamped >= 40 ? "#f59e0b" : "#ef4444";
  const label = clamped >= 70 ? "Mostly Compliant" : clamped >= 40 ? "Partial Gaps" : "Critical Gaps";
  return (
    <div className="flex flex-col items-center justify-center gap-1">
      <svg width="88" height="88" viewBox="0 0 88 88">
        <circle cx="44" cy="44" r={radius} fill="none" stroke="hsl(var(--border))" strokeWidth="7" />
        <circle cx="44" cy="44" r={radius} fill="none" stroke={color} strokeWidth="7"
          strokeDasharray={`${dash} ${circ}`} strokeLinecap="round"
          transform="rotate(-90 44 44)" style={{ transition: "stroke-dasharray 0.6s ease" }} />
        <text x="44" y="48" textAnchor="middle" fontSize="15" fontWeight="700" fill={color} fontFamily="monospace">{clamped.toFixed(0)}%</text>
      </svg>
      <p className="text-[9px] font-mono uppercase tracking-wider text-muted-foreground">{label}</p>
    </div>
  );
}

function OverviewTab({ pkg }: { pkg: ComplianceActionPackage }) {
  const ga = pkg.gap_analysis;
  const totalIssues = ga.critical_count + ga.gap_count + ga.partial_count;

  return (
    <div className="space-y-4">
      {/* Status banner */}
      <div className="rounded-2xl bg-card neu-raised p-4 sm:p-5">
        <div className="flex items-start gap-4">
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-2 mb-3">
              {totalIssues > 0 ? <AlertTriangle className="w-4 h-4 text-destructive" /> : <CheckCircle2 className="w-4 h-4 text-green-600" />}
              <span className="text-[10px] font-mono uppercase tracking-wider font-medium text-muted-foreground">The Policy Lab — Compliance Report</span>
            </div>
            <p className="text-[13px] sm:text-sm text-foreground/85 leading-relaxed">{ga.audit_ready_summary}</p>
            <div className="flex flex-wrap gap-2 mt-3">
              <span className="text-[10px] font-mono px-2 py-0.5 rounded-full bg-secondary text-muted-foreground">{ga.policy_type}</span>
              <span className="text-[10px] font-mono px-2 py-0.5 rounded-full bg-secondary text-muted-foreground">{ga.regulations_applied?.length || 0} regulations</span>
              <span className="text-[10px] font-mono px-2 py-0.5 rounded-full bg-secondary text-muted-foreground">{totalIssues} issues</span>
              <span className="text-[10px] font-mono px-2 py-0.5 rounded-full bg-secondary text-muted-foreground">{pkg.completed_outputs.length}/7 outputs</span>
            </div>
          </div>
          {ga.compliance_score != null && (
            <div className="shrink-0">
              <ComplianceGauge score={ga.compliance_score} />
            </div>
          )}
        </div>
      </div>


      {/* Review Date */}
      {(pkg.gap_analysis?.next_review_recommended || pkg.gap_analysis?.review_frequency) && (
        <div className="rounded-xl p-4 neu-raised flex items-start gap-3">
          <CalendarClock className="w-4 h-4 mt-0.5 shrink-0" style={{ color: "hsl(var(--primary))" }} />
          <div>
            <p className="text-[10px] font-mono uppercase tracking-wider text-muted-foreground mb-1">Policy Review Schedule</p>
            {pkg.gap_analysis.review_frequency && (
              <p className="text-sm font-semibold text-foreground">{pkg.gap_analysis.review_frequency}</p>
            )}
            {pkg.gap_analysis.next_review_recommended && (
              <p className="text-xs text-muted-foreground mt-0.5">Next review: {pkg.gap_analysis.next_review_recommended}</p>
            )}
          </div>
        </div>
      )}

      {/* Source Attribution & Verification Status */}
      <div className="rounded-xl p-4 neu-raised">
        <p className="text-[10px] font-mono uppercase tracking-wider text-muted-foreground mb-3">Source Attribution & Verification</p>

        {pkg.kb_sources_used && pkg.kb_sources_used.length > 0 && (
          <div className="mb-3">
            <p className="text-[10px] font-mono uppercase tracking-wider mb-1.5" style={{ color: "hsl(200 60% 44%)" }}>Knowledge Base Sources Used</p>
            <div className="flex flex-wrap gap-1.5">
              {pkg.kb_sources_used.map((src, i) => {
                const url = pkg.kb_source_urls?.[src];
                const baseStyle = { color: "hsl(200 60% 44%)", background: "hsl(200 60% 50% / 0.1)" };
                return url ? (
                  <a
                    key={i}
                    href={url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="text-[9px] font-mono px-2 py-0.5 rounded-full inline-flex items-center gap-1 hover:underline"
                    style={baseStyle}
                    title={`Open authoritative source: ${url}`}
                  >
                    {src}
                    <span aria-hidden="true">↗</span>
                  </a>
                ) : (
                  <span key={i} className="text-[9px] font-mono px-2 py-0.5 rounded-full" style={baseStyle}>{src}</span>
                );
              })}
            </div>
          </div>
        )}

        {pkg.live_research_used && (
          <div className="mb-3">
            <span className="text-[9px] font-mono font-bold px-2 py-0.5 rounded-full" style={{ color: "hsl(270 60% 50%)", background: "hsl(270 60% 50% / 0.1)" }}>🌐 Live Research Used</span>
            <p className="text-[9px] text-muted-foreground mt-1">Some findings were augmented with controlled live research from curated regulatory sources.</p>
          </div>
        )}

        {pkg.verification_overall && (
          <div className="rounded-lg p-3 neu-inset">
            <p className="text-[10px] font-mono uppercase tracking-wider mb-1" style={{ color: pkg.unverified_claim_count && pkg.unverified_claim_count > 0 ? "hsl(38 85% 44%)" : "hsl(160 60% 36%)" }}>
              {pkg.unverified_claim_count && pkg.unverified_claim_count > 0 ? "⚠️ Verification Status" : "✅ Verification Status"}
            </p>
            <p className="text-[11px] text-foreground/80 leading-relaxed">{pkg.verification_overall}</p>
          </div>
        )}

        {(!pkg.kb_sources_used || pkg.kb_sources_used.length === 0) && !pkg.live_research_used && (
          <div className="rounded-lg p-3" style={{ background: "hsl(38 85% 52% / 0.08)" }}>
            <p className="text-[10px] font-mono font-bold" style={{ color: "hsl(38 85% 44%)" }}>⚠️ Model-Only Mode</p>
            <p className="text-[10px] text-muted-foreground mt-1">No source material was available in the knowledge base. All findings are model inference only and MUST be independently verified by qualified compliance counsel.</p>
          </div>
        )}
      </div>
    </div>
  );
}


function GapAnalysisTab({ result, urlMap, severityFilter, onChangeFilter }: { result: AnalysisResult; urlMap?: Record<string, string>; severityFilter?: "critical" | "gap" | "partial" | "compliant" | null; onChangeFilter?: (s: "critical" | "gap" | "partial" | "compliant" | null) => void }) {
  const nonCompliant = result.gap_table.filter((r) => r.status !== "compliant");
  const compliantItems = result.gap_table.filter((r) => r.status === "compliant");
  const criticalItems = result.gap_table.filter((r) => r.risk_level === "critical");

  // Inline filter chips — one row of buttons, each shows count, tap to narrow the list. "All" resets.
  const chips = [
    { key: null,        label: "All",       count: result.gap_table.length, color: "hsl(220 14% 22%)" },
    { key: "critical" as const,  label: "Critical",  count: result.gap_table.filter((r) => r.risk_level === "critical").length, color: "hsl(0 72% 48%)" },
    { key: "gap" as const,       label: "High",      count: result.gap_table.filter((r) => r.status === "gap").length,          color: "hsl(25 90% 44%)" },
    { key: "partial" as const,   label: "Moderate",  count: result.gap_table.filter((r) => r.status === "partial").length,      color: "hsl(38 85% 44%)" },
    { key: "compliant" as const, label: "Compliant", count: result.gap_table.filter((r) => r.status === "compliant").length,    color: "hsl(160 60% 36%)" },
  ];

  const filterPredicate = severityFilter
    ? (r: typeof result.gap_table[number]) => {
        if (severityFilter === "critical") return r.risk_level === "critical";
        return r.status === severityFilter;
      }
    : null;
  const filteredRows = filterPredicate ? result.gap_table.filter(filterPredicate) : null;

  return (
    <div className="space-y-4">
      {/* Filter chips — surgical drilldown without leaving the page */}
      <div className="flex flex-wrap gap-2">
        {chips.map((chip) => {
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
          filteredRows.map((row, i) => <GapRowItem key={i} row={row} urlMap={urlMap} />)
        )
      ) : (
        <>
          {criticalItems.length > 0 && (
            <div className="rounded-2xl p-4 neu-raised border-l-4 border-l-destructive" style={{ background: "hsl(0 72% 51% / 0.04)" }}>
              <p className="text-[11px] font-mono uppercase tracking-wider font-bold text-destructive mb-2">
                Immediate Action Required — {criticalItems.length} Critical Finding{criticalItems.length !== 1 ? "s" : ""}
              </p>
              {result.priority_findings?.slice(0, 3).map((f, i) => (
                <p key={i} className="text-[11px] sm:text-xs text-foreground/80 leading-relaxed pl-3 border-l-2 border-l-destructive/30 mb-1">{linkifyRegulations(stripCiteTags(f))}</p>
              ))}
            </div>
          )}

          {nonCompliant.length > 0 && (
            <p className="font-mono text-[10px] uppercase tracking-widest text-muted-foreground font-medium px-1">
              Fix these {nonCompliant.length} issue{nonCompliant.length !== 1 ? "s" : ""}
            </p>
          )}

          {nonCompliant.map((row, i) => <GapRowItem key={i} row={row} urlMap={urlMap} />)}

          {compliantItems.length > 0 && (
            <details className="group">
              <summary className="font-mono text-[10px] uppercase tracking-widest text-muted-foreground font-medium px-1 cursor-pointer list-none flex items-center gap-2 select-none">
                <ChevronDown className="w-3 h-3 transition-transform duration-200 group-open:rotate-180" />
                {compliantItems.length} compliant area{compliantItems.length !== 1 ? "s" : ""}
              </summary>
              <div className="mt-3">{compliantItems.map((row, i) => <GapRowItem key={i} row={row} urlMap={urlMap} />)}</div>
            </details>
          )}
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


function RedlineTab({ changes }: { changes: RedlineChange[] }) {
  const added = changes.filter(c => c.type === "added").length;
  const removed = changes.filter(c => c.type === "removed").length;
  const modified = changes.filter(c => c.type === "modified").length;

  return (
    <div className="space-y-4">
      <div className="rounded-2xl bg-card neu-raised p-4 flex gap-6">
        <div className="text-center"><p className="text-lg font-bold" style={{ color: "hsl(160 60% 36%)" }}>{added}</p><p className="text-[10px] font-mono text-muted-foreground">Added</p></div>
        <div className="text-center"><p className="text-lg font-bold" style={{ color: "hsl(0 72% 48%)" }}>{removed}</p><p className="text-[10px] font-mono text-muted-foreground">Removed</p></div>
        <div className="text-center"><p className="text-lg font-bold" style={{ color: "hsl(38 85% 44%)" }}>{modified}</p><p className="text-[10px] font-mono text-muted-foreground">Modified</p></div>
      </div>

      <div className="space-y-2">
        {changes.map((change, i) => (
          <div key={i} className="rounded-xl overflow-hidden neu-sm">
            <div className="px-4 py-2 flex items-center gap-2">
              <span className="text-[9px] font-mono font-bold px-2 py-0.5 rounded-full" style={{
                color: change.type === "added" ? "hsl(160 60% 36%)" : change.type === "removed" ? "hsl(0 72% 48%)" : "hsl(38 85% 44%)",
                background: change.type === "added" ? "hsl(160 60% 42% / 0.1)" : change.type === "removed" ? "hsl(0 72% 51% / 0.1)" : "hsl(38 85% 52% / 0.1)",
              }}>
                {change.type.toUpperCase()}
              </span>
              {change.section && <span className="text-[10px] font-mono text-muted-foreground">{change.section}</span>}
              {change.regulation_ref && <span className="text-[9px] font-mono text-muted-foreground ml-auto">{change.regulation_ref}</span>}
            </div>
            <div className="px-4 pb-3">
              {change.type === "added" && <p className="text-xs leading-relaxed" style={{ color: "hsl(160 60% 36%)" }}>+ {change.revised_text}</p>}
              {change.type === "removed" && <p className="text-xs leading-relaxed line-through" style={{ color: "hsl(0 72% 48%)" }}>- {change.original_text}</p>}
              {change.type === "modified" && (
                <>
                  <p className="text-xs leading-relaxed line-through" style={{ color: "hsl(0 72% 48%)" }}>- {change.original_text}</p>
                  <p className="text-xs leading-relaxed" style={{ color: "hsl(160 60% 36%)" }}>+ {change.revised_text}</p>
                </>
              )}
            </div>
          </div>
        ))}
      </div>
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
  onApplyToPolicy: (text: string) => void;
  canApply: boolean;
}

function ChatPanel({ open, onClose, history, input, setInput, onSend, chatLoading, chatEndRef, onApplyToPolicy, canApply }: ChatPanelProps) {
  const inputRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    if (open && inputRef.current) inputRef.current.focus();
  }, [open]);

  if (!open) return null;

  const SUGGESTIONS = [
    "What's the highest priority fix?",
    "Draft an email to staff about these gaps",
    "Explain the HIPAA penalty for this violation",
    "What should I fix in the first 30 days?",
  ];

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
            <div className="space-y-3">
              <p className="text-[12px] text-muted-foreground leading-relaxed">Ask me anything about your compliance results — I can explain findings, suggest fixes, draft communications, or help you prioritize.</p>
              <div className="space-y-1.5">
                {SUGGESTIONS.map((s) => (
                  <button
                    key={s}
                    onClick={() => { setInput(s); setTimeout(() => inputRef.current?.focus(), 50); }}
                    className="w-full text-left px-3 py-2 rounded-xl text-[11px] font-mono text-muted-foreground hover:text-foreground transition-colors touch-manipulation"
                    style={{ background: "hsl(var(--secondary))" }}
                  >
                    {s}
                  </button>
                ))}
              </div>
            </div>
          )}
          {history.map((msg, i) => {
            // Show "apply to policy" only on substantive assistant replies
            // (>= 200 chars filters out greetings and short chit-chat).
            const showApply = canApply && msg.role === "assistant" && msg.content.trim().length >= 200;
            return (
              <div key={i} className={`flex flex-col ${msg.role === "user" ? "items-end" : "items-start"}`}>
                <div
                  className="max-w-[85%] px-3 py-2.5 rounded-2xl text-[12px] leading-relaxed whitespace-pre-wrap"
                  style={msg.role === "user"
                    ? { background: "hsl(var(--primary))", color: "hsl(var(--primary-foreground))" }
                    : { background: "hsl(var(--secondary))", color: "hsl(var(--foreground))" }
                  }
                >
                  {msg.content}
                </div>
                {showApply && (
                  <button
                    onClick={() => onApplyToPolicy(msg.content)}
                    className="mt-1.5 inline-flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-[10px] font-mono font-bold tracking-wider neu-btn active:neu-pressed transition-all touch-manipulation"
                    style={{ background: "hsl(var(--primary))", color: "hsl(var(--primary-foreground))" }}
                    title="Replace the rewritten/drafted policy with this message"
                  >
                    <Wand2 className="w-3 h-3" />
                    apply to policy
                  </button>
                )}
              </div>
            );
          })}
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
