export default function Guide() {
  return (
    <div className="min-h-screen bg-background text-foreground">
      <header className="border-b border-foreground/8 px-4 py-4">
        <div className="max-w-3xl mx-auto flex items-center justify-between">
          <span className="nyt-masthead text-lg text-foreground leading-[1.25] py-0.5">
            The Policy Lab
          </span>
        </div>
      </header>

      <main className="max-w-3xl mx-auto px-4 py-10 sm:py-14">
        <div className="border-t-2 border-primary pt-3 mb-4 flex items-center gap-3">
          <span className="font-mono text-[10px] font-bold tracking-[0.2em] uppercase text-primary">
            About &amp; How To Use
          </span>
          <div className="flex-1 h-px bg-gradient-to-r from-primary/25 to-transparent" />
        </div>

        <h1 className="font-serif-display text-3xl sm:text-4xl font-black mb-3 leading-tight">
          What this actually is.
        </h1>
        <p className="text-base leading-relaxed text-foreground/85 mb-10">
          The Policy Lab reviews and drafts compliance policies for three industries: Hospitals,
          Home Health, and Pharmacy. Analyses and drafts are grounded in a stored database of federal
          regulation text. A live search of government websites runs only when that database doesn't
          cover a request — for example when a state is selected, or the stored sources are thin or
          out of date — and every result says whether a search ran. It isn't answering from memory and
          hoping the citation is right, but it also can't check what it doesn't have: the limits of each
          result are listed at the top of it.
        </p>

        <section className="space-y-3 mb-10">
          <h2 className="font-mono text-[11px] font-bold tracking-[0.18em] uppercase text-primary">
            The Two Things It Does
          </h2>
          <div className="rounded-2xl p-5 neu-raised mb-3">
            <p className="text-sm font-bold text-foreground mb-1">Analyze — upload a policy, find the gaps</p>
            <p className="text-sm leading-relaxed text-foreground/80">
              Upload or paste an existing policy. You get back a gap analysis: what's missing, what's
              vague, what's a real regulatory exposure versus an organizational best practice, each
              finding cited to a specific regulation — click a citation to see the actual retrieved
              regulatory text it's grounded in, not just a source name. Findings are about the document:
              what it says or leaves out, plus questions about practice that only your team can answer. No
              records are inspected. From there, <span className="font-bold">Draft revisions</span> writes a
              proposed revision aimed at the findings, and <span className="font-bold">Re-check</span> runs a fresh
              analysis on it — that re-check, not the revision itself, is what tells you whether gaps remain.
              Toggle <span className="font-bold">Redline View</span> to see exactly what changed against your
              original, and download the findings report or the proposed revision as an editable Word file.
            </p>
          </div>
          <div className="rounded-2xl p-5 neu-raised">
            <p className="text-sm font-bold text-foreground mb-1">Draft — describe what you need, get a full policy</p>
            <p className="text-sm leading-relaxed text-foreground/80">
              No existing document required. Describe the policy in plain English — the more specific,
              the better the result — and get a complete draft written from scratch, every section,
              regulations cited inline where they genuinely apply and clickable to the actual retrieved
              source text. Anything about your organization you didn't say — who owns a task, an internal
              deadline, the effective date — is left as a bracketed placeholder, with a list of the
              decisions you need to make. Download it as an editable Word file (.docx) to finish.
            </p>
          </div>
        </section>

        <section className="space-y-3 mb-10">
          <h2 className="font-mono text-[11px] font-bold tracking-[0.18em] uppercase text-primary">
            Setting It Up Before You Run It
          </h2>
          <p className="text-sm leading-relaxed text-foreground/85">
            <span className="font-bold">Industry</span> determines which regulatory framework gets
            applied — Hospitals maps to HIPAA and CMS hospital rules, Home Health maps to the Home
            Health Conditions of Participation, and Pharmacy maps to DEA, FDA and Medicare Part D rules.
            There is no separate HR or general industry. Employment-type policies (whistleblower,
            remote work, code of conduct) can be run under whichever industry fits your organization,
            but check that the employment regulations they depend on appear in the result's sources.
          </p>
          <p className="text-sm leading-relaxed text-foreground/85">
            <span className="font-bold">State</span> is optional. Leave it blank and only federal
            regulations are used. The tool does not store state statute or regulation text. Pick a state
            and it will search that state's government websites when the stored sources have nothing for
            it; the result lists exactly which state sources were consulted, or says plainly that none
            were. Search results are web pages, not codified law, so treat any state coverage as
            unverified and check state requirements separately.
          </p>
        </section>

        <section className="space-y-3 mb-10">
          <h2 className="font-mono text-[11px] font-bold tracking-[0.18em] uppercase text-primary">
            Ask AI
          </h2>
          <p className="text-sm leading-relaxed text-foreground/85">
            Once you have results, use the chat to ask follow-up questions — which gap to prioritize,
            what a specific regulation actually requires, what an auditor would check. It's scoped to
            this policy and this tool; it won't answer questions unrelated to your results. It doesn't
            edit the policy for you — for that, use Draft revisions (on an analysis) or regenerate the
            draft.
          </p>
        </section>

        <section className="space-y-3 mb-10">
          <h2 className="font-mono text-[11px] font-bold tracking-[0.18em] uppercase text-primary">
            What It Won't Do
          </h2>
          <p className="text-sm leading-relaxed text-foreground/85">
            It won't fabricate an analysis for something that isn't a policy — paste in something
            unrelated and it will tell you so instead of inventing findings. It won't determine whether
            your organization complies: it reads the document, not your records. It won't invent facts
            about your organization in a draft. It won't treat AI output
            as final: every result is a starting point for review, not a substitute for a compliance
            attorney signing off before anything gets adopted or submitted. See{" "}
            <a href="/legal" target="_blank" rel="noopener noreferrer" className="text-primary hover:underline">
              Legal &amp; Disclaimers
            </a>{" "}
            for the full picture on that.
          </p>
        </section>
      </main>

      <footer className="border-t border-foreground/8 px-4 py-8 text-center">
        <p className="text-[10px] text-muted-foreground/35 font-mono tracking-wider">
          Built by Andrew Weingarten
        </p>
      </footer>
    </div>
  );
}
