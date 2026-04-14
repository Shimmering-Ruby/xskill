import React, { useEffect, useState } from "react";
import { getHealth } from "../api";

export default function TopBar({ title = "traj2skill" }) {
  const [connected, setConnected] = useState(null); // null | true | false

  useEffect(() => {
    let alive = true;
    const tick = async () => {
      try {
        const d = await getHealth();
        if (!alive) return;
        setConnected(d && d.status === "ok");
      } catch {
        if (!alive) return;
        setConnected(false);
      }
    };
    tick();
    const id = setInterval(tick, 10_000);
    return () => {
      alive = false;
      clearInterval(id);
    };
  }, []);

  const isOn = connected === true;
  const pillDot = isOn ? "bg-green-500" : "bg-amber-500";
  const pillText = isOn ? "Connected" : "Demo";
  const pillCls = isOn
    ? "bg-green-50 border-green-200 text-green-700"
    : "bg-amber-50 border-amber-200 text-amber-700";

  return (
    <header className="h-12 shrink-0 bg-white border-b border-gray-200 flex items-center px-4 gap-3">
      <span aria-hidden className="text-[15px] leading-none">🧩</span>
      <span className="text-[15px] font-semibold tracking-tight text-gray-900">
        {title}
      </span>
      <span className="text-[10px] bg-gray-100 border border-gray-200 px-2 py-0.5 rounded text-gray-600 uppercase tracking-wider">
        console
      </span>
      <div className="flex-1" />
      <span
        className={`inline-flex items-center gap-1.5 text-[11px] px-2 py-0.5 rounded-full border ${pillCls}`}
      >
        <span className={`w-1.5 h-1.5 rounded-full ${pillDot}`} />
        {pillText}
      </span>
    </header>
  );
}
