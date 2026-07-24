import { useEffect, useState, type FormEvent, type ReactNode } from "react";
import { getStoredPassword, setStoredPassword, clearStoredPassword, verifyPassword } from "@/lib/api";
import { Loader2 } from "lucide-react";

/**
 * Blocks rendering of the app until the correct password is entered.
 * Backend enforces this for real (api_key_middleware checks the same
 * password on every request) -- this screen is just the front door so
 * someone can't even see the app without it, not a substitute for that
 * server-side check.
 */
export default function PasswordGate({ children }: { children: ReactNode }) {
  const [status, setStatus] = useState<"checking" | "locked" | "unlocked">("checking");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    const stored = getStoredPassword();
    if (!stored) {
      setStatus("locked");
      return;
    }
    verifyPassword(stored).then((ok) => {
      if (ok) {
        setStatus("unlocked");
      } else {
        clearStoredPassword();
        setStatus("locked");
      }
    });
  }, []);

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    if (!password.trim()) return;
    setSubmitting(true);
    setError("");
    const ok = await verifyPassword(password);
    setSubmitting(false);
    if (ok) {
      setStoredPassword(password);
      setStatus("unlocked");
    } else {
      setError("Incorrect password.");
    }
  };

  if (status === "checking") {
    return (
      <div className="min-h-screen flex items-center justify-center bg-background">
        <Loader2 className="w-5 h-5 animate-spin text-muted-foreground" />
      </div>
    );
  }

  if (status === "unlocked") {
    return <>{children}</>;
  }

  return (
    <div className="min-h-screen flex items-center justify-center bg-background px-4">
      <div className="w-full max-w-sm">
        <div className="text-center mb-8">
          <span className="nyt-masthead text-[26px] text-foreground whitespace-nowrap overflow-visible leading-[1.3] py-0.5 inline-block">
            The Policy Lab
          </span>
        </div>
        <form onSubmit={handleSubmit} className="rounded-2xl neu-raised p-6 space-y-4">
          <p className="nyt-eyebrow text-center">This tool is password-protected</p>
          <div className="rounded-xl neu-inset px-4 py-3">
            <input
              type="password"
              autoFocus
              value={password}
              onChange={(e) => { setPassword(e.target.value); setError(""); }}
              placeholder="Enter password"
              className="w-full bg-transparent text-[14px] text-foreground placeholder:text-muted-foreground/50 focus:outline-none"
            />
          </div>
          {error && (
            <p className="text-[11px] text-destructive text-center">{error}</p>
          )}
          <button
            type="submit"
            disabled={submitting || !password.trim()}
            className="w-full font-mono text-[10px] font-bold tracking-wider px-4 py-3 rounded-xl bg-primary text-primary-foreground neu-btn active:neu-pressed touch-manipulation disabled:opacity-60 inline-flex items-center justify-center gap-1.5"
          >
            {submitting ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : null}
            {submitting ? "CHECKING..." : "ENTER"}
          </button>
        </form>
      </div>
    </div>
  );
}
