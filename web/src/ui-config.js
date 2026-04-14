export const defaultConfig = {
  tabs: [
    { id: 'ingest',   label: 'Ingest',   dot: '#b45309', desc: '上传轨迹并建立向量索引' },
    { id: 'search',   label: 'Search',   dot: '#4361ee', desc: '按语义检索已入库的轨迹' },
    { id: 'generate', label: 'Generate', dot: '#0f766e', desc: 'Agent 分析轨迹并生成/更新 Skill' },
    { id: 'evaluate', label: 'Evaluate', dot: '#15803d', desc: '7 维 LLM 打分评估 Skill 质量' },
  ],
  title: 'traj2skill',
};

export async function loadUiConfig() {
  try {
    const r = await fetch('/ui-config.json', { cache: 'no-store' });
    if (!r.ok) return defaultConfig;
    const override = await r.json();
    if (!override || typeof override !== 'object') return defaultConfig;
    return {
      ...defaultConfig,
      ...override,
      tabs: Array.isArray(override.tabs) && override.tabs.length
        ? override.tabs
        : defaultConfig.tabs,
    };
  } catch {
    return defaultConfig;
  }
}
