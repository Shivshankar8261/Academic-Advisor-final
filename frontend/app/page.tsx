"use client";

import Image from "next/image";
import { memo, useCallback, useEffect, useRef, useState } from "react";
import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";

const API = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

type Strategy = "baseline" | "structured" | "rag" | "rag_structured" | "agentic";


type Source = { source_file: string; section: string };

type AdvisorReply = {
  answer: string;
  confidence: "high" | "medium" | "low";
  grounded: boolean;
  sources: Source[];
  needs_clarification: boolean;
  clarifying_question: string | null;
  insufficient_information: boolean;
  conflict_detected: boolean;
  strategy: Strategy;
  provider: string;
  response_time_s: number;
  retrieval_time_s: number;
  retrieved: { source_file: string; section: string; score: number }[];
  usage: { prompt_tokens: number; completion_tokens: number; total_tokens: number; estimated_cost_usd: number | null };
  agent_trace: { agent: string; summary: string; ms: number; tokens: number }[];
  attachments: { type: string; batch: string }[];
  guard: string | null;
  privacy_notice: string | null;
  human_review: string | null;
};

const SILENT_MIC_RMS = 0.0005; // quieter than any real microphone's noise floor: only a dead input stays below this

type Message =
  | { role: "user"; content: string }
  | { role: "assistant"; reply: AdvisorReply }
  | { role: "error"; content: string };

type StudentSummary = {
  student_id: string;
  batch: string | null;
  current_semester: number | null;
  cgpa: number | null;
  completed_credits: number | null;
  scenario: string;
};

function formatCost(cost: number | null): string {
  if (cost === null) return "free tier";
  return cost < 0.0001 ? "<$0.0001" : `$${cost.toFixed(4)}`;
}

const CONFIDENCE_STYLE: Record<string, { pill: string; dot: string }> = {
  high: { pill: "bg-emerald-50 text-emerald-700 ring-emerald-200", dot: "bg-emerald-500" },
  medium: { pill: "bg-amber-50 text-amber-700 ring-amber-200", dot: "bg-amber-500" },
  low: { pill: "bg-rose-50 text-rose-700 ring-rose-200", dot: "bg-rose-500" },
};

// LLM answers often contain Markdown (bold, lists, tables, headings) — render it instead of showing raw "**".
const MD: Components = {
  p: (props) => <p className="my-2 first:mt-0 last:mb-0" {...props} />,
  h1: (props) => <h3 className="mb-2 mt-4 text-base font-semibold text-brand-900 first:mt-0" {...props} />,
  h2: (props) => <h3 className="mb-2 mt-4 text-base font-semibold text-brand-900 first:mt-0" {...props} />,
  h3: (props) => <h4 className="mb-1.5 mt-3 font-semibold text-brand-900 first:mt-0" {...props} />,
  h4: (props) => <h4 className="mb-1.5 mt-3 font-semibold text-slate-800 first:mt-0" {...props} />,
  strong: (props) => <strong className="font-semibold text-slate-900" {...props} />,
  ul: (props) => <ul className="my-2 list-disc space-y-1 pl-5 marker:text-brand-500" {...props} />,
  ol: (props) => <ol className="my-2 list-decimal space-y-1 pl-5 marker:text-brand-600" {...props} />,
  a: (props) => <a className="text-brand-600 underline underline-offset-2" target="_blank" rel="noreferrer" {...props} />,
  hr: () => <hr className="my-3 border-slate-200" />,
  blockquote: (props) => <blockquote className="my-2 border-l-4 border-brand-100 pl-3 text-slate-600" {...props} />,
  code: (props) => <code className="rounded bg-slate-100 px-1 py-0.5 font-mono text-[13px] text-brand-700" {...props} />,
  table: (props) => (
    <div className="my-3 overflow-x-auto rounded-xl ring-1 ring-slate-200">
      <table className="w-full border-collapse text-left text-sm [&_td:nth-child(5)]:whitespace-nowrap" {...props} />
    </div>
  ),
  thead: (props) => <thead className="bg-brand-50 text-brand-900" {...props} />,
  th: (props) => <th className="px-3 py-2 font-semibold" {...props} />,
  td: (props) => <td className="border-t border-slate-100 px-3 py-2 align-top" {...props} />,
};

// FastAPI returns {detail: string} or {detail: [{msg}]} (validation); fall back to the status text.
async function errorText(res: Response): Promise<string> {
  try {
    const body = await res.json();
    if (typeof body?.detail === "string") return body.detail;
    if (Array.isArray(body?.detail)) return body.detail.map((d: { msg?: string }) => d.msg ?? String(d)).join("; ");
  } catch {
    /* non-JSON error body */
  }
  return `${res.status} ${res.statusText || "request failed"}`;
}

// The advisor mascot: white helmet, navy visor with smiling eyes, red "spark" lines.
function Robot({ className, sparks = true }: { className?: string; sparks?: boolean }) {
  return (
    <svg viewBox="0 0 220 190" className={className} aria-hidden fill="none">
      <ellipse cx="100" cy="176" rx="50" ry="7" fill="#e1122b" opacity="0.12" />
      <line x1="100" y1="34" x2="100" y2="50" stroke="#0f2350" strokeWidth="6" strokeLinecap="round" />
      <circle cx="100" cy="24" r="9" fill="#fff" stroke="#0f2350" strokeWidth="6" />
      <rect x="26" y="90" width="20" height="40" rx="7" fill="#c9d1dd" stroke="#0f2350" strokeWidth="5" />
      <rect x="154" y="90" width="20" height="40" rx="7" fill="#c9d1dd" stroke="#0f2350" strokeWidth="5" />
      <rect x="40" y="50" width="120" height="118" rx="58" fill="#fff" stroke="#0f2350" strokeWidth="6" />
      <path d="M58 140c14 18 70 18 84 0" stroke="#e6eaf1" strokeWidth="8" strokeLinecap="round" />
      <rect x="56" y="84" width="88" height="48" rx="24" fill="#0f2350" />
      <path d="M76 116q9-14 18 0M106 116q9-14 18 0" stroke="#fff" strokeWidth="6" strokeLinecap="round" />
      {sparks && (
        <path d="M170 12l-8 22M196 32l-20 14M202 58l-24 2" stroke="#e1122b" strokeWidth="6" strokeLinecap="round" />
      )}
    </svg>
  );
}

function AdvisorAvatar() {
  return (
    <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-white shadow-md shadow-brand-900/10 ring-1 ring-slate-200">
      <Robot className="h-8 w-8 translate-x-[3px]" sparks={false} />
    </div>
  );
}

// Layered navy / grey / red waves along the bottom edge, as in the brand artwork.
function Waves() {
  return (
    <svg
      viewBox="0 0 1440 200"
      preserveAspectRatio="none"
      className="absolute inset-x-0 bottom-0 h-[16vh] max-h-44 min-h-20 w-full"
    >
      <defs>
        <linearGradient id="wave-navy" x1="0" x2="1" y1="0" y2="0">
          <stop offset="0" stopColor="#12306b" />
          <stop offset="0.55" stopColor="#12306b" stopOpacity="0" />
        </linearGradient>
        <linearGradient id="wave-red" x1="0" x2="1" y1="0" y2="0">
          <stop offset="0.35" stopColor="#e1122b" stopOpacity="0" />
          <stop offset="1" stopColor="#e1122b" />
        </linearGradient>
      </defs>
      <path d="M0 40C320 30 560 150 940 150c220 0 380-60 500-120v170H0Z" fill="#e9eef6" />
      <path d="M0 110c260 0 470 60 760 90H0Z" fill="url(#wave-navy)" />
      <path d="M1440 70c-180 70-420 120-760 130h760Z" fill="url(#wave-red)" />
      <path d="M0 150c300-10 520 30 720 50H0Z" fill="#0f2350" opacity="0.9" />
    </svg>
  );
}

// Tables are on screen, and reading them cell by cell is useless, so only the sentences are spoken.
function speakableSentences(text: string): string[] {
  const plain = text
    .split("\n")
    .filter((l) => !l.trim().startsWith("|") && !/^-{3,}$/.test(l.trim()))
    .join(" ")
    .replace(/[*_`#>]/g, "")
    .replace(/\s+/g, " ")
    .trim();
  return plain.match(/[^.!?]+[.!?]*\s*/g) ?? [plain];
}

// Browsers don't report a voice's gender, so female voices are matched by name, Indian English first.
// The list covers macOS/iOS, Chrome, Windows/Edge and Android voices.
const FEMALE_VOICES = /tara|veena|isha|heera|neerja|samantha|karen|moira|tessa|serena|victoria|fiona|zira|aria|jenny|google uk english female|google us english|female/i;

function femaleVoice(voices: SpeechSynthesisVoice[]): SpeechSynthesisVoice | undefined {
  const female = voices.filter((v) => v.lang.startsWith("en") && FEMALE_VOICES.test(v.name));
  return (
    female.find((v) => v.lang === "en-IN" && v.localService) ??
    female.find((v) => v.lang === "en-IN") ??
    female.find((v) => v.localService) ??
    female[0]
  );
}

// Chrome fills its voice list asynchronously: it is empty until voiceschanged fires, often on the very first click.
function loadVoices(synth: SpeechSynthesis): Promise<SpeechSynthesisVoice[]> {
  const now = synth.getVoices();
  if (now.length) return Promise.resolve(now);
  return new Promise((resolve) => {
    const done = () => {
      synth.removeEventListener("voiceschanged", done);
      clearTimeout(timer);
      resolve(synth.getVoices());
    };
    const timer = setTimeout(done, 1500);
    synth.addEventListener("voiceschanged", done);
  });
}

function ReadAloud({ text }: { text: string }) {
  const [state, setState] = useState<"idle" | "loading" | "playing">("idle");
  const audioRef = useRef<HTMLAudioElement | null>(null);

  // Stop speaking if this message leaves the screen (New chat).
  useEffect(() => {
    window.speechSynthesis?.getVoices(); // starts Chrome loading its list, so the first click is ready
    return () => window.speechSynthesis?.cancel();
  }, []);

  // The cloud voice takes up to ~30 s on a long answer, so it is only the fallback for devices with no female voice.
  async function cloudVoice() {
    setState("loading");
    try {
      const res = await fetch(`${API}/speech`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text }),
      });
      if (!res.ok) throw new Error(await errorText(res));
      const audio = new Audio(URL.createObjectURL(await res.blob()));
      audioRef.current = audio;
      audio.onended = () => setState("idle");
      setState("playing");
      await audio.play();
    } catch {
      setState("idle");
    }
  }

  async function toggle() {
    if (state === "playing") {
      audioRef.current?.pause();
      window.speechSynthesis?.cancel();
      setState("idle");
      return;
    }
    const synth = window.speechSynthesis;
    if (!synth) {
      cloudVoice();
      return;
    }
    setState("loading");
    const voice = femaleVoice(await loadVoices(synth));
    if (!voice) {
      cloudVoice(); // no female voice on this device: the cloud voice (Kore) is female, the system default may not be
      return;
    }
    synth.cancel();
    setState("playing");
    // One utterance per sentence: speech starts at once, and Chrome cuts off single utterances longer than ~15 s.
    const sentences = speakableSentences(text);
    sentences.forEach((sentence, i) => {
      const utterance = new SpeechSynthesisUtterance(sentence);
      utterance.voice = voice;
      if (i === sentences.length - 1) utterance.onend = () => setState("idle");
      utterance.onerror = (e) => {
        if (e.error === "canceled" || e.error === "interrupted") return;
        synth.cancel();
        cloudVoice();
      };
      synth.speak(utterance);
    });
  }

  return (
    <button
      type="button"
      onClick={toggle}
      disabled={state === "loading"}
      className="inline-flex items-center gap-1 rounded-lg px-2 py-1 font-medium text-brand-700 hover:bg-brand-50 disabled:opacity-50"
    >
      {state === "loading" ? "⏳ Preparing audio…" : state === "playing" ? "⏹ Stop" : "🔊 Listen"}
    </button>
  );
}

function CopyForAdvisor({ reply, question }: { reply: AdvisorReply; question: string }) {
  const [copied, setCopied] = useState(false);
  async function copy() {
    const sources = reply.sources.map((s) => `- ${s.source_file}: ${s.section}`).join("\n") || "- none cited";
    await navigator.clipboard.writeText(
      `Question: ${question}\n\nAI advisor's answer (please check):\n${reply.answer}\n\nSources:\n${sources}`,
    );
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  }
  return (
    <button
      type="button"
      onClick={copy}
      className="inline-flex items-center gap-1 rounded-lg px-2 py-1 font-medium text-brand-700 hover:bg-brand-50"
    >
      {copied ? "✓ Copied" : "📋 Copy for my advisor"}
    </button>
  );
}

const AssistantMessage = memo(function AssistantMessage({ reply, question }: { reply: AdvisorReply; question: string }) {
  const conf = CONFIDENCE_STYLE[reply.confidence] ?? CONFIDENCE_STYLE.low;
  const smallTalk = reply.provider === "small-talk"; // greetings: no badges or sources
  return (
    <div className="flex max-w-full gap-3 sm:max-w-[88%]">
      <AdvisorAvatar />
      <div className="min-w-0 flex-1 rounded-3xl rounded-tl-md border border-slate-100 bg-white p-5 shadow-[0_10px_30px_-12px_rgba(15,35,80,0.18)]">
        {reply.insufficient_information && !reply.needs_clarification && (
          <div className="mb-3 flex gap-2 rounded-xl border border-amber-200 bg-gradient-to-r from-amber-50 to-yellow-50 px-3 py-2.5 text-sm font-medium text-amber-900">
            <span>⚠️</span>
            <span>I don&apos;t have enough information in the university documents to answer this reliably.</span>
          </div>
        )}
        {reply.conflict_detected && (
          <div className="mb-3 flex gap-2 rounded-xl border border-orange-200 bg-gradient-to-r from-orange-50 to-rose-50 px-3 py-2.5 text-sm font-medium text-orange-900">
            <span>⚖️</span>
            <span>
              The documents contain conflicting rules on this — please confirm with your Faculty Advisor / Program
              Chair.
            </span>
          </div>
        )}

        {reply.human_review && (
          <div className="mb-3 flex gap-2 rounded-xl border border-violet-200 bg-violet-50 px-3 py-2.5 text-sm font-medium text-violet-900">
            <span>🧑‍🏫</span>
            <span>{reply.human_review}</span>
          </div>
        )}
        {reply.privacy_notice && (
          <div className="mb-3 flex gap-2 rounded-xl border border-sky-200 bg-sky-50 px-3 py-2.5 text-sm font-medium text-sky-900">
            <span>🔒</span>
            <span>{reply.privacy_notice}</span>
          </div>
        )}

        <div className="break-words text-[15px] leading-relaxed text-slate-800">
          <ReactMarkdown remarkPlugins={[remarkGfm]} components={MD}>
            {reply.answer}
          </ReactMarkdown>
        </div>

        {reply.attachments.map((a) => (
          <figure key={a.batch} className="mt-3 overflow-hidden rounded-xl ring-1 ring-slate-200">
            {/* eslint-disable-next-line @next/next/no-img-element -- SVG served by the API, sized by its viewBox */}
            <img
              src={`${API}/infographic/${a.batch}`}
              alt={`Course structure infographic for the ${a.batch} batch: credits by basket and courses by semester`}
              className="w-full"
            />
            <figcaption className="flex items-center justify-between bg-slate-50 px-3 py-1.5 text-xs text-slate-500">
              <span>Built from the semester spread. Not an official University document.</span>
              <a className="font-medium text-brand-700 underline" href={`${API}/infographic/${a.batch}`} target="_blank" rel="noreferrer">
                Open full size
              </a>
            </figcaption>
          </figure>
        ))}

        {reply.needs_clarification && reply.clarifying_question && (
          <div className="mt-4 rounded-xl border border-brand-100 bg-gradient-to-r from-brand-50 to-violet-50 px-4 py-3 text-[15px] text-brand-900">
            <div className="mb-1 text-xs font-semibold uppercase tracking-wide text-brand-600">💬 Question for you</div>
            {reply.clarifying_question}
          </div>
        )}

        {!smallTalk && (
        <>
        <div className="mt-4 flex flex-wrap items-center gap-1.5 text-xs">
          <span className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 font-medium ring-1 ${conf.pill}`}>
            <span className={`h-1.5 w-1.5 rounded-full ${conf.dot}`} />
            {reply.confidence} confidence
          </span>
          <span
            className={`inline-flex items-center gap-1 rounded-full px-2.5 py-1 font-medium ring-1 ${
              reply.grounded ? "bg-sky-50 text-sky-700 ring-sky-200" : "bg-slate-50 text-slate-500 ring-slate-200"
            }`}
          >
            {reply.grounded
              ? "✓ grounded in documents"
              : reply.provider.includes("conversation memory")
                ? "from this conversation"
                : "not grounded"}
          </span>
          <span className="inline-flex items-center gap-1 rounded-full bg-slate-50 px-2.5 py-1 text-slate-600 ring-1 ring-slate-200">
            ⚡ {reply.response_time_s.toFixed(2)}s
            {reply.retrieval_time_s > 0 && (
              <span className="text-slate-400"> · retrieval {(reply.retrieval_time_s * 1000).toFixed(0)} ms</span>
            )}
          </span>
          <span
            title={`${reply.usage.prompt_tokens} input + ${reply.usage.completion_tokens} output tokens`}
            className="inline-flex items-center gap-1 rounded-full bg-slate-50 px-2.5 py-1 text-slate-600 ring-1 ring-slate-200"
          >
            🔢 {reply.usage.total_tokens.toLocaleString()} tokens
            <span className="text-slate-400">
              {" "}
              · {reply.usage.total_tokens ? formatCost(reply.usage.estimated_cost_usd) : "no model used"}
            </span>
          </span>
          <span className="rounded-full bg-slate-50 px-2.5 py-1 font-mono text-[11px] text-slate-500 ring-1 ring-slate-200">
            {reply.strategy} · {reply.provider}
          </span>
        </div>

        <div className="mt-2 flex flex-wrap items-center gap-1 text-sm">
          <ReadAloud text={reply.answer} />
          <CopyForAdvisor reply={reply} question={question} />
        </div>

        {reply.agent_trace.length > 0 && (
          <details className="group mt-2 text-sm">
            <summary className="inline-flex cursor-pointer select-none list-none items-center gap-1.5 rounded-lg px-2 py-1 font-medium text-brand-700 hover:bg-brand-50 [&::-webkit-details-marker]:hidden">
              🤖 How the agents worked ({reply.agent_trace.length} steps)
            </summary>
            <ol className="mt-2 grid gap-1.5">
              {reply.agent_trace.map((t, i) => (
                <li key={i} className="rounded-lg border border-slate-100 bg-slate-50/80 px-3 py-2">
                  <span className="font-medium text-slate-800">
                    {i + 1}. {t.agent}
                  </span>
                  <span className="text-xs text-slate-400">
                    {" "}
                    · {t.ms} ms{t.tokens ? ` · ${t.tokens.toLocaleString()} tokens` : ""}
                  </span>
                  <div className="text-slate-600">{t.summary}</div>
                </li>
              ))}
            </ol>
          </details>
        )}

        <details className="group mt-2 text-sm">
          <summary className="inline-flex cursor-pointer select-none list-none items-center gap-1.5 rounded-lg px-2 py-1 font-medium text-brand-700 hover:bg-brand-50 [&::-webkit-details-marker]:hidden">
            <svg
              viewBox="0 0 20 20"
              className="h-3.5 w-3.5 transition-transform group-open:rotate-90"
              fill="currentColor"
            >
              <path d="M7 5l6 5-6 5V5z" />
            </svg>
            📚 Sources ({reply.sources.length} cited
            {reply.retrieved.length ? `, ${reply.retrieved.length} retrieved` : ""})
          </summary>
          {reply.sources.length === 0 ? (
            <p className="mt-2 pl-2 text-slate-500">No sources cited.</p>
          ) : (
            <ul className="mt-2 grid gap-1.5">
              {reply.sources.map((s, i) => (
                <li
                  key={i}
                  className="flex items-start gap-2 rounded-lg border border-slate-100 bg-slate-50/80 px-3 py-2"
                >
                  <span className="mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-md bg-brand-100 text-[11px] font-semibold text-brand-700">
                    {i + 1}
                  </span>
                  <span>
                    <span className="font-medium text-slate-800">{s.source_file}</span>
                    <span className="text-slate-500"> — {s.section}</span>
                  </span>
                </li>
              ))}
            </ul>
          )}
        </details>
        </>
        )}
      </div>
    </div>
  );
});

function TypingIndicator() {
  return (
    <div className="flex animate-fade-up items-center gap-3 self-start">
      <AdvisorAvatar />
      <div className="flex items-center gap-3 rounded-3xl rounded-tl-md bg-white px-4 py-3 shadow-md ring-1 ring-slate-100">
        <div className="flex gap-1">
          {[0, 150, 300].map((d) => (
            <span
              key={d}
              className="h-2 w-2 animate-typing rounded-full bg-vured-500"
              style={{ animationDelay: `${d}ms` }}
            />
          ))}
        </div>
        <span className="text-sm text-slate-500">Checking the university documents…</span>
      </div>
    </div>
  );
}

function Stat({ label, value }: { label: string; value: string | number | null }) {
  return (
    <div className="rounded-lg bg-white/80 px-3 py-1.5 ring-1 ring-brand-100">
      <div className="text-[10px] font-semibold uppercase tracking-wider text-slate-400">{label}</div>
      <div className="text-sm font-semibold text-brand-900">{value ?? "—"}</div>
    </div>
  );
}

export default function Home() {
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const strategy: Strategy = "rag_structured"; // the final advisor; other strategies remain available through the API
  const [studentId] = useState(""); // no profile picker in the UI: students are anonymous
  const [students, setStudents] = useState<StudentSummary[]>([]);
  const [loading, setLoading] = useState(false);
  const [backendOk, setBackendOk] = useState<boolean | null>(null);
  const [dbSource, setDbSource] = useState<string | null>(null); // "supabase" or "bundled": where answers are read from
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const scrollRef = useRef<HTMLElement>(null);

  const abortRef = useRef<AbortController | null>(null);
  const recorderRef = useRef<MediaRecorder | null>(null);
  const [recording, setRecording] = useState(false);
  const [micLevel, setMicLevel] = useState(0);
  const [mics, setMics] = useState<MediaDeviceInfo[]>([]);
  const [micId, setMicId] = useState("");
  const [micHelp, setMicHelp] = useState(false);
  const [busyNote, setBusyNote] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  async function startRecording(deviceId = micId) {
    setNotice(null);
    setMicHelp(false);
    try {
      window.speechSynthesis?.cancel();
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true,
          ...(deviceId ? { deviceId: { exact: deviceId } } : {}),
        },
      });
      const devices = await navigator.mediaDevices.enumerateDevices(); // labels are only available after permission
      setMics(devices.filter((d) => d.kind === "audioinput" && d.deviceId !== "default" && d.deviceId !== "communications"));

      // Measure the real input level: it shows the student the mic is working, and lets us tell a silent mic from
      // a transcription problem (Whisper turns silence into "Thank you.").
      const audioContext = new AudioContext();
      await audioContext.resume();
      const analyser = audioContext.createAnalyser();
      analyser.fftSize = 1024;
      audioContext.createMediaStreamSource(stream).connect(analyser);
      const samples = new Float32Array(analyser.fftSize);
      let peak = 0;
      const meter = setInterval(() => {
        analyser.getFloatTimeDomainData(samples);
        const rms = Math.sqrt(samples.reduce((sum, x) => sum + x * x, 0) / samples.length);
        peak = Math.max(peak, rms);
        setMicLevel(rms);
      }, 80);

      const recorder = new MediaRecorder(stream);
      const parts: Blob[] = [];
      recorder.ondataavailable = (e) => parts.push(e.data);
      recorder.onstop = async () => {
        clearInterval(meter);
        stream.getTracks().forEach((t) => t.stop());
        audioContext.close();
        setRecording(false);
        setMicLevel(0);
        if (peak < SILENT_MIC_RMS) {
          const name = stream.getAudioTracks()[0]?.label || "the selected microphone";
          setNotice(`No sound reached the browser from "${name}". Choose another microphone below and try again.`);
          setMicHelp(true);
          return;
        }
        setBusyNote("Transcribing your question…");
        try {
          const form = new FormData();
          const type = recorder.mimeType || "audio/webm";
          form.append("file", new Blob(parts, { type }), type.includes("mp4") ? "speech.mp4" : "speech.webm");
          const res = await fetch(`${API}/voice/transcribe`, { method: "POST", body: form });
          if (!res.ok) throw new Error(await errorText(res));
          const { text } = await res.json();
          if (!text) {
            setNotice("I picked up sound but no clear words. Please try again in a quieter spot, or choose another microphone.");
            setMicHelp(true);
          }
          // goes into the box, not straight to the advisor: the student can correct a misheard course name first
          setInput((prev) => [prev, text].filter(Boolean).join(" "));
          inputRef.current?.focus();
        } catch (e) {
          setNotice(e instanceof Error ? e.message : String(e));
        } finally {
          setBusyNote(null);
        }
      };
      recorderRef.current = recorder;
      recorder.start();
      setRecording(true);
      setTimeout(() => {
        if (recorder.state === "recording") recorder.stop();
      }, 60000);
    } catch (e) {
      setRecording(false);
      if (e instanceof DOMException && e.name === "OverconstrainedError") {
        setMicId(""); // the saved microphone was unplugged
        setNotice("That microphone is no longer available. Press the mic button to use the default one.");
      } else {
        setNotice("I couldn't use the microphone. Please allow access in your browser, or type your question.");
      }
    }
  }

  const sessionTokens = messages.reduce((sum, m) => sum + (m.role === "assistant" ? m.reply.usage.total_tokens : 0), 0);

  // Poll /health until the backend is up (it may be started after the frontend), then load the profiles.
  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout>;
    async function check() {
      try {
        const r = await fetch(`${API}/health`);
        if (cancelled) return;
        setBackendOk(r.ok);
        if (r.ok) {
          const health = await r.clone().json().catch(() => null);
          if (!cancelled) setDbSource(health?.database?.source ?? null);
          const list = await fetch(`${API}/students`).then((res) => (res.ok ? res.json() : []));
          if (!cancelled) setStudents(Array.isArray(list) ? list : []);
          return;
        }
      } catch {
        if (cancelled) return;
        setBackendOk(false);
      }
      timer = setTimeout(check, 5000);
    }
    check();
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, []);

  // grow the textarea with its content (Shift+Enter adds lines)
  useEffect(() => {
    const el = inputRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 160)}px`;
  }, [input]);

  useEffect(() => {
    const el = scrollRef.current;
    if (el) el.scrollTo({ top: el.scrollHeight, behavior: "smooth" });
  }, [messages, loading]);

  const last = messages[messages.length - 1];
  const awaitingClarification = last?.role === "assistant" && last.reply.needs_clarification;

  // keep the input focused when the advisor asks a follow-up question
  useEffect(() => {
    if (awaitingClarification) inputRef.current?.focus();
  }, [awaitingClarification]);

  async function send(text: string) {
    const message = text.trim();
    if (!message || loading) return;
    const history = messages.flatMap((m) =>
      m.role === "user"
        ? [{ role: "user", content: m.content }]
        : m.role === "assistant"
          ? [
              {
                role: "assistant",
                content: [m.reply.answer, m.reply.clarifying_question].filter(Boolean).join(" "),
                needs_clarification: m.reply.needs_clarification,
              },
            ]
          : [],
    );
    setMessages((prev) => [...prev, { role: "user", content: message }]);
    setInput("");
    setLoading(true);
    const controller = new AbortController();
    abortRef.current = controller;
    try {
      const res = await fetch(`${API}/chat`, {
        method: "POST",
        signal: controller.signal,
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          message,
          strategy,
          student_id: studentId || null,
          conversation_history: history,
        }),
      });
      if (!res.ok) throw new Error(await errorText(res));
      const reply: AdvisorReply = await res.json();
      setMessages((prev) => [...prev, { role: "assistant", reply }]);
      setBackendOk(true);
    } catch (e) {
      if (controller.signal.aborted) return; // "New chat" was pressed — drop the stale reply
      const network = e instanceof TypeError;
      if (network) setBackendOk(false);
      const content = network
        ? `Can't reach the advisor backend at ${API}. Is the server running?`
        : e instanceof Error
          ? e.message
          : String(e);
      setMessages((prev) => [...prev, { role: "error", content }]);
    } finally {
      if (abortRef.current === controller) {
        abortRef.current = null;
        setLoading(false);
        inputRef.current?.focus();
      }
    }
  }

  const newChat = useCallback(() => {
    abortRef.current?.abort();
    abortRef.current = null;
    setLoading(false);
    setMessages([]);
    setInput("");
    setNotice(null);
    inputRef.current?.focus();
  }, []);

  const selected = students.find((s) => s.student_id === studentId);


  const chatting = messages.length > 0;

  // Phones have room for a short placeholder only; the full one wraps onto three lines.
  const [narrow, setNarrow] = useState(false);
  useEffect(() => {
    const mq = window.matchMedia("(max-width: 639px)");
    const update = () => setNarrow(mq.matches);
    update();
    mq.addEventListener("change", update);
    return () => mq.removeEventListener("change", update);
  }, []);
  // re-measure the textarea once the placeholder changes (the long one wraps and leaves the box too tall)
  useEffect(() => {
    const el = inputRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 160)}px`;
  }, [narrow]);

  // Panels that sit above the search bar (mic meter, notices) plus the bar itself.
  // Rendered in the hero on the landing screen, and docked at the bottom once a conversation starts.
  const composer = (
    <div className="w-full">
      {recording && (
        <div className="mb-2 flex items-center gap-3 rounded-full bg-vured-50 px-4 py-1.5 text-sm text-brand-900">
          <span className="whitespace-nowrap">🎙️ Listening… speak, then press ⏹</span>
          <div
            role="meter"
            aria-label="Microphone level"
            aria-valuemin={0}
            aria-valuemax={100}
            aria-valuenow={Math.round(Math.min(1, micLevel * 12) * 100)}
            className="h-2 flex-1 overflow-hidden rounded-full bg-white"
          >
            <div className="h-full bg-vured-500 transition-[width]" style={{ width: `${Math.min(1, micLevel * 12) * 100}%` }} />
          </div>
        </div>
      )}
      {(notice || busyNote) && (
        <p className={`mb-2 rounded-2xl px-4 py-1.5 text-left text-sm ${notice ? "bg-vured-50 text-vured-600" : "bg-brand-50 text-brand-900"}`}>
          {notice ?? busyNote}
        </p>
      )}
      {micHelp && mics.length > 0 && (
        <label className="mb-2 flex items-center gap-2 rounded-2xl bg-amber-50 px-4 py-1.5 text-sm font-medium text-amber-900">
          🎤 Microphone
          <select
            className="min-w-0 flex-1 rounded-lg border border-amber-200 bg-white px-2 py-1 font-normal"
            value={micId}
            onChange={(e) => {
              setMicId(e.target.value);
              startRecording(e.target.value);
            }}
          >
            <option value="">System default</option>
            {mics.map((m) => (
              <option key={m.deviceId} value={m.deviceId}>
                {m.label || "Microphone"}
              </option>
            ))}
          </select>
        </label>
      )}
      <form
        className={`flex items-end gap-0.5 rounded-[32px] border bg-white py-2 pl-3 pr-2 shadow-[0_12px_40px_-12px_rgba(15,35,80,0.22)] transition focus-within:ring-4 sm:gap-2 sm:pl-6 ${
          awaitingClarification
            ? "border-vured-500/50 ring-vured-50"
            : "border-slate-100 focus-within:border-brand-100 focus-within:ring-brand-50"
        }`}
        onSubmit={(e) => {
          e.preventDefault();
          send(input);
        }}
      >
        <svg viewBox="0 0 24 24" className="mb-3 hidden h-6 w-6 shrink-0 text-slate-500 sm:block" fill="none" stroke="currentColor" strokeWidth={2} aria-hidden>
          <circle cx="10.5" cy="10.5" r="6.5" />
          <path d="m15.5 15.5 5 5" strokeLinecap="round" />
        </svg>
        <textarea
          ref={inputRef}
          rows={1}
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
              e.preventDefault();
              send(input);
            }
          }}
          aria-label="Your question"
          placeholder={awaitingClarification ? "Answer the advisor's question…" : narrow ? "Ask a question..." : "Ask your academic question..."}
          className="max-h-40 min-w-0 flex-1 resize-none bg-transparent px-2 py-3 text-base text-slate-800 outline-none placeholder:text-slate-500 sm:text-lg"
        />
        <button
          type="button"
          title={recording ? "Stop recording" : "Ask by voice. You can check the text before sending."}
          aria-label={recording ? "Stop recording" : "Record a voice question"}
          aria-pressed={recording}
          disabled={!!busyNote}
          onClick={() => (recording ? recorderRef.current?.stop() : startRecording())}
          className={`mb-0.5 flex h-9 w-9 shrink-0 sm:h-11 sm:w-11 items-center justify-center rounded-full transition disabled:opacity-40 ${
            recording ? "animate-pulse bg-vured-50 text-vured-500" : "text-slate-400 hover:bg-brand-50 hover:text-brand-900"
          }`}
        >
          {recording ? (
            <svg viewBox="0 0 24 24" className="h-5 w-5" fill="currentColor" aria-hidden>
              <rect x="6" y="6" width="12" height="12" rx="2" />
            </svg>
          ) : (
            <svg viewBox="0 0 24 24" className="h-5 w-5" fill="none" stroke="currentColor" strokeWidth={2} aria-hidden>
              <rect x="9" y="3" width="6" height="11" rx="3" />
              <path d="M5 11a7 7 0 0 0 14 0M12 18v3" strokeLinecap="round" />
            </svg>
          )}
        </button>
        <button
          type="submit"
          disabled={loading || !input.trim()}
          aria-label="Send"
          className="flex h-12 w-14 shrink-0 items-center justify-center rounded-full bg-vured-500 text-white shadow-md shadow-vured-500/30 transition hover:scale-[1.03] hover:bg-vured-600 disabled:scale-100 disabled:cursor-not-allowed disabled:shadow-none sm:w-[72px]"
        >
          <svg viewBox="0 0 24 24" className="h-6 w-6" fill="currentColor" aria-hidden>
            <path d="M21.4 2.6 2.9 9.9c-.8.3-.8 1.4 0 1.7l6.6 2.6 2.6 6.6c.3.8 1.4.8 1.7 0l7.3-18.5c.2-.5-.3-1-.7-.7Zm-11 10.2-.1-.1 7.9-7.9-6.1 9.4-1.7-1.4Z" />
          </svg>
        </button>
      </form>
    </div>
  );

  return (
    <div className="relative isolate flex h-dvh flex-col overflow-hidden bg-[#fbfbfd] text-slate-900">
      {/* decorative background: soft light and the brand waves, fixed so they never scroll or repaint */}
      <div aria-hidden className="pointer-events-none fixed inset-0 -z-10 overflow-hidden">
        <div className="absolute inset-0 bg-[radial-gradient(ellipse_at_top,#ffffff_0%,#f6f7fb_70%)]" />
        <div className={`transition-opacity duration-500 ${chatting ? "opacity-40" : "opacity-100"}`}>
          <Waves />
        </div>
      </div>

      <header
        className={`shrink-0 px-4 py-4 transition-colors sm:px-10 sm:py-6 ${
          chatting ? "border-b border-slate-100 bg-white/85 backdrop-blur" : ""
        }`}
      >
        <div className="mx-auto flex max-w-7xl items-center gap-4">
          <div className="mr-auto flex items-center gap-4 sm:gap-6">
            <Image src="/vu-logo.png" alt="Vidyashilp University" width={160} height={58} priority className="h-10 w-auto sm:h-14" />
            <span aria-hidden className="h-10 w-px bg-slate-300 sm:h-14" />
            {chatting && (
              <span className="hidden font-[family-name:var(--font-display)] text-lg font-bold text-brand-900 sm:inline">
                VU Academic Advisor
              </span>
            )}
          </div>

          <button
            className="inline-flex items-center gap-2.5 rounded-full border-2 border-vured-500 bg-white/70 px-4 py-2 text-base font-medium text-brand-900 transition hover:bg-vured-50 sm:px-7 sm:py-2.5 sm:text-lg"
            onClick={newChat}
          >
            <svg viewBox="0 0 24 24" className="h-6 w-6" fill="none" stroke="currentColor" strokeWidth={2} aria-hidden>
              <path
                d="M12 3.5c-4.97 0-9 3.36-9 7.5 0 2.2 1.14 4.18 2.96 5.55L5.5 20.5l4.08-2.2c.78.15 1.59.2 2.42.2 4.97 0 9-3.36 9-7.5s-4.03-7.5-9-7.5Z"
                strokeLinejoin="round"
              />
            </svg>
            New Chat
          </button>
        </div>

        {selected && (
          <div className="mx-auto mt-3 flex max-w-4xl animate-fade-up flex-wrap items-center gap-2 rounded-2xl border border-brand-100 bg-brand-50 px-3 py-2">
            <div className="mr-2 flex items-center gap-2">
              <div className="flex h-8 w-8 items-center justify-center rounded-full bg-brand-900 text-xs font-bold text-white">
                {selected.student_id.slice(-2)}
              </div>
              <div>
                <div className="text-sm font-semibold text-brand-900">
                  {selected.student_id}{" "}
                  <span className="rounded bg-white/80 px-1.5 py-0.5 text-[10px] font-medium text-slate-500">synthetic</span>
                </div>
                <div className="max-w-md text-xs text-slate-600">{selected.scenario}</div>
              </div>
            </div>
            <div className="ml-auto flex flex-wrap gap-2">
              <Stat label="Batch" value={selected.batch} />
              <Stat label="Semester" value={selected.current_semester} />
              <Stat label="CGPA" value={selected.cgpa} />
              <Stat label="Credits" value={selected.completed_credits} />
            </div>
          </div>
        )}
      </header>

      {backendOk === false && (
        <div className="mx-auto mb-2 rounded-full bg-vured-50 px-4 py-1.5 text-center text-sm text-vured-600">
          Can&apos;t reach the advisor right now. Retrying automatically…
        </div>
      )}

      <main ref={scrollRef} className="chat-scroll min-h-0 flex-1 overflow-y-auto overscroll-contain px-4 sm:px-6">
        {!chatting ? (
          <div className="mx-auto flex min-h-full max-w-5xl animate-fade-up flex-col items-center justify-center pb-[18vh] pt-2 text-center">
            <Robot className="h-32 w-auto animate-float sm:h-44" />
            <h1 className="mt-2 font-[family-name:var(--font-display)] text-4xl font-bold tracking-tight text-brand-900 sm:text-6xl">
              VU Academic Advisor
            </h1>
            <span aria-hidden className="mt-6 h-1 w-24 rounded-full bg-vured-500 sm:mt-8 sm:w-32" />

            <div className="mt-10 w-full max-w-4xl sm:mt-14">{composer}</div>
          </div>
        ) : (
          <div className="mx-auto flex max-w-4xl flex-col gap-5 py-8">
            {messages.map((m, i) =>
              m.role === "user" ? (
                <div
                  key={i}
                  className="max-w-[85%] animate-fade-up self-end whitespace-pre-wrap break-words rounded-3xl rounded-tr-md bg-brand-900 px-5 py-3 text-[15px] text-white shadow-lg shadow-brand-900/20 sm:max-w-[75%]"
                >
                  {m.content}
                </div>
              ) : m.role === "assistant" ? (
                <div key={i} className="animate-fade-up self-start">
                  <AssistantMessage
                    reply={m.reply}
                    question={messages.slice(0, i).reduce((q, x) => (x.role === "user" ? x.content : q), "")}
                  />
                </div>
              ) : (
                <div
                  key={i}
                  className="animate-fade-up self-start rounded-2xl border border-vured-500/20 bg-vured-50 px-4 py-2.5 text-sm text-vured-600"
                >
                  ❌ Error: {m.content}
                </div>
              ),
            )}
            {loading && <TypingIndicator />}
          </div>
        )}
      </main>

      {chatting && (
        <footer className="shrink-0 px-4 pb-4 pt-2 sm:px-6">
          <div className="mx-auto max-w-4xl">{composer}</div>
          <p className="mx-auto mt-2 max-w-4xl text-center text-[11px] text-slate-500">
            AI-generated guidance: always confirm important decisions with your Faculty Advisor · Enter to send · This
            conversation: {sessionTokens.toLocaleString()} tokens
            {dbSource && ` · ${dbSource === "supabase" ? "Database: Supabase" : "Database: bundled copy"}`}
          </p>
        </footer>
      )}
    </div>
  );
}
