import React from "react";

/**
 * Small colored pill showing a similarity score.
 * Color scale:
 *   >= 0.8  green
 *   >= 0.6  blue / indigo
 *   >= 0.4  amber
 *   else    gray
 */
export default function SimilarityPill({ score }) {
  const s = typeof score === "number" && isFinite(score) ? score : 0;
  let cls = "bg-gray-50 text-gray-600 border-gray-200";
  if (s >= 0.8) cls = "bg-green-50 text-green-700 border-green-200";
  else if (s >= 0.6) cls = "bg-[#eef2ff] text-[#4361ee] border-[#c7d2fe]";
  else if (s >= 0.4) cls = "bg-amber-50 text-amber-700 border-amber-200";
  return (
    <span
      className={`inline-flex items-center gap-1 text-[11px] px-2 py-0.5 rounded-full border font-mono ${cls}`}
      title={`similarity: ${s.toFixed(4)}`}
    >
      simil: {s.toFixed(2)}
    </span>
  );
}
