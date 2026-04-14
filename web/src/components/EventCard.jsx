import React from "react";

/**
 * Wrapper for milestone/decision events — rounded card with a colored
 * left border and optional small label.
 */
export default function EventCard({
  accent = "#4361ee",
  label,
  tag,
  children,
  tone = "default", // default | muted | error
}) {
  const bgMap = {
    default: "#ffffff",
    muted: "#f8f9fb",
    error: "#fef2f2",
  };
  const borderMap = {
    default: "#e5e7eb",
    muted: "#e5e7eb",
    error: "#fecaca",
  };
  return (
    <div
      className="rounded-md my-1.5 overflow-hidden"
      style={{
        background: bgMap[tone] || bgMap.default,
        border: `1px solid ${borderMap[tone] || borderMap.default}`,
        borderLeft: `3px solid ${accent}`,
      }}
    >
      {(label || tag) && (
        <div className="flex items-center gap-2 px-3 pt-2">
          {label && (
            <span
              className="text-[10px] font-mono uppercase tracking-wide"
              style={{ color: accent, fontWeight: 600 }}
            >
              {label}
            </span>
          )}
          {tag && (
            <span className="text-[10px] text-gray-400 font-mono">[{tag}]</span>
          )}
        </div>
      )}
      <div className="px-3 py-2 text-[12px] text-gray-700 leading-relaxed">
        {children}
      </div>
    </div>
  );
}
