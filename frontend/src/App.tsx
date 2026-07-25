import { BrainCircuit, Braces, ShieldCheck } from "lucide-react";

import { ConfigurationForm } from "./components/configuration-form";
import { EmptyPreview } from "./components/empty-preview";
import { ErrorPanel } from "./components/error-panel";
import { ProcessingPanel } from "./components/processing-panel";
import { ResultPanel } from "./components/result-panel";
import { VisualReviewPanel } from "./components/visual-review-panel";
import { useVideoCreation } from "./hooks/use-video-creation";

export default function App() {
  const { job, debugMode, debugMaxScenes, isSubmitting, isActing, error, createVideo, regenerateScene, approveVisuals, resumeVideo, reset, assetUrl } = useVideoCreation();
  const isRunning = Boolean(job && !["completed", "failed"].includes(job.status));

  return (
    <div className="app-shell">
      <header className="app-header">
        <a className="brand" href="/" aria-label="Narrativa AI — início">
          <span className="brand__mark"><BrainCircuit size={22} /></span>
          <span><strong>Narrativa</strong><small>AI Studio</small></span>
        </a>
        <div className={`header-status${debugMode ? " header-status--debug" : ""}`}><span className="status-dot" /> {debugMode ? "Modo debug · sem APIs pagas" : "MVP conectado à API"}</div>
        <a className="header-link" href="/docs" target="_blank" rel="noreferrer"><Braces size={17} /> <span>API Docs</span></a>
      </header>

      {debugMode && (
        <aside className="debug-banner" role="status">
          <ShieldCheck size={20} />
          <div>
            <strong>Modo debug ativo — nenhum crédito de IA será consumido.</strong>
            <span>Roteiro e planejamento são locais e determinísticos; as páginas originais, eSpeak e FFmpeg substituem Replicate, Pydantic AI e TTS generativo{debugMaxScenes ? `, com no máximo ${debugMaxScenes} cenas por teste` : ""}.</span>
          </div>
        </aside>
      )}

      <main>
        <section className="hero">
          <div className="eyebrow"><SparkleMark /> IA generativa para apresentações</div>
          <h1>Do slide ao vídeo.<br /><span>Com narrativa de verdade.</span></h1>
          <p>Envie um PowerPoint ou PDF. A plataforma entende o conteúdo, escreve o roteiro, gera a voz e monta o vídeo cena a cena.</p>
        </section>

        <section className="studio-grid">
          <aside className="configuration-card">
            <ConfigurationForm
              disabled={isSubmitting || isRunning}
              isResuming={isActing}
              onSubmit={createVideo}
              onResume={resumeVideo}
            />
          </aside>
          <section className="workspace-card" aria-live="polite">
            {!job && !error && <EmptyPreview />}
            {job && !["awaiting_visual_approval", "completed", "failed"].includes(job.status) && <ProcessingPanel job={job} error={error} />}
            {job?.status === "awaiting_visual_approval" && (
              <VisualReviewPanel job={job} isActing={isActing} error={error} assetUrl={assetUrl} onRegenerate={regenerateScene} onApprove={approveVisuals} />
            )}
            {job?.status === "completed" && <ResultPanel job={job} assetUrl={assetUrl} onReset={reset} />}
            {!job && error && <ErrorPanel message={error} onReset={reset} />}
            {job?.status === "failed" && <ErrorPanel message={error || job.detail || "Erro inesperado."} isActing={isActing} onResume={resumeVideo} onReset={reset} />}
          </section>
        </section>

        <section className="trust-strip">
          <div><ShieldCheck size={18} /><span><strong>Arquitetura desacoplada</strong>Ports & Adapters no backend e gateway no frontend.</span></div>
          <div><BrainCircuit size={18} /><span><strong>Modelo substituível</strong>Pydantic AI mantém o LLM fora do domínio.</span></div>
          <div><SparkleMark /><span><strong>Processamento modular</strong>Roteiro, áudio e vídeo são processados por cena narrativa.</span></div>
        </section>
      </main>

      <footer><span>Narrativa AI · Starter proprietário</span><span>React + TypeScript · FastAPI + Pydantic AI</span></footer>
    </div>
  );
}

function SparkleMark() {
  return (
    <svg width="17" height="17" viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <path d="M12 2L13.45 8.55L20 10L13.45 11.45L12 18L10.55 11.45L4 10L10.55 8.55L12 2Z" fill="currentColor" />
      <path d="M19 16L19.65 18.35L22 19L19.65 19.65L19 22L18.35 19.65L16 19L18.35 18.35L19 16Z" fill="currentColor" />
    </svg>
  );
}
