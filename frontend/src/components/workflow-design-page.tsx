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
  ProductionMode,
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
  content_audit: "Auditoria de conteúdo",
  narrative: "Storytelling",
  content_coverage: "Cobertura do conteúdo",
  duration_validate: "Validação da duração",
  duration_review: "Aprovação da duração",
  speech: "Voz e alinhamento",
  scene_plan: "Planejamento de cenas e takes",
  instructional_design: "Direção instrucional",
  visual_plan: "Planejamento visual",
  whiteboard_concept_plan: "Plano didático whiteboard",
  prompt_compile: "Compilação de prompts",
  rule_validate: "Validação de regras",
  character_references: "Folhas de personagens",
  storyboard: "Storyboard mestre e recortes",
  generate_images: "Geração de imagens",
  whiteboard_master: "Ilustração mestre",
  whiteboard_states: "Estados progressivos",
  visual_review: "Revisão humana",
  animate: "Animação",
  storyboard_animate: "Roteamento multi-shot",
  whiteboard_animate: "Transições de desenho",
  visual_qa: "QA visual",
  render: "Renderização",
  assemble: "Montagem final",
  captions: "Pacote de legendas",
};

const STEP_DESCRIPTIONS: Record<string, string> = {
  ingest: "Lê o PDF ou PPTX, extrai páginas, textos, notas e imagens que alimentarão o restante do fluxo.",
  content_audit: "Identifica regras, prazos, números, tabelas e outras informações críticas que não podem desaparecer durante o resumo.",
  narrative: "Transforma o documento em uma história coerente, define a progressão narrativa e escreve o texto falado de cada cena.",
  content_coverage: "Confere se todas as informações obrigatórias do documento continuam representadas no roteiro.",
  duration_validate: "Compara o tamanho do roteiro com a duração solicitada e calcula se o conteúdo cabe no tempo disponível.",
  duration_review: "Pausa o processamento quando a duração precisa da decisão do usuário: resumir ou aceitar um vídeo maior.",
  speech: "Gera a narração ou as falas dos personagens e mede o áudio real para alinhar a duração das cenas.",
  scene_plan: "Divide o roteiro em cenas e takes, define o tempo de cada trecho e detecta conteúdos que devem permanecer estáticos.",
  instructional_design: "Classifica cenas de treinamento como conceito, processo, regra, comportamento, demonstração ou resumo e escolhe o tratamento adequado.",
  visual_plan: "Converte cada trecho do roteiro em direção visual, composição, personagens, continuidade e movimentos de câmera.",
  whiteboard_concept_plan: "Organiza a explicação de whiteboard em ideias didáticas cumulativas e em uma ordem clara de desenho.",
  prompt_compile: "Transforma roteiro e direção visual em prompts completos por take, preservando continuidade e evitando repetição de ações.",
  rule_validate: "Valida duração máxima, cobertura do áudio, restrições do formato e consistência dos prompts antes de gastar com geração.",
  character_references: "Cria folhas de referência dos personagens recorrentes para manter rosto, roupa e identidade consistentes no storyboard.",
  storyboard: "Gera o storyboard cinematográfico mestre e prepara seus painéis para revisão ou animação pelo modelo selecionado.",
  generate_images: "Cria as imagens aprováveis de cada cena ou take usando o planejamento visual e a identidade da apresentação.",
  whiteboard_master: "Gera a ilustração final completa do quadro branco que servirá como referência imutável para toda a animação.",
  whiteboard_states: "Cria estados intermediários determinísticos, do quadro vazio até a ilustração completa, sem inventar novos elementos.",
  visual_review: "Pausa o fluxo para o usuário aprovar, editar, regenerar ou substituir as imagens antes da animação.",
  animate: "Anima individualmente as imagens aprovadas para os formatos que usam takes convencionais.",
  storyboard_animate: "Escolhe entre enviar o storyboard inteiro para um modelo multi-shot ou recortar e animar seus painéis separadamente.",
  whiteboard_animate: "Anima cada transição entre estados progressivos do desenho, preservando tudo que já estava no quadro.",
  visual_qa: "Verifica se os vídeos gerados existem, têm duração válida e podem seguir para composição; falhas retornam ao take afetado.",
  render: "Combina o visual de cada cena com seu áudio, aplica duração, enquadramento e elementos de identidade visual.",
  assemble: "Concatena todas as cenas na ordem correta e acrescenta abertura, encerramento e watermark quando configurados.",
  captions: "Gera os arquivos de legenda VTT e SRT sincronizados com o áudio do vídeo final.",
};

const VISUAL_FORMATS: { value: ProductionMode; label: string }[] = [
  { value: "hybrid_presentation", label: "Apresentação híbrida" },
  { value: "cinematic_story", label: "História cinematográfica" },
  { value: "whiteboard_explainer", label: "Whiteboard explicativo" },
  { value: "corporate_training", label: "Treinamento corporativo" },
];

const WORKFLOW_LABELS: Record<string, string> = {
  "presentation-video": "Replicate",
  "presentation-video-pydantic-ai": "Vertex AI + Pydantic AI",
  "presentation-video-seedance-fast": "Seedance 2.0 Fast",
};

function matchesVisualFormat(step: WorkflowStepDefinition, format: ProductionMode): boolean {
  if (step.when === true) return true;
  if (step.when === false) return false;
  if (typeof step.when === "string") return true;
  const source = step.when.input ?? step.when.reference;
  if (source !== "production_mode") return true;
  if ("equals" in step.when) return step.when.equals === format;
  if ("not_equals" in step.when) return step.when.not_equals !== format;
  if ("in" in step.when) return (step.when.in ?? []).includes(format);
  if ("not_in" in step.when) return !(step.when.not_in ?? []).includes(format);
  return true;
}

function isVisualFormatCondition(step: WorkflowStepDefinition): boolean {
  return typeof step.when === "object"
    && (step.when.input ?? step.when.reference) === "production_mode";
}

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
  const [selectedFormat, setSelectedFormat] = useState<ProductionMode>("hybrid_presentation");
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
      if (activeJobId) {
        try {
          const nextSnapshot = await gateway.getWorkflowRun(activeJobId);
          setSnapshot(nextSnapshot);
          setSelectedId(nextSnapshot.run.workflow_id);
          const jobFormat = nextSnapshot.run.inputs.production_mode;
          if (VISUAL_FORMATS.some((item) => item.value === jobFormat)) {
            setSelectedFormat(jobFormat as ProductionMode);
          }
        } catch {
          setSnapshot(null);
          setSelectedId((current) => current || workflows[0]?.id || "");
        }
      } else {
        setSnapshot(null);
        setSelectedId((current) => current || workflows[0]?.id || "");
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

  const snapshotMatchesSelection = snapshot?.run.workflow_id === selectedId
    && snapshot.run.inputs.production_mode === selectedFormat;
  const definition = useMemo(
    () => (snapshot?.run.workflow_id === selectedId ? snapshot.definition : null)
      ?? definitions.find((item) => item.id === selectedId)
      ?? definitions[0],
    [definitions, selectedId, snapshot],
  );
  const visibleSteps = useMemo(
    () => definition?.steps.filter((step) => matchesVisualFormat(step, selectedFormat)) ?? [],
    [definition, selectedFormat],
  );
  const runs = useMemo(
    () => new Map(
      snapshotMatchesSelection
        ? snapshot?.steps.map((step) => [step.step_id, step]) ?? []
        : [],
    ),
    [snapshot, snapshotMatchesSelection],
  );
  const graphLevels = useMemo(() => {
    if (!definition) return [];
    const levels = new Map<string, number>();
    const steps = new Map(visibleSteps.map((step) => [step.id, step]));
    const levelFor = (step: WorkflowStepDefinition): number => {
      const known = levels.get(step.id);
      if (known !== undefined) return known;
      const activeDependencies = step.needs.filter((dependency) => steps.has(dependency));
      const level = activeDependencies.length === 0
        ? 0
        : Math.max(...activeDependencies.map((dependency) => levelFor(steps.get(dependency)!))) + 1;
      levels.set(step.id, level);
      return level;
    };
    for (const step of visibleSteps) levelFor(step);
    const columns: WorkflowStepDefinition[][] = [];
    for (const step of visibleSteps) {
      const level = levels.get(step.id) ?? 0;
      (columns[level] ??= []).push(step);
    }
    return columns;
  }, [definition, visibleSteps]);

  useLayoutEffect(() => {
    const graph = graphRef.current;
    if (!graph || !definition) return;
    const drawEdges = () => {
      const graphBounds = graph.getBoundingClientRect();
      const nextEdges: WorkflowEdge[] = [];
      const stepsById = new Map(visibleSteps.map((step) => [step.id, step]));
      const activeNeeds = (step: WorkflowStepDefinition) => (
        step.needs.filter((dependency) => stepsById.has(dependency))
      );
      const isAncestor = (ancestorId: string, stepId: string, visited = new Set<string>()): boolean => {
        if (ancestorId === stepId) return true;
        if (visited.has(stepId)) return false;
        visited.add(stepId);
        return (stepsById.get(stepId) ? activeNeeds(stepsById.get(stepId)!) : []).some(
          (dependency) => isAncestor(ancestorId, dependency, new Set(visited)),
        );
      };
      let longEdgeIndex = 0;
      for (const target of visibleSteps) {
        const targetElement = nodeRefs.current.get(target.id);
        if (!targetElement) continue;
        const targetBounds = targetElement.getBoundingClientRect();
        for (const sourceId of activeNeeds(target)) {
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
            relationship: activeNeeds(target).some(
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
  }, [definition, graphLevels, runs, visibleSteps]);

  return (
    <main className="workflow-page">
      <section className="workflow-hero">
        <div>
          <div className="eyebrow"><Workflow size={17} /> Orquestração declarativa</div>
          <h1>Design do workflow</h1>
          <p>Veja dependências, políticas de execução, checkpoint humano e o estado persistido de cada etapa.</p>
        </div>
        <div className="workflow-toolbar">
          <label>
            <span>Formato visual</span>
            <select
              value={selectedFormat}
              onChange={(event) => setSelectedFormat(event.target.value as ProductionMode)}
            >
              {VISUAL_FORMATS.map((item) => (
                <option key={item.value} value={item.value}>{item.label}</option>
              ))}
            </select>
          </label>
          <label>
            <span>Workflow</span>
            <select value={selectedId} onChange={(event) => setSelectedId(event.target.value)}>
              {definitions.map((item) => (
                <option key={item.id} value={item.id}>
                  {WORKFLOW_LABELS[item.id] ?? item.id}
                </option>
              ))}
            </select>
          </label>
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
            <div>
              <span>Etapas deste formato</span>
              <strong>{visibleSteps.length}</strong>
              <small>
                de {definition.steps.length} no YAML · {visibleSteps.filter((step) => step.checkpoint).length} checkpoint humano
              </small>
            </div>
            <div>
              <span>Estado</span>
              <strong className={`run-state run-state--${snapshotMatchesSelection ? snapshot?.run.status : "definition"}`}>
                {snapshotMatchesSelection ? snapshot?.run.status : "somente design"}
              </strong>
              <small>
                {snapshotMatchesSelection && activeJobId
                  ? `Job ${activeJobId.slice(0, 8)}…`
                  : "Nenhuma execução para esta seleção"}
              </small>
            </div>
          </section>

          <section className="workflow-dag" aria-label="Diagrama condicional do workflow">
            <header className="workflow-dag__heading">
              <div>
                <span><GitBranch size={16} /> DAG completo</span>
                <h2>{VISUAL_FORMATS.find((item) => item.value === selectedFormat)?.label}</h2>
              </div>
              <small>Somente as etapas executáveis para este formato são exibidas. Passe o mouse sobre um card para entender sua função.</small>
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
                        const condition = isVisualFormatCondition(step)
                          ? null
                          : conditionLabel(step);
                        const stepIndex = visibleSteps.findIndex((item) => item.id === step.id);
                        const visibleDependencies = step.needs.filter((dependency) => (
                          visibleSteps.some((item) => item.id === dependency)
                        ));
                        const description = step.description
                          || STEP_DESCRIPTIONS[step.id]
                          || `Executa a etapa ${step.uses} dentro deste workflow.`;
                        return (
                          <article
                            className={`workflow-dag-node workflow-dag-node--${status ?? "design"}${step.checkpoint ? " workflow-dag-node--checkpoint" : ""}${condition ? " workflow-dag-node--conditional" : ""}`}
                            data-tooltip={description}
                            aria-label={`${STEP_LABELS[step.id] ?? step.id}: ${description}`}
                            tabIndex={0}
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
                              {visibleDependencies.map((dependency) => <span key={dependency}>← {STEP_LABELS[dependency] ?? dependency}</span>)}
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
