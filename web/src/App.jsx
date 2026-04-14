import React, { useEffect, useMemo, useState } from "react";
import TopBar from "./components/TopBar";
import Sidebar from "./components/Sidebar";
import PageIngest from "./pages/Ingest";
import PageSearch from "./pages/Search";
import PageGenerate from "./pages/Generate";
import PageEval from "./pages/Eval";
import { defaultConfig, loadUiConfig } from "./ui-config";

const PAGE_BY_ID = {
  ingest: PageIngest,
  search: PageSearch,
  generate: PageGenerate,
  evaluate: PageEval,
};

export default function App() {
  const [config, setConfig] = useState(defaultConfig);
  const [activeTab, setActiveTab] = useState(defaultConfig.tabs[0].id);

  useEffect(() => {
    let alive = true;
    loadUiConfig().then((cfg) => {
      if (!alive) return;
      setConfig(cfg);
      // Keep current activeTab if still present, else fall back to first.
      if (!cfg.tabs.some((t) => t.id === activeTab)) {
        setActiveTab(cfg.tabs[0]?.id ?? "ingest");
      }
    });
    return () => {
      alive = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const activeTabDef = useMemo(
    () => config.tabs.find((t) => t.id === activeTab) ?? config.tabs[0],
    [config.tabs, activeTab]
  );

  const PageComp = PAGE_BY_ID[activeTabDef?.id] || PageIngest;

  return (
    <div className="h-screen w-screen flex flex-col overflow-hidden bg-[#f8f9fb]">
      <TopBar title={config.title} />
      <div className="flex-1 flex overflow-hidden min-h-0">
        <Sidebar
          tabs={config.tabs}
          activeTab={activeTab}
          onSelect={setActiveTab}
        />
        <main className="flex-1 overflow-auto">
          <PageComp desc={activeTabDef?.desc} />
        </main>
      </div>
    </div>
  );
}
