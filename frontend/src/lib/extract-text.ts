/**
 * Client-side text extraction for file uploads.
 *
 * The browser applies the same general input bounds as the backend so a file
 * cannot expand into an unexpectedly large string before it is submitted for
 * analysis. The backend remains authoritative and re-validates server-side.
 */

const MAX_EXTRACTED_CHARS = 100_000;
const MAX_PDF_PAGES = 250;

function ensureTextLimit(text: string): string {
  if (text.length > MAX_EXTRACTED_CHARS) {
    throw new Error(
      `This document expands to more than ${MAX_EXTRACTED_CHARS.toLocaleString()} characters. ` +
        "Please upload a shorter policy or paste the relevant section.",
    );
  }
  return text;
}

// Bundled, not fetched from a CDN. Imported lazily because PDF.js and its
// worker are large and most sessions never upload a PDF.
async function loadPdfJs() {
  const [pdfjsLib, worker] = await Promise.all([
    import("pdfjs-dist"),
    import("pdfjs-dist/build/pdf.worker.min.mjs?url"),
  ]);
  pdfjsLib.GlobalWorkerOptions.workerSrc = worker.default;
  return pdfjsLib;
}

async function extractPdfText(file: File): Promise<string> {
  let pdfjsLib;
  try {
    pdfjsLib = await loadPdfJs();
  } catch {
    throw new Error(
      "The PDF reader could not load in this browser. Try a hard refresh " +
        "(Ctrl+Shift+R, or Cmd+Shift+R on Mac), or paste the policy text instead.",
    );
  }

  const arrayBuffer = await file.arrayBuffer();
  const pdf = await pdfjsLib.getDocument({ data: arrayBuffer }).promise;
  if (pdf.numPages > MAX_PDF_PAGES) {
    throw new Error(
      `This PDF has ${pdf.numPages.toLocaleString()} pages; the maximum is ${MAX_PDF_PAGES}.`,
    );
  }

  const pages: string[] = [];
  let totalChars = 0;
  for (let i = 1; i <= pdf.numPages; i++) {
    const page = await pdf.getPage(i);
    const content = await page.getTextContent();
    const text = content.items.map((item: any) => item.str).join(" ");
    totalChars += text.length + (pages.length ? 2 : 0);
    if (totalChars > MAX_EXTRACTED_CHARS) {
      throw new Error(
        `This PDF expands to more than ${MAX_EXTRACTED_CHARS.toLocaleString()} characters. ` +
          "Please upload a shorter policy or paste the relevant section.",
      );
    }
    pages.push(text);
  }
  return pages.join("\n\n");
}

async function extractDocxText(file: File): Promise<string> {
  const mammoth = await import("mammoth");
  const arrayBuffer = await file.arrayBuffer();
  const { value } = await mammoth.extractRawText({ arrayBuffer });
  return ensureTextLimit(value);
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
  if (ext === "docx") return extractDocxText(file);
  if (ext === "doc") {
    throw new Error(
      "Legacy .doc files are not supported. Open the file in Word and save it as .docx, then upload it again.",
    );
  }
  if (ext === "rtf") return ensureTextLimit(stripRtf(await file.text()));
  return ensureTextLimit(await file.text());
}
