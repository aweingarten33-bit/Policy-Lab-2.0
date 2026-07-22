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
          The Policy Lab reviews and drafts compliance policies for a specific industry —
          right now Hospitals, Home Health, and general organizational policies (HR, whistleblower,
          remote work, and similar). Every run — analysis, draft, a follow-up chat question — is
          checked against a curated federal regulatory database <span className="font-bold">and</span> a
          live search of real government sources, every single time. It isn't answering from memory
          and hoping the citation is right.
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
              regulatory text it's grounded in, not just a source name. From there, <span className="font-bold">Fix All Gaps</span> rewrites
              the whole policy to resolve every finding. Toggle <span className="font-bold">Redline View</span> to
              see exactly what changed against your original, tracked-changes style, and download either
              the findings report or the corrected policy as an editable Word file.
            </p>
          </div>
          <div className="rounded-2xl p-5 neu-raised">
            <p className="text-sm font-bold text-foreground mb-1">Draft — describe what you need, get a full policy</p>
            <p className="text-sm leading-relaxed text-foreground/80">
              No existing document required. Describe the policy in plain English — the more specific,
              the better the result — and get a complete, ready-to-adopt policy written from scratch,
              every section, regulations cited inline where they genuinely apply and clickable to the
              actual retrieved source text. Download it as an editable Word file (.docx), ready to
              review, revise, and adopt.
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
            Health Conditions of Participation, Other/General is best for employment and HR-type
            policies (whistleblower, remote work, code of conduct) rather than a specific regulated
            sector.
          </p>
          <p className="text-sm leading-relaxed text-foreground/85">
            <span className="font-bold">State</span> is optional. Leave it blank and only federal
            regulations get checked. Pick one and it also checks that state's specific requirements —
            health privacy law, licensure, breach notification — verified with a live search of that
            state's own government sources, not just general knowledge.
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
            edit the policy for you — for that, use Fix All Gaps (on an analysis) or regenerate the
            draft.
          </p>
        </section>

        <section className="space-y-3 mb-10">
          <h2 className="font-mono text-[11px] font-bold tracking-[0.18em] uppercase text-primary">
            What It Won't Do
          </h2>
          <p className="text-sm leading-relaxed text-foreground/85">
            It won't fabricate an analysis for something that isn't a policy — paste in something
            unrelated and it will tell you so instead of inventing findings. It won't treat AI output
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
