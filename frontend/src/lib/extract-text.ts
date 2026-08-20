/**
 * Client-side text extraction for file uploads.
 * Used for preview/display before sending to backend.
 * The backend also extracts text server-side for analysis.
 */

// Bundled, not fetched from a CDN.
//
// This used to inject a <script> tag pointing at cdnjs.cloudflare.com. Adding
// a Content-Security-Policy of "script-src 'self'" then blocked that script
// outright, so PDF.js never loaded and every PDF upload failed with "Failed to
// load PDF.js" -- a security header silently removing a core feature.
//
// Loosening the CSP to allow the CDN would fix the symptom and reintroduce the
// real problem: a third-party host able to run arbitrary script inside a tool
// that handles compliance documents. Bundling the library removes both the
// external dependency and the exception.
// Imported lazily: the library and its worker are ~2MB, and most sessions
// never upload a PDF. Loading it up front delayed first paint for everyone to
// serve a minority of visits.
async function loadPdfJs() {
  const [pdfjsLib, worker] = await Promise.all([
    import("pdfjs-dist"),
    // Vite resolves this to a hashed asset on our own origin, so it satisfies
    // both `script-src 'self'` and `worker-src 'self'`.
    import("pdfjs-dist/build/pdf.worker.min.mjs?url"),
  ]);
  pdfjsLib.GlobalWorkerOptions.workerSrc = worker.default;
  return pdfjsLib;
}

async function extractPdfText(file: File): Promise<string> {
  const pdfjsLib = await loadPdfJs();
  const arrayBuffer = await file.arrayBuffer();
  const pdf = await pdfjsLib.getDocument({ data: arrayBuffer }).promise;
  const pages: string[] = [];
  for (let i = 1; i <= pdf.numPages; i++) {
    const page = await pdf.getPage(i);
    const content = await page.getTextContent();
    pages.push(content.items.map((item: any) => item.str).join(" "));
  }
  return pages.join("\n\n");
}

async function extractDocxText(file: File): Promise<string> {
  const mammoth = await import("mammoth");
  const arrayBuffer = await file.arrayBuffer();
  const { value } = await mammoth.extractRawText({ arrayBuffer });
  return value;
}

function stripRtf(rtf: string): string {
  return rtf
    .replace(/\{\\[^{}]*\}/g, "")
    .replace(/\\[a-z]+\d* ?/gi, "")
    .replace(/[{}\\]/g, "")
    .trim();
}

export async function extractText(file: File): Promise<string> {
  const ext = file.name.split(".").pop()?.toLowerCase() || "";
  if (ext === "pdf") return extractPdfText(file);
  if (ext === "docx" || ext === "doc") return extractDocxText(file);
  if (ext === "rtf") return stripRtf(await file.text());
  return file.text();
}
