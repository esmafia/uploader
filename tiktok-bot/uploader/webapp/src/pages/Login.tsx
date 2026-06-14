import { useEffect, useRef, useState } from "react";
import { useForm } from "react-hook-form";
import { z } from "zod";
import { zodResolver } from "@hookform/resolvers/zod";
import { Login } from "../api/client";

const schema = z.object({
  username: z
    .string()
    .min(1, "Required")
    .max(128)
    .regex(/^[A-Za-z0-9_.\-]+$/, "Letters, digits, . _ - only"),
});
type FormValues = z.infer<typeof schema>;

type Phase = "idle" | "starting" | "active" | "completing" | "completed" | "failed";

export default function LoginPage() {
  const form = useForm<FormValues>({ resolver: zodResolver(schema) });
  const [phase, setPhase] = useState<Phase>("idle");
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [vncUrl, setVncUrl] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const esRef = useRef<EventSource | null>(null);

  useEffect(() => () => esRef.current?.close(), []);

  const onSubmit = async ({ username }: FormValues) => {
    setError(null);
    setPhase("starting");
    try {
      const r = await Login.start(username);
      setSessionId(r.session_id);
      setVncUrl(r.vnc_url);
      // Open SSE for status transitions
      const es = new EventSource(Login.eventStreamUrl(r.session_id));
      esRef.current = es;
      es.addEventListener("status", (e: MessageEvent) => {
        setPhase(e.data as Phase);
        if (["completed", "failed", "expired"].includes(e.data)) es.close();
      });
      es.addEventListener("error", () => {
        setError("Event stream dropped — refresh to retry");
        es.close();
      });
    } catch (e: any) {
      setPhase("failed");
      setError(e?.response?.data?.detail ?? e.message);
    }
  };

  const cancel = async () => {
    if (sessionId) {
      await Login.cancel(sessionId);
      esRef.current?.close();
    }
    setPhase("idle");
    setSessionId(null);
    setVncUrl(null);
  };

  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-2xl font-semibold">Browser login</h2>
        <p className="text-sm text-slate-500 mt-1">
          Log into TikTok inside a virtual browser. When your session cookie is
          captured, you'll be redirected automatically.
        </p>
      </div>

      {phase === "idle" && (
        <form onSubmit={form.handleSubmit(onSubmit)} className="card space-y-4 max-w-md">
          <div>
            <label className="label">Account name</label>
            <input className="input" placeholder="my-account" {...form.register("username")} />
            {form.formState.errors.username && (
              <p className="text-xs text-red-600 mt-1">{form.formState.errors.username.message}</p>
            )}
          </div>
          <button className="btn-primary">Open virtual browser</button>
        </form>
      )}

      {phase !== "idle" && (
        <div className="card space-y-4">
          <div className="flex items-center justify-between">
            <div>
              <span className="chip bg-brand-50 text-brand-700">session {sessionId?.slice(0, 8)}</span>
              <span className="ml-2 text-sm text-slate-600">Status: <b>{phase}</b></span>
              {error && <p className="text-xs text-red-600 mt-1">{error}</p>}
            </div>
            <button className="btn-secondary" onClick={cancel}>Cancel</button>
          </div>
          {vncUrl && phase !== "completed" && (
            <iframe
              title="Virtual browser"
              src={vncUrl}
              className="w-full border rounded-md"
              style={{ height: "600px" }}
            />
          )}
          {phase === "completed" && (
            <p className="text-sm text-emerald-700">
              Session captured. The account is now in your Accounts list.
            </p>
          )}
        </div>
      )}
    </div>
  );
}
