import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import {
  AlertTriangle,
  Braces,
  Check,
  Clock3,
  GitBranch,
  LoaderCircle,
  Minus,
  Pause,
  RefreshCw,
  Settings2,
  Workflow,
  Zap,
} from "lucide-react";

import type {
  WorkflowDefinition,
  WorkflowSnapshot,
  WorkflowStepDefinition,
  WorkflowStepStatus,
} from "../api/contracts";
import { HttpVideoGateway } from "../api/http-video-gateway";

interface WorkflowDesignPageProps {
  activeJobId?: string;
}

interface WorkflowEdge {
  id: string;
  path: string;
  state: WorkflowStepStatus | "design";
  relationship: "execution" | "data";
}

const gateway = new HttpVideoGateway();

const STEP_LABELS: Record<string, string> = {
  ingest: "Ingestão",
  narrative: "Storytelling",
  speech: "Voz e alinhamento",
  scene_plan: "Planejamento de cenas e takes",
  instructional_design: "Direção instrucional",
  visual_plan: "Planejamento visual",
  whiteboard_concept_plan: "Plano didático whiteboard",
  prompt_compile: "Compilação de prompts",
  rule_validate: "Validação de regras",
  generate_images: "Geração de imagens",
  whiteboard_master: "Ilustração mestre",
  whiteboard_states: "Estados progressivos",
  visual_review: "Revisão humana",
  animate: "Animação",
  whiteboard_animate: "Transições de desenho",
  visual_qa: "QA visual",
  render: "Renderização",
  assemble: "Montagem final",
  captions: "Pacote de legendas",
};

function statusIcon(status: WorkflowStepStatus | undefined) {
  if (status === "completed") return <Check size={15} />;
  if (status === "running") return <LoaderCircle className="spin" size={15} />;
  if (status === "waiting") return <Pause size={15} />;
  if (status === "failed") return <AlertTriangle size={15} />;
  if (status === "skipped") return <Minus size={15} />;
  return <Clock3 size={15} />;
}

function conditionLabel(step: WorkflowStepDefinition): string | null {
  if (step.when === true) return null;
  if (step.when === false) return "desativado";
  if (typeof step.when === "string") return step.when;
  const source = step.when.input ?? step.when.reference ?? "condição";
  if ("equals" in step.when) return `${source} = ${String(step.when.equals)}`;
  if ("not_equals" in step.when) return `${source} ≠ ${String(step.when.not_equals)}`;
  if ("in" in step.when) return `${source} ∈ ${(step.when.in ?? []).join(", ")}`;
  if ("not_in" in step.when) return `${source} ∉ ${(step.when.not_in ?? []).join(", ")}`;
  return source;
}

export function WorkflowDesignPage({ activeJobId }: WorkflowDesignPageProps) {
  const [definitions, setDefinitions] = useState<WorkflowDefinition[]>([]);
  const [selectedId, setSelectedId] = useState("");
  const [snapshot, setSnapshot] = useState<WorkflowSnapshot | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const graphRef = useRef<HTMLDivElement | null>(null);
  const nodeRefs = useRef(new Map<string, HTMLElement>());
  const [edges, setEdges] = useState<WorkflowEdge[]>([]);
  const [graphSize, setGraphSize] = useState({ width: 0, height: 0 });

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const workflows = await gateway.getWorkflows();
      setDefinitions(workflows);
      setSelectedId((current) => current || workflows[0]?.id || "");
      if (activeJobId) {
        try {
          setSnapshot(await gateway.getWorkflowRun(activeJobId));
        } catch {
          setSnapshot(null);
        }
      } else {
        setSnapshot(null);
      }
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Não foi possível carregar o workflow.");
    } finally {
      setLoading(false);
    }
  }, [activeJobId]);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    if (!activeJobId) return;
    let cancelled = false;
    let timer: number | undefined;

    const refreshSnapshot = async () => {
      try {
        const next = await gateway.getWorkflowRun(activeJobId);
        if (cancelled) return;
        setSnapshot(next);
        setError(null);
        if (!["completed", "failed", "cancelled"].includes(next.run.status)) {
          timer = window.setTimeout(refreshSnapshot, 1500);
        }
      } catch (caught) {
        if (!cancelled) {
          setError(caught instanceof Error ? caught.message : "Não foi possível atualizar a execução.");
          timer = window.setTimeout(refreshSnapshot, 2500);
        }
      }
    };

    timer = window.setTimeout(refreshSnapshot, 700);
    return () => {
      cancelled = true;
      if (timer !== undefined) window.clearTimeout(timer);
    };
  }, [activeJobId]);

  const definition = useMemo(
    () => snapshot?.definition
      ?? definitions.find((item) => item.id === selectedId)
      ?? definitions[0],
    [definitions, selectedId, snapshot],
  );
  const runs = useMemo(
    () => new Map(snapshot?.steps.map((step) => [step.step_id, step]) ?? []),
    [snapshot],
  );
  const graphLevels = useMemo(() => {
    if (!definition) return [];
    const levels = new Map<string, number>();
    const steps = new Map(definition.steps.map((step) => [step.id, step]));
    const levelFor = (step: WorkflowStepDefinition): number => {
      const known = levels.get(step.id);
      if (known !== undefined) return known;
      const level = step.needs.length === 0
        ? 0
        : Math.max(...step.needs.map((dependency) => levelFor(steps.get(dependency)!))) + 1;
      levels.set(step.id, level);
      return level;
    };
    for (const step of definition.steps) levelFor(step);
    const columns: WorkflowStepDefinition[][] = [];
    for (const step of definition.steps) {
      const level = levels.get(step.id) ?? 0;
      (columns[level] ??= []).push(step);
    }
    return columns;
  }, [definition]);

  useLayoutEffect(() => {
    const graph = graphRef.current;
    if (!graph || !definition) return;
    const drawEdges = () => {
      const graphBounds = graph.getBoundingClientRect();
      const nextEdges: WorkflowEdge[] = [];
      const stepsById = new Map(definition.steps.map((step) => [step.id, step]));
      const isAncestor = (ancestorId: string, stepId: string, visited = new Set<string>()): boolean => {
        if (ancestorId === stepId) return true;
        if (visited.has(stepId)) return false;
        visited.add(stepId);
        return (stepsById.get(stepId)?.needs ?? []).some(
          (dependency) => isAncestor(ancestorId, dependency, new Set(visited)),
        );
      };
      let longEdgeIndex = 0;
      for (const target of definition.steps) {
        const targetElement = nodeRefs.current.get(target.id);
        if (!targetElement) continue;
        const targetBounds = targetElement.getBoundingClientRect();
        for (const sourceId of target.needs) {
          const sourceElement = nodeRefs.current.get(sourceId);
          if (!sourceElement) continue;
          const sourceBounds = sourceElement.getBoundingClientRect();
          const startX = sourceBounds.right - graphBounds.left;
          const startY = sourceBounds.top + sourceBounds.height / 2 - graphBounds.top;
          const endX = targetBounds.left - graphBounds.left;
          const endY = targetBounds.top + targetBounds.height / 2 - graphBounds.top;
          const horizontalDistance = endX - startX;
          const isLongEdge = horizontalDistance > 390;
          const path = isLongEdge
            ? (() => {
                const exitX = startX + 22;
                const entryX = endX - 22;
                const railY = 18 + (longEdgeIndex++ % 8) * 8;
                return `M ${startX} ${startY} L ${exitX} ${startY} Q ${exitX + 8} ${startY} ${exitX + 8} ${startY - 8} L ${exitX + 8} ${railY + 8} Q ${exitX + 8} ${railY} ${exitX + 16} ${railY} L ${entryX - 16} ${railY} Q ${entryX - 8} ${railY} ${entryX - 8} ${railY + 8} L ${entryX - 8} ${endY - 8} Q ${entryX - 8} ${endY} ${entryX} ${endY} L ${endX} ${endY}`;
              })()
            : (() => {
                const bend = Math.max(horizontalDistance * 0.48, 24);
                return `M ${startX} ${startY} C ${startX + bend} ${startY}, ${endX - bend} ${endY}, ${endX} ${endY}`;
              })();
          nextEdges.push({
            id: `${sourceId}-${target.id}`,
            path,
            state: runs.get(target.id)?.status ?? "design",
            relationship: target.needs.some(
              (otherDependency) => (
                otherDependency !== sourceId
                && isAncestor(sourceId, otherDependency)
              ),
            ) ? "data" : "execution",
          });
        }
      }
      setGraphSize({ width: graph.scrollWidth, height: graph.scrollHeight });
      setEdges(nextEdges);
    };
    const observer = new ResizeObserver(drawEdges);
    observer.observe(graph);
    for (const element of nodeRefs.current.values()) observer.observe(element);
    drawEdges();
    window.addEventListener("resize", drawEdges);
    return () => {
      observer.disconnect();
      window.removeEventListener("resize", drawEdges);
    };
  }, [definition, graphLevels, runs]);

  return (
    <main className="workflow-page">
      <section className="workflow-hero">
        <div>
          <div className="eyebrow"><Workflow size={17} /> Orquestração declarativa</div>
          <h1>Design do workflow</h1>
          <p>Veja dependências, políticas de execução, checkpoint humano e o estado persistido de cada etapa.</p>
        </div>
        <div className="workflow-toolbar">
          {definitions.length > 1 && (
            <select value={selectedId} onChange={(event) => setSelectedId(event.target.value)}>
              {definitions.map((item) => <option key={item.id} value={item.id}>{item.id}</option>)}
            </select>
          )}
          <button type="button" onClick={() => void load()} disabled={loading}>
            <RefreshCw className={loading ? "spin" : ""} size={16} /> Atualizar
          </button>
        </div>
      </section>

      {error && <div className="workflow-error"><AlertTriangle size={18} />{error}</div>}
      {loading && !definition && <div className="workflow-loading"><LoaderCircle className="spin" />Carregando desenho...</div>}

      {definition && (
        <>
          <section className="workflow-summary">
            <div><span>Workflow</span><strong>{definition.id}</strong><small>{definition.description}</small></div>
            <div><span>Versão</span><strong>{definition.version}</strong><small>Congelada por execução</small></div>
            <div><span>Etapas</span><strong>{definition.steps.length}</strong><small>{definition.steps.filter((step) => step.checkpoint).length} checkpoint humano</small></div>
            <div>
              <span>Estado</span>
              <strong className={`run-state run-state--${snapshot?.run.status ?? "definition"}`}>
                {snapshot?.run.status ?? "somente design"}
              </strong>
              <small>{activeJobId ? `Job ${activeJobId.slice(0, 8)}…` : "Nenhum job ativo"}</small>
            </div>
          </section>

          <section className="workflow-dag" aria-label="Diagrama condicional do workflow">
            <header className="workflow-dag__heading">
              <div>
                <span><GitBranch size={16} /> DAG completo</span>
                <h2>Etapas, decisões e convergências</h2>
              </div>
              <small>Role horizontalmente para acompanhar o fluxo. Condições ficam visíveis dentro de cada ramificação.</small>
            </header>
            <div className="workflow-dag__viewport">
              <div className="workflow-dag__columns" ref={graphRef}>
                <svg
                  className="workflow-dag__edges"
                  width={graphSize.width}
                  height={graphSize.height}
                  viewBox={`0 0 ${graphSize.width} ${graphSize.height}`}
                  aria-hidden="true"
                >
                  <defs>
                    <marker id="workflow-arrow" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto" markerUnits="strokeWidth">
                      <path d="M 0 0 L 8 4 L 0 8 z" />
                    </marker>
                  </defs>
                  {edges.map((edge) => (
                    <path
                      className={`workflow-dag__edge workflow-dag__edge--${edge.state} workflow-dag__edge--${edge.relationship}`}
                      d={edge.path}
                      key={edge.id}
                      markerEnd="url(#workflow-arrow)"
                    />
                  ))}
                </svg>
                {graphLevels.map((column, columnIndex) => (
                  <section className="workflow-dag__column" key={columnIndex}>
                    <span className="workflow-dag__level">nível {columnIndex + 1}</span>
                    <div className="workflow-dag__stack">
                      {column.map((step) => {
                        const run = runs.get(step.id);
                        const status = run?.status;
                        const condition = conditionLabel(step);
                        const stepIndex = definition.steps.findIndex((item) => item.id === step.id);
                        return (
                          <article
                            className={`workflow-dag-node workflow-dag-node--${status ?? "design"}${step.checkpoint ? " workflow-dag-node--checkpoint" : ""}${condition ? " workflow-dag-node--conditional" : ""}`}
                            key={step.id}
                            ref={(element) => {
                              if (element) nodeRefs.current.set(step.id, element);
                              else nodeRefs.current.delete(step.id);
                            }}
                          >
                            {columnIndex > 0 && <span className="workflow-dag-node__inlet" aria-hidden="true" />}
                            {columnIndex < graphLevels.length - 1 && <span className="workflow-dag-node__outlet" aria-hidden="true" />}
                            {condition && <div className="workflow-condition"><GitBranch size={12} /><span>quando</span><strong>{condition}</strong></div>}
                            <header>
                              <span className="workflow-node__index">{String(stepIndex + 1).padStart(2, "0")}</span>
                              <div><small>{step.uses}</small><h3>{STEP_LABELS[step.id] ?? step.id.replaceAll("_", " ")}</h3></div>
                            </header>
                            <span className={`workflow-status workflow-status--${status ?? "design"}`}>{statusIcon(status)} {status ?? "design"}</span>
                            <div className="workflow-dag-node__dependencies">
                              {step.needs.map((dependency) => <span key={dependency}>← {STEP_LABELS[dependency] ?? dependency}</span>)}
                            </div>
                            <div className="workflow-node__meta">
                              {typeof step.config.provider === "string" && <span className="provider-chip"><Settings2 size={13} /> {step.config.provider}</span>}
                              {step.checkpoint && <span className="checkpoint-chip"><Pause size={13} /> aprovação</span>}
                              {step.parallelism > 1 && <span><Zap size={13} /> ×{step.parallelism}</span>}
                            </div>
                            {Object.keys(step.config).length > 0 && (
                              <details className="workflow-node__config">
                                <summary><Braces size={14} /> Configuração</summary>
                                <pre>{JSON.stringify(step.config, null, 2)}</pre>
                              </details>
                            )}
                          </article>
                        );
                      })}
                    </div>
                  </section>
                ))}
              </div>
            </div>
          </section>

          <section className="workflow-legend">
            <span><i className="legend-line legend-line--execution" />Ordem de execução</span>
            <span><i className="legend-line legend-line--data" />Consumo de dados</span>
            <span><i className="legend-dot legend-dot--running" />Em execução</span>
            <span><i className="legend-dot legend-dot--waiting" />Aguardando aprovação</span>
            <span><i className="legend-dot legend-dot--completed" />Concluído</span>
            <span><i className="legend-dot legend-dot--failed" />Falhou</span>
            <small>Definição: <code>workflows/{definition.id}.yaml</code></small>
          </section>
        </>
      )}
    </main>
  );
}
