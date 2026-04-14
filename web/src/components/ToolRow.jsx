import React, { useState } from "react";

/**
 * ToolRow — renders a tool call and (optionally) its follow-up result
 * in a single rounded bordered card.
 *
 * Props:
 *   name:    tool name (e.g. "search_skills")
 *   args:    args string (compact, already stringified) — OR empty
 *   result:  result preview string (optional, appended after a divider)
 *   live:    true while the tool is running (no result yet)
 */
const TOOL_COLOR = {
  search_skills:        { accent: "#7c3aed", bg: "#f5f3ff", border: "#ddd6fe" }, // violet
  search_similar_trajs: { accent: "#7c3aed", bg: "#f5f3ff", border: "#ddd6fe" },
  read_file:            { accent: "#0369a1", bg: "#f0f9ff", border: "#bae6fd" }, // sky
  create_skill:         { accent: "#15803d", bg: "#f0fdf4", border: "#bbf7d0" }, // green
  write_file:           { accent: "#15803d", bg: "#f0fdf4", border: "#bbf7d0" },
};
const DEFAULT_COLOR = { accent: "#6366f1", bg: "#f8fafc", border: "#e2e8f0" };

function parseToolMessage(msg = "") {
  // Two input shapes are accepted:
  //   1. Structured JSON       -> '{"kind":"call","name":"...","args":"..."}'
  //                               '{"kind":"result","name":"...","result":"..."}'
  //   2. Legacy agent.py text  -> 'name(k=v, k=v)'   (call)
  //                               'name -> preview'  (result)
  const trimmed = msg.trim();
  if (trimmed.startsWith("{") && trimmed.endsWith("}")) {
    try {
      const obj = JSON.parse(trimmed);
      if (obj && typeof obj === "object" && obj.kind) {
        if (obj.kind === "call") {
          return {
            kind: "call",
            name: String(obj.name ?? "").trim() || "tool",
            args:
              typeof obj.args === "string"
                ? obj.args
                : obj.args != null
                ? JSON.stringify(obj.args)
                : "",
          };
        }
        if (obj.kind === "result") {
          const r = obj.result ?? obj.preview ?? "";
          const preview = typeof r === "string" ? r : JSON.stringify(r);
          return {
            kind: "result",
            name: String(obj.name ?? "").trim() || "tool",
            preview,
          };
        }
      }
    } catch {
      // fall through to legacy text parser
    }
  }
  const arrow = msg.indexOf(" -> ");
  if (arrow !== -1) {
    const name = msg.slice(0, arrow).trim();
    const preview = msg.slice(arrow + 4).trim();
    return { kind: "result", name, preview };
  }
  const paren = msg.indexOf("(");
  if (paren !== -1 && msg.endsWith(")")) {
    const name = msg.slice(0, paren).trim();
    const args = msg.slice(paren + 1, -1);
    return { kind: "call", name, args };
  }
  return { kind: "call", name: msg.trim(), args: "" };
}

export default function ToolRow({ name = "", args = "", result = "", live = false }) {
  const [open, setOpen] = useState(false);
  const col = TOOL_COLOR[name] || DEFAULT_COLOR;
  const hasMore = (args && args.length > 40) || result;
  const argsShort =
    args && args.length > 60 ? args.slice(0, 60) + "…" : args || "";
  return (
    <div
      className="my-1.5 rounded-md overflow-hidden"
      style={{
        background: col.bg,
        border: `1px solid ${live ? col.accent + "55" : col.border}`,
      }}
    >
      <div
        className="flex items-center gap-2 px-3 py-2 cursor-pointer select-none"
        onClick={() => hasMore && setOpen((o) => !o)}
      >
        <span
          className="inline-block rounded-full"
          style={{
            width: 7,
            height: 7,
            background: col.accent,
            boxShadow: live ? `0 0 0 3px ${col.accent}22` : "none",
          }}
        />
        <span
          className="font-mono text-[11px] font-semibold"
          style={{ color: col.accent }}
        >
          {name || "tool"}
        </span>
        {argsShort && (
          <span className="font-mono text-[11px] text-gray-500 truncate flex-1">
            {argsShort}
          </span>
        )}
        <span
          className="ml-auto text-[10px] font-mono"
          style={{ color: live ? col.accent : "#9ca3af" }}
        >
          {live ? "running" : "done"}
        </span>
        {hasMore && (
          <span className="text-[10px] text-gray-300 font-mono">
            {open ? "▾" : "▸"}
          </span>
        )}
      </div>
      {open && hasMore && (
        <div
          className="px-3 pb-2 text-[11px] font-mono text-gray-600 space-y-2"
          style={{ borderTop: `1px solid ${col.border}` }}
        >
          {args && (
            <div className="pt-2 break-all whitespace-pre-wrap">{args}</div>
          )}
          {result && (
            <div
              className="pt-2 break-all whitespace-pre-wrap text-gray-700"
              style={{ borderTop: args ? `1px dashed ${col.border}` : "none" }}
            >
              <span className="text-gray-400">→ </span>
              {result}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

export { parseToolMessage };
