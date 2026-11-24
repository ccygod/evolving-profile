import type { GraphData } from "@/components/graph-2d";

export type PreferenceDimension =
  | "communication"
  | "learning"
  | "reasoning"
  | "collaboration"
  | "delivery";
export type PreferenceLocale = "zh" | "en";
export type PreferenceScope = "global" | "project_scoped" | "task_local";
export type PreferenceEnforcement = "hard_guardrail" | "default_policy" | "soft_style";

export type PreferenceUnit = {
  id: string;
  primary_category: PreferenceDimension;
  text: string;
  scope?: Record<string, unknown> | PreferenceScope;
  scope_level?: PreferenceScope;
  enforcement?: PreferenceEnforcement;
  inject_default?: boolean;
  applies_when?: string[];
  exceptions?: string[];
  effect_on_action?: string;
  evidence_refs?: Array<{ id?: string }>;
  activated_at?: number | string;
  preference_audit?: { state?: string; reason?: string; validity_kind?: string };
};

export type FusionModel = {
  id: string;
  title: string;
  dimensions: PreferenceDimension[];
  purpose?: string;
  mechanism?: string;
  confidence?: number;
  last_verified_at?: string;
  status?: string;
  sections?: Array<{
    text?: string;
    applies_when?: string[];
    exceptions?: string[];
    counterevidence?: string[];
    guidance_refs?: Array<{ id: string }>;
  }>;
};

export const FUSION_MODEL_COLOR = "#8b5cf6";

const DIMENSIONS: PreferenceDimension[] = [
  "communication",
  "learning",
  "reasoning",
  "collaboration",
  "delivery",
];

const COLORS: Record<PreferenceDimension, string> = {
  communication: "#009296",
  learning: "#3882dc",
  reasoning: "#e0a030",
  collaboration: "#4aa37c",
  delivery: "#f0643c",
};

const LABELS: Record<PreferenceLocale, Record<PreferenceDimension, string>> = {
  zh: {
  communication: "沟通与呈现",
  learning: "学习与理解",
  reasoning: "分析与决策",
  collaboration: "执行与协作",
  delivery: "质量与交付",
  },
  en: {
    communication: "Communication",
    learning: "Learning",
    reasoning: "Reasoning",
    collaboration: "Collaboration",
    delivery: "Delivery",
  },
};

export function preferenceDimensionLabel(dimension: PreferenceDimension, locale: PreferenceLocale = "zh"): string {
  return LABELS[locale][dimension];
}

export function preferenceDimensionColor(dimension: PreferenceDimension): string {
  return COLORS[dimension];
}

export function preferenceEnforcementLabel(
  enforcement?: PreferenceEnforcement | string,
  locale: PreferenceLocale = "zh"
): string | null {
  if (!enforcement) return null;
  const map: Record<PreferenceLocale, Record<string, string>> = {
    zh: {
      hard_guardrail: "红线禁令",
      default_policy: "默认策略",
      soft_style: "表达风格",
    },
    en: {
      hard_guardrail: "Guardrail",
      default_policy: "Default policy",
      soft_style: "Soft style",
    },
  };
  return map[locale][enforcement] ?? null;
}

export function preferenceScopeLabel(
  scope?: PreferenceScope | string,
  locale: PreferenceLocale = "zh"
): string | null {
  if (!scope) return null;
  const map: Record<PreferenceLocale, Record<string, string>> = {
    zh: {
      global: "全局",
      project_scoped: "项目/场景级",
      task_local: "单次隔离",
    },
    en: {
      global: "Global",
      project_scoped: "Project-scoped",
      task_local: "Task-local",
    },
  };
  return map[locale][scope] ?? null;
}

export function buildPreferenceGraph(
  units: PreferenceUnit[],
  models: FusionModel[],
  selection: PreferenceDimension | "fusion" | "all",
  locale: PreferenceLocale = "zh"
): GraphData {
  const dimensionNodes = (dimensions: PreferenceDimension[]) =>
    dimensions.map((dimension) => ({
      id: `dimension:${dimension}`,
      label: LABELS[locale][dimension],
      color: COLORS[dimension],
      group: dimension,
      metadata: { kind: "dimension", dimension },
    }));

  const preferenceNodes = (items: PreferenceUnit[]) =>
    items.map((unit) => ({
      id: unit.id,
      label: unit.text,
      color: COLORS[unit.primary_category],
      group: unit.primary_category,
      metadata: { kind: "preference", unit },
    }));

  if (selection === "all") {
    const modelNodes = models.map((model) => ({
      id: `model:${model.id}`,
      label: model.title,
      color: FUSION_MODEL_COLOR,
      group: "fusion",
      metadata: { kind: "model", model },
    }));
    const modelLinks = models.flatMap((model) => {
      const modelId = `model:${model.id}`;
      const refs = model.sections?.flatMap((section) => section.guidance_refs?.map((ref) => ref.id) ?? []) ?? [];
      return [
        { source: "fusion-center", target: modelId, type: "fusion", color: FUSION_MODEL_COLOR, width: 2 },
        ...refs
          .filter((id) => units.some((unit) => unit.id === id))
          .map((id) => ({ source: modelId, target: id, type: "reference", color: FUSION_MODEL_COLOR })),
      ];
    });
    return {
      nodes: [
        ...dimensionNodes(DIMENSIONS),
        ...preferenceNodes(units),
        { id: "fusion-center", label: "融合心智模型", color: FUSION_MODEL_COLOR, group: "fusion", metadata: { kind: "fusion" } },
        ...modelNodes,
      ],
      links: [
        ...units.map((unit) => ({
          source: `dimension:${unit.primary_category}`,
          target: unit.id,
          type: "dimension",
          color: COLORS[unit.primary_category],
        })),
        ...modelLinks,
      ],
    };
  }

  if (selection !== "fusion") {
    const selected = units.filter((unit) => unit.primary_category === selection);
    const dimensionId = `dimension:${selection}`;
    return {
      nodes: [
        ...dimensionNodes([selection]),
        ...preferenceNodes(selected),
      ],
      links: selected.map((unit) => ({
        source: dimensionId,
        target: unit.id,
        type: "dimension",
        color: COLORS[selection],
      })),
    };
  }

  const centerId = "fusion-center";
  const nodes: GraphData["nodes"] = [
    { id: centerId, label: "融合心智模型", color: FUSION_MODEL_COLOR, group: "fusion", metadata: { kind: "fusion" } },
    ...models.map((model) => ({
      id: `model:${model.id}`,
      label: model.title,
      color: FUSION_MODEL_COLOR,
      group: "fusion",
      metadata: { kind: "model", model },
    })),
  ];
  const links: GraphData["links"] = models.flatMap((model) => {
    const modelId = `model:${model.id}`;
    return [{ source: centerId, target: modelId, type: "fusion", color: FUSION_MODEL_COLOR, width: 2 }];
  });
  return { nodes, links };
}
