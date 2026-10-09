/**
 * API client v3 — Source-Grounded Compliance Intelligence System.
 * All calls go through the FastAPI backend. No direct LLM calls from frontend.
 * Every output now carries source attribution and verification status.
 */

const API_BASE = import.meta.env.VITE_API_URL || "";

/**
 * A random id for this browser session, used to bind background jobs to the
 * client that started them. Everyone shares one app password, so this is not
 * authentication -- it only answers "is this the same client that started
 * this job?", which stops one person's policy text and analysis being
 * readable by another who has a job id.
 *
 * sessionStorage, not localStorage: it should not outlive the tab, and a new
 * session getting a new id is the correct behaviour.
 */
const CLIENT_ID_KEY = "policylab_client_id";

function getClientId(): string {
  try {
    const existing = sessionStorage.getItem(CLIENT_ID_KEY);
    if (existing) return existing;
    const generated =
      typeof crypto !== "undefined" && crypto.randomUUID
        ? crypto.randomUUID()
        : `${Date.now()}-${Math.random().toString(36).slice(2)}`;
    sessionStorage.setItem(CLIENT_ID_KEY, generated);
    return generated;
  } catch {
    // Private browsing can throw on storage access. Jobs then fall back to
    // the shared anonymous bucket rather than the app breaking.
    return "";
  }
}

function authHeaders(): Record<string, string> {
  const headers: Record<string, string> = {};
  const cid = getClientId();
  if (cid) headers["x-client-id"] = cid;
  return headers;
}

// A start request that fails without a JSON body never reached the app: the
// hosting platform answered instead, almost always because the server was
// restarting (a deploy, or a crash from running out of memory). Saying so is
// more useful than "Failed to start", which reads like a bug in the request.
function startFailureMessage(status: number, what: "analysis" | "draft"): string {
  if (status === 502 || status === 503 || status === 504) {
    return `The server was restarting and could not start your ${what}. Wait a minute and try again.`;
  }
  return `Failed to start ${what} (${status}).`;
}

// Pull a readable message out of an error response body (FastAPI-style
// `detail` as a string or a list of {msg}), or a caught Error / string.
function extractErrorDetail(data: unknown, fallback: string): string {
  if (typeof data === "string") return data || fallback;
  if (data instanceof Error) return data.message || fallback;
  if (data && typeof data === "object") {
    const detail = (data as { detail?: unknown }).detail;
    if (typeof detail === "string" && detail) return detail;
    if (Array.isArray(detail)) {
      const msgs = detail
        .map((d) =>
          typeof d === "string"
            ? d
            : d && typeof d === "object" && typeof (d as { msg?: unknown }).msg === "string"
              ? (d as { msg: string }).msg
              : "",
        )
        .filter(Boolean);
      if (msgs.length) return msgs.join("; ");
    }
    const message = (data as { message?: unknown }).message;
    if (typeof message === "string" && message) return message;
  }
  return fallback;
}

// ── Source Attribution Types (Phase 3) ──

export type SourceType = "model_knowledge" | "retrieved_source" | "live_research" | "verified_source";
// "cannot_determine" is distinct from "unverified": there, a current source
// was checked and did not support the claim; here, the source's own standing
// (proposed, superseded, historical, unknown) makes any conclusion about
// present law impossible. Both are fail-closed, but they tell the reader
// different things about what to go and check.
export type VerificationStatus =
  | "verified"
  | "partially_verified"
  | "unverified"
  | "contradicted"
  | "cannot_determine";

export interface SourceAttribution {
  source_type: SourceType;
  verification_status: VerificationStatus;
  source_name?: string;
  source_citation?: string;
  source_url?: string;
  source_date?: string;
  confidence: number;
  warning?: string;
}

// ── Types ──

export interface GapRow {
  clause: string;
  regulations: string[];
  status: "compliant" | "partial" | "gap" | "missing";
  risk_level: "critical" | "high" | "moderate" | "low" | "compliant";
  current_state?: string;
  finding: string;
  // What the cited text itself requires -- the only part checked against the
  // regulation. Recommendations are kept apart and never attributed to it.
  regulatory_requirement?: string | null;
  recommendations?: string[];
  // Two independent fields, set when the package is final: is it established
  // law, and how urgent is it. See lib/findings.ts.
  evidence_status?: "verified_requirement" | "needs_source_review" | "recommendation" | null;
  priority?: "must_fix" | "should_fix" | null;
  suggested_language: string;
  citation: string;
  remediation_priority: string;
  // What kind of duty this finding asserts. "unverified_requirement" is set
  // server-side by the entailment gate, never by the model: the finding was
  // presented as legally required, but the cited source does not establish it.
  obligation_type?:
    | "required"
    | "guidance"
    | "best_practice"
    | "organizational_choice"
    | "unverified_requirement";
  obligation_note?: string;
  // What the finding is a statement about. Only a policy document is ever
  // read, so a finding is a gap in the document or a question about practice
  // -- never a determination that the organization is out of compliance.
  finding_kind?: "document_gap" | "implementation_question";
  implementation_question?: string | null;
  verification_warning?: string;
  evidence?: VerificationEvidence;
  source_attribution?: SourceAttribution;
}

/** Which state-law sources a run actually consulted. Derived server-side from retrieval. */
export interface StateCoverage {
  jurisdiction: string;
  state_text_available: boolean;
  sources_consulted: Array<{ name: string; url?: string | null; kind: "knowledge_base" | "live_search" }>;
  summary: string;
}

export type ClaimSupport =
  | "SUPPORTED" | "PARTIALLY_SUPPORTED" | "NOT_SUPPORTED" | "CONTRADICTED" | "NOT_CHECKED";

export interface VerificationEvidence {
  claim_id: string;
  claim_text: string;
  status: VerificationStatus;
  citation?: string | null;
  source: {
    name?: string | null;
    url?: string | null;
    version_date?: string | null;
    excerpt?: string | null;
    // The excerpt split by section: the passage the requirement was checked
    // against, plus any other cited section or paragraph it incorporates.
    passages?: { citation: string; role: string; text: string }[];
  };
  checks: {
    citation_exists: boolean;
    specifics_supported?: boolean | null;
    claim_support: ClaimSupport;
  };
  reason: string;
}

export interface AnalysisResult {
  policy_type: string;
  scope: string;
  methodology: string;
  regulations_applied: string[];
  last_updated_note?: string;
  critical_count: number;
  gap_count: number;
  partial_count: number;
  compliant_count: number;
  compliance_score?: number;
  priority_findings: string[];
  gap_table: GapRow[];
  audit_ready_summary: string;
  source_attributions?: SourceAttribution[];
  verification_summary?: string;
  retrieved_sources_used?: string[];
  live_research_used: boolean;
  findings_summary?: string | null;
}

// ── Chat Types ──

export interface ChatMessage {
  role: "user" | "assistant";
  content: string;
}

export interface ChatResponse {
  response: string;
}

export interface RewrittenPolicySection {
  section_title: string;
  original_text: string;
  rewritten_text: string;
  changes_summary: string;
  regulation_refs: string[];
  source_attribution?: SourceAttribution;
}

export interface RewrittenPolicy {
  policy_title: string;
  effective_date: string;
  version_note: string;
  sections: RewrittenPolicySection[];
  full_text: string;
  change_summary: string;
  source_attributions?: SourceAttribution[];
  retrieved_sources_used?: string[];
  live_research_used: boolean;
}

export interface RedlineChange {
  type: "added" | "removed" | "modified";
  section?: string;
  original_text?: string;
  revised_text?: string;
  regulation_ref?: string;
}

export interface RemediationTask {
  task_id: string;
  title: string;
  description: string;
  phase: string;
  risk_level: string;
  responsible_party: string;
  deliverable: string;
  regulation_refs: string[];
  dependencies: string[];
  verification_method: string;
}

export interface RemediationPhase {
  phase_number: number;
  phase_name: string;
  time_range: string;
  objective: string;
  tasks: RemediationTask[];
}

export interface RemediationPlan {
  plan_title: string;
  total_tasks: number;
  critical_tasks_first_30: number;
  phases: RemediationPhase[];
  success_criteria: string;
  resource_requirements: string;
  source_attributions?: SourceAttribution[];
  retrieved_sources_used?: string[];
  live_research_used: boolean;
}

export interface BoardSummary {
  headline: string;
  overall_status: string;
  risk_summary: string;
  key_findings: string[];
  regulatory_exposure: string;
  remediation_status: string;
  recommended_actions: string[];
  budget_impact?: string;
  next_review_date?: string;
  prepared_by?: string;
  prepared_date?: string;
  source_attributions?: SourceAttribution[];
  retrieved_sources_used?: string[];
  live_research_used: boolean;
}

export interface ChecklistItem {
  item_id: string;
  action: string;
  category: string;
  priority: string;
  responsible_role: string;
  deadline: string;
  regulation_ref: string;
  verification: string;
  evidence_needed: string;
  status: string;
}

export interface ImplementationChecklist {
  total_items: number;
  critical_items: number;
  categories: string[];
  items: ChecklistItem[];
  completion_timeline: string;
  source_attributions?: SourceAttribution[];
  retrieved_sources_used?: string[];
  live_research_used: boolean;
}

export type PackageStatus =
  | "pending"
  | "retrieving"
  | "analyzing"
  | "verifying"
  | "rewriting"
  | "generating_redline"
  | "finding_adjacent"
  | "building_remediation"
  | "drafting_board_summary"
  | "building_checklist"
  | "complete"
  | "failed";

export interface ComplianceActionPackage {
  package_id: string;
  created_at: string;
  source_file_name?: string;
  policy_type: string;
  jurisdiction?: string;
  gap_analysis: AnalysisResult;
  rewritten_policy?: RewrittenPolicy;
  redline_changes?: RedlineChange[];
  remediation_plan?: RemediationPlan;
  board_summary?: BoardSummary;
  implementation_checklist?: ImplementationChecklist;
  status: PackageStatus;
  completed_outputs: string[];
  error_message?: string;
  kb_sources_used?: string[];
  kb_source_urls?: Record<string, string>;
  source_snippets?: SourceSnippet[];
  live_research_used: boolean;
  verification_overall?: string;
  unverified_claim_count?: number;
  state_coverage?: StateCoverage | null;
}

// ── Status labels for the pipeline ──

export const PIPELINE_STEPS = [
  { key: "retrieving", label: "Retrieving Sources", status: "retrieving" },
  { key: "gap_analysis", label: "Gap Analysis", status: "analyzing" },
  { key: "verifying", label: "Verifying Claims", status: "verifying" },
] as const;

export const STATUS_LABELS: Record<string, string> = {
  pending: "Preparing...",
  retrieving: "Retrieving source material...",
  analyzing: "Analyzing gaps...",
  rewriting: "Rewriting policy...",
  generating_redline: "Generating redline...",
  building_remediation: "Building remediation plan...",
  drafting_board_summary: "Drafting board summary...",
  building_checklist: "Building checklist...",
  verifying: "Verifying claims...",
  complete: "Complete",
  failed: "Failed",
};

// ── API Functions ──

/**
 * Generate the Complete Compliance Action Package from text.
 */
export interface IndustryOption {
  slug: string;
  name: string;
  icon: string;
  description: string;
}

export async function getIndustries(): Promise<IndustryOption[]> {
  try {
    const response = await fetch(`${API_BASE}/api/industries`, { headers: authHeaders() });
    if (!response.ok) return [];
    const data = await response.json();
    return data.industries || [];
  } catch {
    return [];
  }
}

/**
 * "Draft revisions" — write a proposed revision of the policy aimed at the
 * findings of an existing gap analysis. Does not re-run the analysis, so the
 * result is unconfirmed until the user re-checks it.
 */
export async function fixAllGaps(
  text: string,
  gapAnalysis: AnalysisResult,
  industry?: string,
  jurisdiction?: string,
): Promise<RewrittenPolicy> {
  const response = await fetch(`${API_BASE}/api/action-package/rewrite`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...authHeaders() },
    body: JSON.stringify({
      text,
      gap_analysis: gapAnalysis,
      industry: industry || "healthcare",
      jurisdiction: jurisdiction || undefined,
    }),
  });

  if (!response.ok) {
    const errorData = await response.json().catch(() => ({ detail: "Rewrite failed" }));
    throw new Error(extractErrorDetail(errorData, `Rewrite failed (${response.status})`));
  }

  return response.json();
}

/**
 * Export ONLY the rewritten/updated policy as a clean .docx.
 * No gap analysis, no redline — just the new policy text formatted
 * as a real policy document.
 */
/** The download name from Content-Disposition: the UTF-8 `filename*` when
 *  present (it keeps characters like "—"), else the plain `filename`. */
function filenameFromDisposition(header: string, fallback: string): string {
  const star = header.match(/filename\*\s*=\s*UTF-8''([^;]+)/i);
  if (star) {
    try { return decodeURIComponent(star[1].trim()); } catch { /* fall through */ }
  }
  const plain = header.match(/filename\s*=\s*"([^"]*)"/i) ?? header.match(/filename\s*=\s*([^;]+)/i);
  return plain ? plain[1].trim() : fallback;
}

export async function exportUpdatedPolicy(
  pkg: ComplianceActionPackage,
): Promise<void> {
  if (!pkg.rewritten_policy) {
    throw new Error("No updated policy available to download");
  }
  const response = await fetch(`${API_BASE}/api/export-updated-policy`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...authHeaders() },
    body: JSON.stringify({
      rewritten_policy: pkg.rewritten_policy,
      source_file_name: pkg.source_file_name,
    }),
  });

  if (!response.ok) {
    const errorData = await response.json().catch(() => ({ detail: "Export failed" }));
    throw new Error(extractErrorDetail(errorData, `Export failed (${response.status})`));
  }

  const blob = await response.blob();
  const contentDisposition = response.headers.get("Content-Disposition");
  let downloadName = "Updated_Policy.docx";
  if (contentDisposition) {
    downloadName = filenameFromDisposition(contentDisposition, downloadName);
  }

  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = downloadName;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

/**
 * Export ONLY the gap analysis as a clean .docx report.
 */
export async function exportGapAnalysis(
  pkg: ComplianceActionPackage,
): Promise<void> {
  if (!pkg.gap_analysis) {
    throw new Error("No gap analysis available to download");
  }
  const response = await fetch(`${API_BASE}/api/export`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...authHeaders() },
    body: JSON.stringify({
      result: pkg.gap_analysis,
      file_name: pkg.source_file_name,
      export_format: "docx",
      kb_sources_used: pkg.kb_sources_used,
      live_research_used: pkg.live_research_used,
      verification_overall: pkg.verification_overall,
      state_coverage: pkg.state_coverage ?? null,
    }),
  });

  if (!response.ok) {
    const errorData = await response.json().catch(() => ({ detail: "Export failed" }));
    throw new Error(extractErrorDetail(errorData, `Export failed (${response.status})`));
  }

  const blob = await response.blob();
  const contentDisposition = response.headers.get("Content-Disposition");
  let downloadName = "Gap_Analysis.docx";
  if (contentDisposition) {
    downloadName = filenameFromDisposition(contentDisposition, downloadName);
  }

  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = downloadName;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

// ── Source Attribution Helpers ──

export function getSourceTypeLabel(type: SourceType): string {
  const labels: Record<SourceType, string> = {
    verified_source: "✅ Verified Source",
    retrieved_source: "📄 Retrieved Source",
    live_research: "🌐 Live Research",
    model_knowledge: "⚠️ Model Inference",
  };
  return labels[type] || type;
}

export function getSourceTypeColor(type: SourceType): string {
  const colors: Record<SourceType, string> = {
    verified_source: "hsl(160 60% 36%)",
    retrieved_source: "hsl(200 60% 44%)",
    live_research: "hsl(270 60% 50%)",
    model_knowledge: "hsl(38 85% 44%)",
  };
  return colors[type] || "hsl(200 60% 44%)";
}

export function getSourceTypeBg(type: SourceType): string {
  const bgs: Record<SourceType, string> = {
    verified_source: "hsl(160 60% 42% / 0.1)",
    retrieved_source: "hsl(200 60% 50% / 0.1)",
    live_research: "hsl(270 60% 50% / 0.1)",
    model_knowledge: "hsl(38 85% 52% / 0.1)",
  };
  return bgs[type] || "hsl(200 60% 50% / 0.1)";
}

export function getVerificationIcon(status: VerificationStatus): string {
  const icons: Record<VerificationStatus, string> = {
    verified: "✅",
    partially_verified: "🔶",
    unverified: "⚠️",
    contradicted: "❌",
    cannot_determine: "❔",
  };
  return icons[status] || "⚠️";
}

export interface DraftedPolicy {
  policy_title: string;
  effective_date?: string;
  version: string;
  scope?: string;
  regulations_applied: string[];
  sections: Array<{ title: string; content: string }>;
  full_text: string;
  drafting_notes?: string;
  kb_sources_used?: string[] | null;
  kb_source_urls?: Record<string, string> | null;
  source_snippets?: SourceSnippet[] | null;
  live_research_used?: boolean;
  // False when no reference source was retrieved for this draft (shown as a banner).
  grounded?: boolean;
  verification_overall?: string | null;
  unverified_claim_count?: number | null;
  // Placeholders the draft uses instead of invented facts, and what each needs decided.
  decisions_required?: string[];
  // Regulatory obligations for this topic the draft does not appear to cover.
  missing_obligations?: string[];
  state_coverage?: StateCoverage | null;
}

export interface SourceSnippet {
  citation?: string | null;
  source_name: string;
  url?: string | null;
  text: string;
}

/**
 * Export a drafted policy as a .docx file download.
 */
export async function exportDraftPolicy(policy: DraftedPolicy): Promise<void> {
  const response = await fetch(`${API_BASE}/api/export-draft`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...authHeaders() },
    body: JSON.stringify({ policy }),
  });

  if (!response.ok) {
    const errorData = await response.json().catch(() => ({ detail: "Export failed" }));
    throw new Error(extractErrorDetail(errorData, `Export failed (${response.status})`));
  }

  const blob = await response.blob();
  const contentDisposition = response.headers.get("Content-Disposition");
  let downloadName = "Drafted_Policy.docx";
  if (contentDisposition) {
    downloadName = filenameFromDisposition(contentDisposition, downloadName);
  }

  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = downloadName;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

/**
 * Start a draft as a background job on the server. Returns a job_id
 * immediately; the draft keeps running server-side even if the client tabs
 * away, backgrounds the app, or loses connection.
 */
export async function startDraftJob(
  policyDescription: string,
  industry?: string,
  jurisdiction?: string,
): Promise<string> {
  const response = await fetch(`${API_BASE}/api/draft-policy/start`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...authHeaders() },
    body: JSON.stringify({
      policy_description: policyDescription,
      industry: industry || "healthcare",
      jurisdiction: jurisdiction || undefined,
    }),
  });
  if (!response.ok) {
    const errorData = await response.json().catch(() => ({ detail: startFailureMessage(response.status, "draft") }));
    throw new Error(extractErrorDetail(errorData, `Job start failed (${response.status})`));
  }
  const data = await response.json();
  if (!data.job_id) throw new Error("Server did not return a job id");
  return data.job_id as string;
}

export type DraftJobStatusResponse = {
  job_id: string;
  status: "running" | "complete" | "error";
  partial_text: string;
  policy: DraftedPolicy | null;
  error: string | null;
  version: number;
};

/**
 * One-shot snapshot of a draft job. Returns null on 404 (expired/missing).
 */
export async function getDraftJobStatus(jobId: string): Promise<DraftJobStatusResponse | null> {
  const response = await fetch(`${API_BASE}/api/draft-policy/status/${encodeURIComponent(jobId)}`, { headers: authHeaders() });
  if (response.status === 404) return null;
  if (!response.ok) throw new Error(`Status check failed (${response.status})`);
  return (await response.json()) as DraftJobStatusResponse;
}

/**
 * Cancel an in-flight draft job. Best-effort -- the UI resets locally
 * regardless of whether this call succeeds.
 */
export async function cancelDraftJob(jobId: string): Promise<void> {
  try {
    await fetch(`${API_BASE}/api/draft-policy/cancel/${encodeURIComponent(jobId)}`, { method: "POST", headers: authHeaders() });
  } catch {
    // best-effort
  }
}

/**
 * Subscribe to live SSE updates for an in-flight draft job. Calls onDelta
 * with each newly-arrived slice of text, then resolves with the final
 * DraftedPolicy on completion. On disconnect, calling this again with the
 * same jobId reconnects — the job keeps running server-side regardless.
 */
export async function streamDraftJob(
  jobId: string,
  onDelta: (fullTextSoFar: string) => void,
): Promise<DraftedPolicy> {
  const response = await fetch(`${API_BASE}/api/draft-policy/stream/${encodeURIComponent(jobId)}`, { headers: authHeaders() });
  if (response.status === 404) throw new Error("Job not found or expired");
  if (!response.ok) throw new Error(`Stream failed (${response.status})`);

  const reader = response.body!.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let finalPolicy: DraftedPolicy | null = null;
  let finalError: string | null = null;
  let terminal = false;

  while (!terminal) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const parts = buffer.split("\n\n");
    buffer = parts.pop() ?? "";
    for (const part of parts) {
      const line = part.trim();
      if (!line.startsWith("data: ")) continue;
      try {
        const data = JSON.parse(line.slice(6));
        if (typeof data.partial_text === "string") onDelta(data.partial_text);
        if (data.status === "complete") {
          finalPolicy = data.policy as DraftedPolicy;
          terminal = true;
        }
        if (data.status === "error") {
          finalError = data.error || "Draft failed";
          terminal = true;
        }
      } catch (e) {
        if (e instanceof Error && e.message !== "Unexpected end of JSON input") throw e;
      }
    }
  }

  if (finalError) throw new Error(finalError);
  if (!finalPolicy) throw new Error("No response received from server");
  return finalPolicy;
}

/**
 * Start an action-package job on the server. Returns a job_id immediately;
 * the actual analysis runs in the background on the server, so it survives
 * tab-switches, navigation, and brief network drops.
 */
export async function startActionPackageJob(
  text: string,
  fileName?: string,
  industry?: string,
  jurisdiction?: string,
  enableLiveResearch: boolean = true,
): Promise<string> {
  const response = await fetch(`${API_BASE}/api/action-package/start`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...authHeaders() },
    body: JSON.stringify({
      text,
      file_name: fileName,
      industry: industry || "healthcare",
      jurisdiction: jurisdiction || undefined,
      enable_live_research: enableLiveResearch,
    }),
  });
  if (!response.ok) {
    const errorData = await response.json().catch(() => ({ detail: startFailureMessage(response.status, "analysis") }));
    throw new Error(extractErrorDetail(errorData, `Job start failed (${response.status})`));
  }
  const data = await response.json();
  if (!data.job_id) throw new Error("Server did not return a job id");
  return data.job_id as string;
}

export type JobStatusResponse = {
  job_id: string;
  status: "running" | "complete" | "error";
  package: ComplianceActionPackage | null;
  error: string | null;
  version: number;
};

/**
 * Cancel an in-flight action-package job. Best-effort -- the UI resets
 * locally regardless of whether this call succeeds.
 */
export async function cancelActionPackageJob(jobId: string): Promise<void> {
  try {
    await fetch(`${API_BASE}/api/action-package/cancel/${encodeURIComponent(jobId)}`, { method: "POST", headers: authHeaders() });
  } catch {
    // best-effort
  }
}

/**
 * One-shot snapshot of an action-package job. Returns null on 404 (expired/missing).
 */
export async function getActionPackageJobStatus(jobId: string): Promise<JobStatusResponse | null> {
  const response = await fetch(`${API_BASE}/api/action-package/status/${encodeURIComponent(jobId)}`, { headers: authHeaders() });
  if (response.status === 404) return null;
  if (!response.ok) throw new Error(`Status check failed (${response.status})`);
  return (await response.json()) as JobStatusResponse;
}

/**
 * Subscribe to live SSE updates for an in-flight job. Calls onUpdate each time
 * the server pushes a new package snapshot. Resolves with the final package on
 * completion; rejects if the server reports an error or the job is missing.
 *
 * On stream disconnect (network blip, server reload), the caller can simply
 * call this again with the same jobId to reconnect — the job keeps running
 * server-side regardless.
 */
export async function streamActionPackageJob(
  jobId: string,
  onUpdate: (pkg: ComplianceActionPackage) => void,
): Promise<ComplianceActionPackage> {
  const response = await fetch(`${API_BASE}/api/action-package/stream/${encodeURIComponent(jobId)}`, { headers: authHeaders() });
  if (response.status === 404) throw new Error("Job not found or expired");
  if (!response.ok) throw new Error(`Stream failed (${response.status})`);

  const reader = response.body!.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let lastPkg: ComplianceActionPackage | null = null;
  let finalError: string | null = null;
  let terminal = false;

  while (!terminal) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const parts = buffer.split("\n\n");
    buffer = parts.pop() ?? "";
    for (const part of parts) {
      const line = part.trim();
      if (!line.startsWith("data: ")) continue;
      try {
        const data = JSON.parse(line.slice(6));
        if (data.package) {
          lastPkg = data.package as ComplianceActionPackage;
          onUpdate(lastPkg);
        }
        if (data.status === "complete") terminal = true;
        if (data.status === "error") {
          finalError = data.error || "Generation failed";
          terminal = true;
        }
      } catch (e) {
        if (e instanceof Error && e.message !== "Unexpected end of JSON input") throw e;
      }
    }
  }

  if (finalError) throw new Error(finalError);
  if (!lastPkg) throw new Error("No response received from server");
  return lastPkg;
}

/**
 * Health check — verify backend is running.
 */
export async function healthCheck(): Promise<boolean> {
  try {
    const response = await fetch(`${API_BASE}/api/health`);
    return response.ok;
  } catch {
    return false;
  }
}

/**
 * Send a chat message to the compliance AI assistant.
 * Works for both post-analysis (mode="analysis") and post-draft (mode="draft") contexts.
 */
export async function sendChatMessage(
  message: string,
  mode: "analysis" | "draft",
  industry?: string,
  jurisdiction?: string,
  contextSummary?: string,
  history?: ChatMessage[],
): Promise<ChatResponse> {
  const response = await fetch(`${API_BASE}/api/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...authHeaders() },
    body: JSON.stringify({
      message,
      mode,
      industry: industry || "healthcare",
      jurisdiction: jurisdiction || undefined,
      context_summary: contextSummary || undefined,
      conversation_history: history || [],
    }),
  });

  if (!response.ok) {
    const errorData = await response.json().catch(() => ({ detail: "Chat failed" }));
    throw new Error(extractErrorDetail(errorData, `Chat failed (${response.status})`));
  }

  return response.json();
}

