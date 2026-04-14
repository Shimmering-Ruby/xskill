import React, { useEffect, useRef } from "react";

/**
 * SSE log viewer.
 * Each entry: { type: "progress"|"log"|"result"|"error"|"done", data: any, ts }
 * Auto-scrolls to bottom when new events arrive.
 */
const TAG_COLORS = {
  progress: "#4361ee",
  log: "#9ca3af",
  result: "#15803d",
  error: "#ef4444",
  done: "#a78bfa",
  message: "#9ca3af",
};

function formatLine(ev) {
  const d = ev.data ?? {};
  if (ev.type === "progress") {
    const step = d.step ?? "";
    const cur = d.current;
    const tot = d.total;
    const det = d.detail ? ` - ${d.detail}` : "";
    let frac = "";
    if (typeof cur === "number" && typeof tot === "number" && tot > 0) {
      frac = ` ${cur}/${tot}`;
    }
    return `${step}${frac}${det}`;
  }
  if (ev.type === "log") {
    const tag = d.tag ? `(${d.tag}) ` : "";
    return `${tag}${d.msg ?? JSON.stringify(d)}`;
  }
  if (ev.type === "error") {
    return d.error ?? d.message ?? JSON.stringify(d);
  }
  if (ev.type === "result") {
    try {
      return JSON.stringify(d);
    } catch {
      return String(d);
    }
  }
  if (ev.type === "done") {
    return d.aborted ? "aborted" : "stream closed";
  }
  try {
    return JSON.stringify(d);
  } catch {
    return String(d);
  }
}

export default function SSELog({ events }) {
  const scrollRef = useRef(null);
  useEffect(() => {
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [events]);

  return (
    <div
      ref={scrollRef}
      className="font-mono text-[11px] rounded-md max-h-[380px] overflow-auto p-3 leading-relaxed"
      style={{ background: "#1e1e2e", color: "#cdd6f4" }}
    >
      {(!events || events.length === 0) && (
        <div style={{ color: "#6c7086" }}>(no events yet)</div>
      )}
      {events &&
        events.map((ev, i) => (
          <div key={i} className="whitespace-pre-wrap break-words">
            <span style={{ color: TAG_COLORS[ev.type] || "#9ca3af" }}>
              [{ev.type}]
            </span>{" "}
            <span>{formatLine(ev)}</span>
          </div>
        ))}
    </div>
  );
}
