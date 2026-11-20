/* ==========================================================================
   Model router

   Chooses the best Puter.js model for a task and provides the ordered failover
   chain.  Keep this table in sync with assistant/status.py.
   ========================================================================== */

export const ROUTING = Object.freeze({
  statistics: "claude",
  dataset: "deepseek-chat",
  research: "claude",
  chart: "gemini",
  chat: "qwen",
  fast: "qwen",
});

export const FALLBACK_CHAIN = Object.freeze([
  "claude",
  "gpt-4o",
  "gemini",
  "deepseek-chat",
  "qwen",
]);

/** Single source of truth for the models offered in the assistant selector. */
export const MODEL_REGISTRY = Object.freeze([
  Object.freeze({ id: "gpt-4o", label: "GPT-4o", description: "General reasoning" }),
  Object.freeze({ id: "claude", label: "Claude", description: "Statistics and research" }),
  Object.freeze({ id: "gemini", label: "Gemini", description: "Chart interpretation" }),
  Object.freeze({ id: "deepseek-chat", label: "DeepSeek", description: "Dataset analysis" }),
  Object.freeze({ id: "qwen", label: "Qwen", description: "Fast general chat" }),
]);

export const MODEL_LABELS = Object.freeze(
  MODEL_REGISTRY.reduce((labels, model) => {
    labels[model.id] = model.label;
    return labels;
  }, {})
);

export const MODEL_IDS = Object.freeze(MODEL_REGISTRY.map((model) => model.id));

export function isSupportedModel(model) {
  return MODEL_IDS.includes(String(model || ""));
}

export const TASK_LABELS = Object.freeze({
  statistics: "Statistical reasoning",
  dataset: "Dataset analysis",
  research: "Research writing",
  chart: "Chart interpretation",
  chat: "General chat",
  fast: "Fast UI help",
});

const TASK_ALIASES = {
  explain_plot: "chart",
  explain_image: "chart",
  image: "chart",
  plot: "chart",
  chart: "chart",
  statistics: "statistics",
  statistical: "statistics",
  analysis: "statistics",
  explain_analysis: "statistics",
  model: "statistics",
  dataset: "dataset",
  data: "dataset",
  summary: "dataset",
  research: "research",
  report: "research",
  writing: "research",
  caption: "research",
  chat: "chat",
  general: "chat",
  fast: "fast",
  help: "fast",
  ui: "fast",
};

const RULES = [
  {
    task: "chart",
    re: /\b(chart|plot|figure|graph|visual|axis|legend|image|screenshot|dashboard|heatmap|histogram|scatter|boxplot|bar chart|line chart)\b/i,
  },
  {
    task: "statistics",
    re: /\b(statistic|statistical|regression|correlation|anomal|outlier|significan|p[- ]?value|confidence interval|distribution|variance|standard deviation|trend|seasonal|diurnal|machine learning|forecast|predict|rmse|r²|r2|model fit|residual|insight|finding|result)\b/i,
  },
  {
    task: "research",
    re: /\b(write|draft|report|abstract|introduction|methodology|discussion|conclusion|caption|publication|manuscript|paper|executive summary|recommendation|policy brief)\b/i,
  },
  {
    task: "dataset",
    re: /\b(dataset|data set|column|columns|rows|summarise|summarize|overview|quality|missing|coverage|station|compare|describe|profile|schema|variable|completeness)\b/i,
  },
  {
    task: "fast",
    re: /\b(how do i|how to|where is|navigate|shortcut|button|menu|ui|interface|getting started|tutorial|help)\b/i,
  },
];

/** Normalise an explicit task hint, or classify the question text. */
export function classifyTask(question, hint) {
  if (hint) {
    const key = String(hint).toLowerCase().replace(/[\s-]+/g, "_");
    if (TASK_ALIASES[key]) return TASK_ALIASES[key];
    if (ROUTING[key]) return key;
  }
  const text = String(question || "");
  for (const rule of RULES) {
    if (rule.re.test(text)) return rule.task;
  }
  return "chat";
}

/** Preferred model for a task. */
export function selectModel(task) {
  return ROUTING[task] || ROUTING.chat;
}

/** Preferred model first, then the rest of the canonical failover chain. */
export function buildModelOrder(preferred) {
  const order = [];
  if (isSupportedModel(preferred)) order.push(String(preferred));
  for (const model of FALLBACK_CHAIN) {
    if (!order.includes(model)) order.push(model);
  }
  return order;
}

export function modelLabel(model) {
  return MODEL_LABELS[model] || String(model || "AI");
}

export function taskLabel(task) {
  return TASK_LABELS[task] || TASK_LABELS.chat;
}
