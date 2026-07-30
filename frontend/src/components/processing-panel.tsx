import { useEffect, useState } from "react";
import {
  AudioLines,
  Check,
  CircleDashed,
  Clapperboard,
  FileScan,
  FileText,
  LoaderCircle,
  Sparkles,
} from "lucide-react";

import type { JobStatus, VideoJob } from "../api/contracts";

interface ProcessingPanelProps {
  job: VideoJob;
  error?: string | null;
}

const STEPS: Array<{ status: JobStatus; label: string; description: string; icon: typeof FileScan }> = [
  { status: "ingesting", label: "Lendo o documento", description: "Extração de páginas, conteúdo e imagens", icon: FileScan },
  { status: "scripting", label: "Criando a narrativa", description: "Síntese do conteúdo em um arco de storytelling", icon: Sparkles },
  { status: "duration_validating", label: "Validando a duração", description: "Comparação entre tempo solicitado e narração planejada", icon: AudioLines },
  { status: "synthesizing", label: "Gerando a voz", description: "Síntese do áudio por cena", icon: AudioLines },
  { status: "scene_planning", label: "Planejando cenas e takes", description: "Timeline aderente ao áudio, com takes de até 8 segundos", icon: Clapperboard },
  { status: "visual_planning", label: "Dirigindo os visuais", description: "Classificação entre conteúdo animável e informacional estático", icon: Sparkles },
  { status: "prompt_compiling", label: "Compilando prompts", description: "Continuidade e instruções específicas para cada take", icon: Sparkles },
  { status: "rule_validating", label: "Validando as regras", description: "Cobertura do áudio, duração e restrições de mídia", icon: FileScan },
  { status: "generating_images", label: "Gerando as imagens", description: "Criação dos frames-base para revisão", icon: Sparkles },
  { status: "generating_video", label: "Criando os clipes", description: "Movimento apenas nas cenas sem texto; slides permanecem fixos", icon: Clapperboard },
  { status: "visual_qa", label: "Validando os clipes", description: "Verificação dos artefatos antes da composição", icon: FileScan },
  { status: "rendering", label: "Renderizando as cenas", description: "Composição dos visuais e do áudio", icon: Clapperboard },
  { status: "assembling", label: "Finalizando o vídeo", description: "Montagem e exportação em MP4", icon: FileText },
  { status: "captioning", label: "Gerando legendas", description: "Exportação dos arquivos WebVTT e SRT", icon: FileText },
];

const ORDER: JobStatus[] = [
  "received",
  "ingesting",
  "scripting",
  "duration_validating",
  "awaiting_duration_approval",
  "synthesizing",
  "scene_planning",
  "visual_planning",
  "prompt_compiling",
  "rule_validating",
  "generating_images",
  "awaiting_visual_approval",
  "generating_video",
  "visual_qa",
  "rendering",
  "assembling",
  "captioning",
  "completed",
];

function stepState(step: JobStatus, current: JobStatus): "pending" | "active" | "done" {
  const stepIndex = ORDER.indexOf(step);
  const currentIndex = ORDER.indexOf(current);
  if (current === "completed" || currentIndex > stepIndex) return "done";
  if (current === step) return "active";
  return "pending";
}

function elapsedLabel(seconds: number): string {
  if (seconds < 10) return "agora";
  if (seconds < 60) return `há ${seconds}s`;
  return `há ${Math.floor(seconds / 60)} min`;
}

export function ProcessingPanel({ job, error }: ProcessingPanelProps) {
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 10_000);
    return () => window.clearInterval(timer);
  }, []);

  const secondsSinceUpdate = Math.max(
    0,
    Math.floor((now - new Date(job.updated_at).getTime()) / 1_000),
  );
  const appearsStalled = secondsSinceUpdate >= 120;

  return (
    <div className="processing-panel">
      <div className="processing-orbit" aria-hidden="true">
        <div className="processing-orbit__inner"><LoaderCircle size={31} /></div>
      </div>
      <div className="processing-title">
        <span>Produzindo seu vídeo</span>
        <h2>{job.file_name}</h2>
        <p>As páginas são sintetizadas em uma narrativa; o número de cenas é definido pelo storytelling.</p>
        {job.debug_mode && <p><strong>Modo debug:</strong> execução local, determinística e sem chamadas pagas.</p>}
      </div>

      <div className="progress-block">
        <div className="progress-label"><span>Progresso geral</span><strong>{job.progress_percent}%</strong></div>
        <div className="progress-track"><span style={{ width: `${job.progress_percent}%` }} /></div>
        {job.detail && <p className="processing-detail">Etapa atual: {job.detail}</p>}
        <p className={`processing-heartbeat${appearsStalled ? " processing-heartbeat--stale" : ""}`}>
          Última atualização {elapsedLabel(secondsSinceUpdate)}
          {appearsStalled && " — esta etapa está demorando mais que o esperado; verifique os logs."}
        </p>
      </div>

      {error && <div className="processing-error" role="alert">Falha ao consultar o job: {error}</div>}

      <div className="timeline">
        {STEPS.map((step) => {
          const state = stepState(step.status, job.status);
          const Icon = step.icon;
          return (
            <div className={`timeline-step timeline-step--${state}`} key={step.status}>
              <div className="timeline-step__marker">
                {state === "done" ? <Check size={15} /> : state === "active" ? <LoaderCircle size={16} /> : <CircleDashed size={16} />}
              </div>
              <div className="timeline-step__icon"><Icon size={18} /></div>
              <div className="timeline-step__text"><strong>{step.label}</strong><span>{step.description}</span></div>
            </div>
          );
        })}
      </div>

      <div className="processing-meta">
        <span>Job <code>{job.job_id.slice(0, 8)}</code></span>
        <span>Meta: {Math.round(job.target_seconds / 60)} min</span>
        <span>
          Formato: {job.production_mode === "cinematic_story"
            ? "história cinematográfica"
            : job.production_mode === "whiteboard_explainer"
              ? "whiteboard explicativo"
              : job.production_mode === "corporate_training"
                ? "treinamento corporativo"
            : "apresentação híbrida"}
        </span>
      </div>
    </div>
  );
}
