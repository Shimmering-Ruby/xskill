import React, { useCallback, useRef, useState } from "react";
import { chatMessageStream, archiveChat } from "../api";

/* ─── Inline Markdown — bold, code, links ────────────── */
function Inline({ text = "" }) {
  const re = /(\*\*(.+?)\*\*|`([^`]+)`|\[([^\]]+)\]\(([^)]+)\))/g;
  const out = [];
  let last = 0,
    m,
    k = 0;
  while ((m = re.exec(text)) !== null) {
    if (m.index > last) out.push(text.slice(last, m.index));
    if (m[2])
      out.push(
        <strong key={k++} className="font-semibold">
          {m[2]}
        </strong>
      );
    else if (m[3])
      out.push(
        <code
          key={k++}
          className="bg-gray-100 text-[#e11d48] px-1 rounded text-[12px] font-mono"
        >
          {m[3]}
        </code>
      );
    else if (m[4])
      out.push(
        <a
          key={k++}
          href={m[5]}
          target="_blank"
          rel="noreferrer"
          className="text-blue-600 underline underline-offset-2"
        >
          {m[4]}
        </a>
      );
    last = m.index + m[0].length;
  }
  if (last < text.length) out.push(text.slice(last));
  return <>{out}</>;
}

/* ─── Block-level Markdown renderer ──────────────────── */
function Prose({ text = "" }) {
  const lines = text.split("\n");
  const nodes = [];
  let i = 0,
    k = 0;
  while (i < lines.length) {
    const l = lines[i];

    // Fenced code blocks
    if (l.startsWith("```")) {
      const lang = l.slice(3).trim();
      const code = [];
      i++;
      while (i < lines.length && !lines[i].startsWith("```")) {
        code.push(lines[i]);
        i++;
      }
      nodes.push(
        <pre
          key={k++}
          className="font-mono text-[12px] rounded-lg my-2 p-3 overflow-x-auto"
          style={{ background: "#1e1e2e", color: "#cdd6f4", lineHeight: 1.55 }}
        >
          {lang && (
            <div className="text-[10px] mb-1.5" style={{ color: "#7f849c" }}>
              {lang}
            </div>
          )}
          {code.join("\n")}
        </pre>
      );
      i++;
      continue;
    }

    // Headings
    const hm = l.match(/^(#{1,3})\s+(.+)/);
    if (hm) {
      const cls = [
        "text-[15px] font-semibold mt-3 mb-1",
        "text-[14px] font-semibold mt-2 mb-1",
        "text-[13px] font-semibold mt-1.5 mb-0.5",
      ][hm[1].length - 1];
      nodes.push(
        <div key={k++} className={`text-gray-900 ${cls}`}>
          <Inline text={hm[2]} />
        </div>
      );
      i++;
      continue;
    }

    // Unordered list
    if (/^[-*]\s/.test(l)) {
      const items = [];
      while (i < lines.length && /^[-*]\s/.test(lines[i])) {
        items.push(lines[i].replace(/^[-*]\s/, ""));
        i++;
      }
      nodes.push(
        <div key={k++} className="my-1 pl-1">
          {items.map((it, j) => (
            <div
              key={j}
              className="flex gap-2 text-[13px] leading-relaxed text-gray-700 mb-0.5"
            >
              <span className="text-gray-400 mt-0.5 shrink-0 select-none">&#8226;</span>
              <span>
                <Inline text={it} />
              </span>
            </div>
          ))}
        </div>
      );
      continue;
    }

    // Ordered list
    if (/^\d+\.\s/.test(l)) {
      const items = [];
      while (i < lines.length && /^\d+\.\s/.test(lines[i])) {
        items.push(lines[i].replace(/^\d+\.\s/, ""));
        i++;
      }
      nodes.push(
        <div key={k++} className="my-1 pl-1">
          {items.map((it, j) => (
            <div
              key={j}
              className="flex gap-2 text-[13px] leading-relaxed text-gray-700 mb-0.5"
            >
              <span className="text-gray-400 shrink-0 tabular-nums font-mono text-[12px]">
                {j + 1}.
              </span>
              <span>
                <Inline text={it} />
              </span>
            </div>
          ))}
        </div>
      );
      continue;
    }

    // Blockquote
    if (l.startsWith("> ")) {
      nodes.push(
        <div
          key={k++}
          className="border-l-2 border-gray-300 pl-3 text-gray-500 italic text-[13px] leading-relaxed my-1"
        >
          <Inline text={l.slice(2)} />
        </div>
      );
      i++;
      continue;
    }

    // Horizontal rule
    if (/^---+$/.test(l.trim())) {
      nodes.push(<hr key={k++} className="border-gray-200 my-3" />);
      i++;
      continue;
    }

    // Empty line
    if (l.trim() === "") {
      nodes.push(<div key={k++} className="h-1.5" />);
      i++;
      continue;
    }

    // Plain paragraph
    nodes.push(
      <div key={k++} className="text-[13px] leading-relaxed text-gray-700 mb-0.5">
        <Inline text={l} />
      </div>
    );
    i++;
  }
  return <>{nodes}</>;
}

/* ─── Skill Banner — 醒目展示命中的 skill ─────────────── */
function SkillBanner({ meta, expanded, onToggle }) {
  if (!meta?.skill_name) return null;
  const isStaging = meta.side === "staging";
  const accent = isStaging ? "#f59e0b" : "#10b981";

  return (
    <div
      className="rounded-lg text-[11px] mb-1.5 overflow-hidden"
      style={{
        borderLeft: `3px solid ${accent}`,
        background: isStaging ? "#fffbeb" : "#ecfdf5",
        animation: "slideIn 0.15s ease-out",
      }}
    >
      {/* header — always visible */}
      <div
        className="flex items-center gap-2.5 px-3 py-2 cursor-pointer select-none"
        onClick={onToggle}
      >
        {/* skill icon + name */}
        <span style={{ fontSize: 13 }}>&#9881;</span>
        <span className="font-semibold text-gray-900 text-[12px]">
          {meta.skill_name}
        </span>

        {/* side badge */}
        <span
          className={`inline-flex items-center gap-1 text-[10px] font-mono font-medium px-2 py-0.5 rounded-full border ${
            isStaging
              ? "bg-amber-100 text-amber-800 border-amber-300"
              : "bg-emerald-100 text-emerald-800 border-emerald-300"
          }`}
        >
          <span
            className={`w-1.5 h-1.5 rounded-full ${
              isStaging ? "bg-amber-500" : "bg-emerald-500"
            }`}
          />
          {meta.side}
        </span>

        {isStaging && (
          <span className="text-[10px] font-medium text-amber-600 bg-amber-100 px-1.5 py-0.5 rounded">
            CANARY
          </span>
        )}

        {/* right: sha + sim */}
        <div className="ml-auto flex items-center gap-3 text-[10px] font-mono text-gray-400">
          {meta.commit_sha && <span>sha:{meta.commit_sha}</span>}
          {meta.similarity != null && (
            <span
              className={
                meta.similarity >= 0.8
                  ? "text-emerald-600"
                  : meta.similarity >= 0.6
                  ? "text-indigo-500"
                  : "text-gray-400"
              }
            >
              sim:{meta.similarity.toFixed(3)}
            </span>
          )}
          <span style={{ fontSize: 9, color: "#d1d5db" }}>
            {expanded ? "▾" : "▸"}
          </span>
        </div>
      </div>

      {/* expanded: show skill body preview */}
      {expanded && meta.skill_body && (
        <div className="px-3 pb-2.5 border-t border-gray-200/50">
          <div className="text-[10px] text-gray-400 uppercase tracking-wider mt-2 mb-1">
            Skill Body
          </div>
          <pre className="text-[10px] font-mono text-gray-600 leading-relaxed whitespace-pre-wrap break-words max-h-40 overflow-auto bg-white/60 rounded p-2">
            {meta.skill_body.slice(0, 1000)}
            {meta.skill_body.length > 1000 ? "\n..." : ""}
          </pre>
        </div>
      )}
    </div>
  );
}

/* ─── Tool Bubble — 折叠式工具调用展示 + 灰度标记 ──────── */
function ToolRow({ tc }) {
  const [open, setOpen] = useState(false);
  const name = tc.name || tc.tool || "";
  const live = tc.live !== false;
  const isSkillTool = name === "search_skills" || name === "read_skill";
  const accent = isSkillTool ? "#10b981" : "#4361ee";
  const pairs = Object.entries(tc.args || {}).map(([k, v]) => ({
    k,
    v: typeof v === "string" ? v : JSON.stringify(v),
  }));

  // Parse canary info from result
  let canaryInfo = null;
  if (tc.result && isSkillTool) {
    try {
      const parsed = JSON.parse(tc.result);
      if (Array.isArray(parsed) && parsed.length > 0) {
        canaryInfo = parsed.map((s) => ({ name: s.skill_name, side: s.side }));
      } else if (parsed.skill_name) {
        canaryInfo = [{ name: parsed.skill_name, side: parsed.side }];
      }
    } catch {}
  }

  return (
    <div
      className="my-1"
      style={{
        borderLeft: `2px solid ${live ? accent : "#e5e7eb"}`,
        paddingLeft: 10,
        animation: "slideIn 0.12s ease-out",
      }}
    >
      <div
        className="flex items-center gap-2 cursor-pointer select-none py-1"
        onClick={() => setOpen((o) => !o)}
      >
        {live ? (
          <span
            style={{
              color: accent,
              fontSize: 9,
              display: "inline-block",
              animation: "spin 1s linear infinite",
            }}
          >
            &#8635;
          </span>
        ) : (
          <span
            style={{
              width: 5,
              height: 5,
              borderRadius: "50%",
              background: accent,
              display: "inline-block",
            }}
          />
        )}
        <span
          className="font-mono"
          style={{ fontSize: 11, fontWeight: 500, color: accent }}
        >
          {name}
        </span>
        {!open && pairs.length > 0 && (
          <span className="text-[10px] text-gray-400 flex-1 truncate">
            {pairs[0].v.length > 40
              ? pairs[0].v.slice(0, 40) + "..."
              : pairs[0].v}
          </span>
        )}
        <span className="text-[10px] ml-auto" style={{ color: live ? accent : "#9ca3af" }}>
          {live ? "..." : "Done"}
        </span>
      </div>
      {open && pairs.length > 0 && (
        <div className="pb-1 pt-0.5">
          {pairs.map((p, i) => (
            <div
              key={i}
              className="text-[10px] flex gap-2"
              style={{ lineHeight: 1.7 }}
            >
              <span className="text-gray-400 shrink-0" style={{ minWidth: 50 }}>
                {p.k}
              </span>
              <span className="text-gray-700 break-all font-mono">
                = &quot;{p.v}&quot;
              </span>
            </div>
          ))}
        </div>
      )}
      {tc.result && open && (
        <pre className="text-[10px] text-gray-500 bg-gray-50 rounded p-1.5 mt-0.5 mb-1 max-h-32 overflow-auto whitespace-pre-wrap break-words font-mono">
          {tc.result}
        </pre>
      )}
      {/* Canary/side badges for skill tools */}
      {canaryInfo && !live && (
        <div className="flex gap-1.5 py-0.5 flex-wrap">
          {canaryInfo.map((s, i) => (
            <span
              key={i}
              className={`inline-flex items-center gap-1 text-[9px] font-mono px-1.5 py-0.5 rounded-full border ${
                s.side === "staging"
                  ? "bg-amber-50 text-amber-700 border-amber-300"
                  : "bg-emerald-50 text-emerald-700 border-emerald-300"
              }`}
            >
              <span
                className={`w-1.5 h-1.5 rounded-full ${
                  s.side === "staging" ? "bg-amber-500" : "bg-emerald-500"
                }`}
              />
              {s.name} ({s.side})
            </span>
          ))}
        </div>
      )}
    </div>
  );
}

/* ─── Message Bubble — items-based rendering ──────────── */
function MessageBubble({ msg, expandedSkill, onToggleSkill }) {
  const isUser = msg.role === "user";

  // Build render items: for user msgs use text; for assistant use items array
  const renderItems = isUser
    ? [{ type: "text", content: msg.text || "" }]
    : msg.items && msg.items.length > 0
    ? msg.items
    : msg.text
    ? [{ type: "text", content: msg.text }]
    : [];

  const hasAnyText = renderItems.some((x) => x.type === "text" && x.content);

  return (
    <div
      className={`flex ${isUser ? "justify-end" : "justify-start"} mb-4`}
      style={{ animation: "slideIn 0.15s ease-out" }}
    >
      <div className={`max-w-[85%] ${isUser ? "" : "w-full max-w-[85%]"}`}>
        {/* Role label */}
        <div className={`text-[10px] font-medium tracking-wide text-gray-400 mb-1 ${isUser ? "text-right mr-1" : "ml-1"}`}>
          {isUser ? "YOU" : "AI AGENT"}
        </div>

        {/* Skill banner */}
        {!isUser && msg.meta?.skill_name && (
          <SkillBanner meta={msg.meta} expanded={expandedSkill} onToggle={onToggleSkill} />
        )}

        {/* User bubble */}
        {isUser && (
          <div className="bg-[#4361ee] text-white rounded-xl rounded-br-sm px-4 py-2.5 shadow-sm text-[13px] leading-relaxed">
            <div className="whitespace-pre-wrap break-words">{msg.text}</div>
          </div>
        )}

        {/* Assistant: interleaved items */}
        {!isUser && renderItems.map((item, i) => {
          if (item.type === "tool") {
            return <ToolRow key={i} tc={item.tc} />;
          }
          if (item.type === "text" && item.content) {
            return (
              <div
                key={i}
                className="bg-white border border-gray-200 text-gray-800 rounded-xl rounded-bl-sm px-5 py-3.5 shadow-[0_1px_4px_rgba(0,0,0,0.06)] text-[13px] leading-relaxed mb-1"
              >
                <div className="break-words">
                  <Prose text={item.content} />
                </div>
              </div>
            );
          }
          return null;
        })}

        {/* Streaming cursor */}
        {!isUser && msg.streaming && (
          <span
            className="inline-block w-[2px] h-4 bg-indigo-500 ml-2 align-text-bottom rounded-sm"
            style={{ animation: "blink 1s step-end infinite" }}
          />
        )}

        {/* Loading dots when no items yet */}
        {!isUser && msg.streaming && !hasAnyText && renderItems.every((x) => x.type !== "tool") && (
          <div className="flex gap-1.5 py-2 px-1">
            {[0, 1, 2].map((i) => (
              <span
                key={i}
                className="w-1.5 h-1.5 rounded-full bg-gray-300 inline-block"
                style={{ animation: `dotPulse 1.2s ease-in-out ${i * 0.2}s infinite` }}
              />
            ))}
          </div>
        )}

        {/* Aborted marker */}
        {msg.aborted && (
          <div className="text-[10px] text-gray-400 mt-1 flex items-center gap-1.5 ml-1">
            <span className="inline-block w-1.5 h-1.5 rounded-sm bg-gray-300" />
            stopped
          </div>
        )}
      </div>
    </div>
  );
}

/* ─── Main Chat Page ─────────────────────────────────── */
export default function PageChat({ desc }) {
  const [messages, setMessages] = useState([]);
  const [input, setInput] = useState("");
  const [streaming, setStreaming] = useState(false);
  const [expandedSkills, setExpandedSkills] = useState({});
  const bottomRef = useRef(null);
  const abortRef = useRef(null);
  const [sessionId] = useState(() => `sess_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`);

  const scrollDown = useCallback(
    () => setTimeout(() => bottomRef.current?.scrollIntoView({ behavior: "smooth" }), 30),
    []
  );

  const toggleSkill = useCallback((idx) => {
    setExpandedSkills((prev) => ({ ...prev, [idx]: !prev[idx] }));
  }, []);

  const handleStop = useCallback(() => {
    if (abortRef.current) {
      abortRef.current.abort();
      abortRef.current = null;
    }
  }, []);

  // streamItems: interleaved array of {type:"text",content} | {type:"tool",tc}
  const streamItemsRef = useRef([]);

  const _updateLastMsg = useCallback((updater) => {
    setMessages((prev) => {
      const updated = [...prev];
      const last = updated[updated.length - 1];
      if (last?.streaming) updater(last);
      return updated;
    });
  }, []);

  const send = async () => {
    const text = input.trim();
    if (!text || streaming) return;
    setInput("");
    setMessages((prev) => [...prev, { role: "user", text }]);
    scrollDown();
    setStreaming(true);
    streamItemsRef.current = [];

    // Insert empty assistant msg with items array
    setMessages((prev) => [
      ...prev,
      { role: "assistant", items: [], meta: null, streaming: true },
    ]);

    let meta = null;
    const ctrl = new AbortController();
    abortRef.current = ctrl;

    try {
      await chatMessageStream(text, { session_id: sessionId }, {
        signal: ctrl.signal,
        onEvent: (type, data) => {
          if (type === "meta") {
            meta = data;
            _updateLastMsg((m) => { m.meta = data; });
          } else if (type === "token") {
            const items = streamItemsRef.current;
            const last = items[items.length - 1];
            if (last?.type === "text") {
              last.content += data.text || "";
            } else {
              items.push({ type: "text", content: data.text || "" });
            }
            streamItemsRef.current = [...items];
            _updateLastMsg((m) => { m.items = streamItemsRef.current; });
            scrollDown();
          } else if (type === "tool_call") {
            // Flush current text slot, insert tool, start new text slot
            streamItemsRef.current = [
              ...streamItemsRef.current,
              { type: "tool", tc: { ...data, live: true } },
            ];
            _updateLastMsg((m) => { m.items = streamItemsRef.current; });
            scrollDown();
          } else if (type === "tool_result") {
            const items = streamItemsRef.current;
            const tool = items.find((x) => x.type === "tool" && x.tc?.id === data.id);
            if (tool) {
              tool.tc.result = data.result;
              tool.tc.live = false;
            }
            streamItemsRef.current = [...items];
            _updateLastMsg((m) => { m.items = streamItemsRef.current; });
            scrollDown();
          } else if (type === "error") {
            streamItemsRef.current = [
              ...streamItemsRef.current,
              { type: "text", content: `\n\nError: ${data.error || data.message || "unknown"}` },
            ];
            _updateLastMsg((m) => { m.items = streamItemsRef.current; });
          }
        },
      });
    } catch (err) {
      if (!ctrl.signal.aborted) {
        const items = streamItemsRef.current;
        if (!items.some((x) => x.type === "text" && x.content)) {
          items.push({ type: "text", content: `Error: ${err.message}` });
          streamItemsRef.current = items;
        }
      }
    } finally {
      const items = streamItemsRef.current.filter(
        (x) => x.type !== "text" || x.content?.trim()
      );
      streamItemsRef.current = [];
      setMessages((prev) => {
        const updated = [...prev];
        const last = updated[updated.length - 1];
        if (last?.streaming) {
          last.items = items.length > 0 ? items : [{ type: "text", content: "(no response)" }];
          last.streaming = false;
          if (ctrl.signal.aborted) last.aborted = true;
        }
        return [...updated];
      });
      setStreaming(false);
      abortRef.current = null;
      scrollDown();
    }
  };

  const onKey = (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      send();
    }
  };

  // Stats
  const skillHits = messages.filter((m) => m.role === "assistant" && m.meta?.skill_name);
  const stagingHits = skillHits.filter((m) => m.meta?.side === "staging");

  // Archive
  const [archiving, setArchiving] = useState(false);
  const [archiveResult, setArchiveResult] = useState(null);

  const handleArchive = async () => {
    if (archiving || messages.length === 0) return;
    setArchiving(true);
    setArchiveResult(null);
    try {
      const lastMeta = [...messages].reverse().find((m) => m.meta?.skill_name)?.meta;
      const res = await archiveChat({
        messages: messages.map((m) => ({
          role: m.role,
          text: m.text || "",
          items: m.items || [],
          meta: m.meta || null,
        })),
        skill_name: lastMeta?.skill_name || "",
        side: lastMeta?.side || "",
        sha: lastMeta?.commit_sha || "",
      });
      setArchiveResult(res);
    } catch (err) {
      setArchiveResult({ ok: false, message: err.message });
    } finally {
      setArchiving(false);
    }
  };

  return (
    <div className="flex flex-col h-full">
      {/* Header */}
      <div className="px-6 py-3 border-b border-gray-200 bg-white flex items-center gap-3">
        <h2 className="text-base font-semibold text-gray-900">Chat</h2>
        {skillHits.length > 0 && (
          <div className="flex items-center gap-2 text-[11px]">
            <span className="bg-emerald-50 text-emerald-700 px-2 py-0.5 rounded-full border border-emerald-200">
              {skillHits.length} skill hit{skillHits.length > 1 ? "s" : ""}
            </span>
            {stagingHits.length > 0 && (
              <span className="bg-amber-50 text-amber-700 px-2 py-0.5 rounded-full border border-amber-200">
                {stagingHits.length} canary
              </span>
            )}
          </div>
        )}
        <div className="ml-auto flex items-center gap-2">
          {archiveResult && (
            <span className={`text-[10px] ${archiveResult.ok ? "text-emerald-600" : "text-red-500"}`}>
              {archiveResult.ok ? `Archived: ${archiveResult.traj_id}` : archiveResult.message}
            </span>
          )}
          {messages.length > 0 && !streaming && (
            <button
              onClick={handleArchive}
              disabled={archiving}
              className="text-[11px] px-2.5 py-1 rounded border border-gray-300 text-gray-600 hover:bg-gray-50 hover:border-gray-400 disabled:opacity-40 transition-colors flex items-center gap-1"
            >
              {archiving ? "Archiving..." : "Archive"}
            </button>
          )}
          {desc && <span className="text-[11px] text-gray-400">{desc}</span>}
        </div>
      </div>

      {/* Messages */}
      <div className="flex-1 overflow-auto px-6 py-4 bg-[#f8f9fb]">
        {messages.length === 0 && (
          <div className="text-center mt-20 space-y-2">
            <div className="text-3xl opacity-30">&#128172;</div>
            <div className="text-gray-400 text-sm">输入问题开始对话</div>
            <div className="text-gray-300 text-xs max-w-md mx-auto">
              回复将自动匹配 Skill 库中最相关的 Skill 并高亮展示。
              灰度期间部分请求会使用 staging 分支版本。
            </div>
          </div>
        )}
        {messages.map((m, i) => (
          <MessageBubble
            key={i}
            msg={m}
            expandedSkill={expandedSkills[i]}
            onToggleSkill={() => toggleSkill(i)}
          />
        ))}
        <div ref={bottomRef} />
      </div>

      {/* Input bar */}
      <div className="px-6 py-3 border-t border-gray-200 bg-white">
        <div className="flex gap-2 items-end">
          <textarea
            rows={1}
            className="flex-1 resize-none rounded-lg border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-[#4361ee]/40 focus:border-[#4361ee]"
            placeholder="输入你的问题..."
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={onKey}
            disabled={streaming}
          />
          {streaming ? (
            <button
              onClick={handleStop}
              className="w-9 h-9 bg-gray-800 text-white rounded-lg flex items-center justify-center hover:bg-gray-700 transition-colors"
              title="Stop"
            >
              <span className="w-2.5 h-2.5 bg-white rounded-sm block" />
            </button>
          ) : (
            <button
              onClick={send}
              disabled={!input.trim()}
              className={`w-9 h-9 rounded-lg flex items-center justify-center text-lg font-bold transition-colors ${
                input.trim()
                  ? "bg-[#4361ee] text-white hover:bg-[#3a56d4]"
                  : "bg-gray-200 text-gray-400 cursor-not-allowed"
              }`}
            >
              &#8593;
            </button>
          )}
        </div>
      </div>

      {/* Inline CSS animations */}
      <style>{`
        @keyframes slideIn {
          from { opacity: 0; transform: translateY(4px); }
          to   { opacity: 1; transform: translateY(0); }
        }
        @keyframes dotPulse {
          0%, 100% { opacity: 0.3; transform: scale(0.8); }
          50%      { opacity: 1;   transform: scale(1); }
        }
        @keyframes blink {
          50% { opacity: 0; }
        }
        @keyframes spin {
          to { transform: rotate(360deg); }
        }
      `}</style>
    </div>
  );
}
