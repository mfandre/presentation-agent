import { useCallback, useEffect, useMemo, useState } from "react";
import {
  AlertTriangle,
  Braces,
  Check,
  Clock3,
  GitBranch,
  LoaderCircle,
  Pause,
  RefreshCw,
  RotateCcw,
  Settings2,
  Workflow,
  Zap,
} from "lucide-react";

import type {
  WorkflowDefinition,
  WorkflowSnapshot,
  WorkflowStepStatus,
} from "../api/contracts";
import { HttpVideoGateway } from "../api/http-video-gateway";

interface WorkflowDesignPageProps {
  activeJobId?: string;
}

const gateway = new HttpVideoGateway();

const STEP_LABELS: Record<string, string> = {
  ingest: "Ingestão",
  narrative: "Storytelling",
  speech: "Voz e alinhamento",
  scene_plan: "Planejamento de cenas e takes",
  visual_plan: "Planejamento visual",
  prompt_compile: "Compilação de prompts",
  rule_validate: "Validação de regras",
  generate_images: "Geração de imagens",
  visual_review: "Revisão humana",
  animate: "Animação",
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
  return <Clock3 size={15} />;
}

export function WorkflowDesignPage({ activeJobId }: WorkflowDesignPageProps) {
  const [definitions, setDefinitions] = useState<WorkflowDefinition[]>([]);
  const [selectedId, setSelectedId] = useState("");
  const [snapshot, setSnapshot] = useState<WorkflowSnapshot | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

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

          <section className="workflow-canvas" aria-label="Grafo do workflow">
            <div className="workflow-lane">
              {definition.steps.map((step, index) => {
                const run = runs.get(step.id);
                const status = run?.status;
                return (
                  <div className="workflow-node-wrap" key={step.id}>
                    {index > 0 && (
                      <div className="workflow-connector" aria-hidden="true">
                        <span />
                        <small>{step.needs.length > 1 ? `${step.needs.length} dependências` : ""}</small>
                      </div>
                    )}
                    <article className={`workflow-node workflow-node--${status ?? "design"}${step.checkpoint ? " workflow-node--checkpoint" : ""}`}>
                      <header>
                        <span className="workflow-node__index">{String(index + 1).padStart(2, "0")}</span>
                        <div>
                          <small>{step.uses}</small>
                          <h2>{STEP_LABELS[step.id] ?? step.id.replaceAll("_", " ")}</h2>
                        </div>
                        <span className={`workflow-status workflow-status--${status ?? "design"}`}>
                          {statusIcon(status)} {status ?? "design"}
                        </span>
                      </header>
                      <div className="workflow-node__meta">
                        {typeof step.config.provider === "string" && (
                          <span className="provider-chip"><Settings2 size={14} /> {step.config.provider}</span>
                        )}
                        {typeof step.config.model === "string" && (
                          <span className="model-chip">{step.config.model}</span>
                        )}
                        {step.needs.length > 0 && <span><GitBranch size={14} /> após {step.needs.join(", ")}</span>}
                        <span><RotateCcw size={14} /> {step.retry.attempts} tentativa{step.retry.attempts === 1 ? "" : "s"}</span>
                        {step.parallelism > 1 && <span><Zap size={14} /> paralelo ×{step.parallelism}</span>}
                        {step.checkpoint && <span className="checkpoint-chip"><Pause size={14} /> aprovação humana</span>}
                      </div>
                      {Object.keys(step.config).length > 0 && (
                        <details className="workflow-node__config">
                          <summary><Braces size={14} /> Configuração do step</summary>
                          <pre>{JSON.stringify(step.config, null, 2)}</pre>
                        </details>
                      )}
                      {run && (
                        <footer>
                          <span>Tentativa registrada: {run.attempt}</span>
                          {run.error && <strong>{run.error}</strong>}
                        </footer>
                      )}
                    </article>
                  </div>
                );
              })}
            </div>
          </section>

          <section className="workflow-legend">
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
