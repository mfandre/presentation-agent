import { useState } from "react";
import {
  CheckCircle2,
  Clapperboard,
  FileText,
  Image,
  LoaderCircle,
  PencilLine,
  RefreshCw,
  Sparkles,
} from "lucide-react";

import type { VideoJob } from "../api/contracts";

interface VisualReviewPanelProps {
  job: VideoJob;
  isActing: boolean;
  error: string | null;
  assetUrl(path: string): string;
  onRegenerate(sceneNumber: number, prompt: string): Promise<void>;
  onApprove(): Promise<void>;
}

function formatSourcePages(pages: number[]): string {
  if (pages.length === 0) return "não informadas";
  const sorted = [...new Set(pages)].sort((left, right) => left - right);
  const ranges: string[] = [];
  let start = sorted[0];
  let end = sorted[0];
  for (const page of sorted.slice(1)) {
    if (page === end + 1) {
      end = page;
      continue;
    }
    ranges.push(start === end ? String(start) : `${start}–${end}`);
    start = page;
    end = page;
  }
  ranges.push(start === end ? String(start) : `${start}–${end}`);
  return ranges.join(", ");
}

export function VisualReviewPanel({
  job,
  isActing,
  error,
  assetUrl,
  onRegenerate,
  onApprove,
}: VisualReviewPanelProps) {
  const [editedPrompts, setEditedPrompts] = useState<Record<number, string>>({});
  const [openEditors, setOpenEditors] = useState<Record<number, boolean>>(() => {
    const firstScene = job.scene_images.find((scene) => scene.media_mode === "video")?.scene_number;
    return firstScene ? { [firstScene]: true } : {};
  });

  const staticCount = job.scene_images.filter((scene) => scene.media_mode === "static").length;
  const videoCount = job.scene_images.length - staticCount;

  const toggleEditor = (sceneNumber: number) => {
    setOpenEditors((current) => ({
      ...current,
      [sceneNumber]: !current[sceneNumber],
    }));
  };

  return (
    <div className="visual-review">
      <div className="visual-review__heading">
        <span className="review-icon"><Image size={22} /></span>
        <div>
          <span>Revisão visual</span>
          <h2>Aprove o storyboard híbrido</h2>
          <p>Slides fixos preservam informações legíveis. Somente os frames marcados como vídeo serão animados.</p>
        </div>
      </div>

      <div className="visual-review__tip">
        <Sparkles size={17} />
        <span><strong>{job.debug_mode ? "Modo debug ativo." : "Ritmo editorial planejado."}</strong> {job.debug_mode ? "As páginas originais são usadas como frames e nenhuma regeneração chama APIs pagas." : `${staticCount} slides manterão o texto intacto e ${videoCount} cenas usarão movimento sem palavras.`}</span>
      </div>

      {error && <div className="visual-review__error" role="alert">{error}</div>}

      <div className="visual-grid">
        {job.scene_images.map((scene) => {
          const isVideo = scene.media_mode === "video";
          const regenerating = job.regenerating_scene_numbers.includes(scene.scene_number);
          const editedPrompt = editedPrompts[scene.scene_number] ?? scene.prompt;
          const editorOpen = Boolean(openEditors[scene.scene_number]);
          const editorId = `scene-${scene.scene_number}-prompt-editor`;
          return (
            <article
              className={`visual-card visual-card--${scene.media_mode}${editorOpen ? " visual-card--editing" : ""}`}
              key={`${scene.scene_number}-${scene.revision}`}
            >
              <div className="visual-card__image">
                <img src={assetUrl(scene.image_url)} alt={`Frame planejado para a cena ${scene.scene_number}`} />
                <span>Cena {scene.scene_number}</span>
                <small>versão {scene.revision}</small>
                <em className={`visual-card__mode visual-card__mode--${scene.media_mode}`}>
                  {isVideo ? <Clapperboard size={13} /> : <FileText size={13} />}
                  {isVideo ? "Vídeo sem texto" : "Slide fixo"}
                </em>
                {isVideo ? <button
                  className="visual-card__edit"
                  type="button"
                  aria-expanded={editorOpen}
                  aria-controls={editorId}
                  onClick={() => toggleEditor(scene.scene_number)}
                >
                  <PencilLine size={15} />
                  {editorOpen ? "Fechar editor" : "Editar prompt e regenerar"}
                </button> : <div className="visual-card__static-note">
                  Texto preservado da página {scene.source_slide_number ?? scene.source_slide_numbers[0]}
                </div>}
              </div>
              {isVideo && editorOpen && <div className="visual-card__body" id={editorId}>
                <div className="visual-card__editor-heading">
                  <div>
                    <strong>Personalize a imagem da cena {scene.scene_number}</strong>
                    <span>
                      Baseado nas páginas {formatSourcePages(scene.source_slide_numbers)}. Descreva
                      uma ação ou ambiente sem palavras e gere somente este frame novamente.
                    </span>
                  </div>
                  <span>versão {scene.revision}</span>
                </div>
                <label className="visual-card__prompt">
                  <span>Prompt do vídeo sem texto</span>
                  <textarea
                    value={editedPrompt}
                    rows={6}
                    disabled={regenerating}
                    aria-label={`Prompt da imagem da cena ${scene.scene_number}`}
                    onChange={(event) => setEditedPrompts((current) => ({
                      ...current,
                      [scene.scene_number]: event.target.value,
                    }))}
                  />
                </label>
                <span><Sparkles size={13} /> {scene.camera_motion}</span>
                <button
                  className="secondary-button visual-card__action"
                  type="button"
                  disabled={isActing || regenerating || editedPrompt.trim().length < 3}
                  onClick={() => void onRegenerate(scene.scene_number, editedPrompt.trim())}
                >
                  {regenerating ? <LoaderCircle className="spin" size={16} /> : <RefreshCw size={16} />}
                  {regenerating ? "Gerando nova versão..." : "Regenerar imagem com este prompt"}
                </button>
              </div>}
            </article>
          );
        })}
      </div>

      <div className="visual-review__approval">
        <div><CheckCircle2 size={20} /><span><strong>{staticCount} slides fixos + {videoCount} vídeos</strong>O resultado preservará os textos e animará somente as cenas sem palavras.</span></div>
        <button className="primary-button" type="button" disabled={isActing} onClick={() => void onApprove()}>
          {isActing ? <LoaderCircle className="spin" size={18} /> : <CheckCircle2 size={18} />}
          Aprovar e gerar vídeo
        </button>
      </div>
    </div>
  );
}
