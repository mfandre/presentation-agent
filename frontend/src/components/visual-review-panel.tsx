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

  const staticCount = job.scene_images.filter((scene) => scene.preserve_source_frame).length;
  const illustrationCount = job.scene_images.filter(
    (scene) => scene.media_mode === "static" && !scene.preserve_source_frame,
  ).length;
  const videoCount = job.scene_images.filter((scene) => scene.media_mode === "video").length;

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
        <span><strong>{job.debug_mode ? "Modo debug ativo." : "Ritmo editorial planejado."}</strong> {job.debug_mode ? "Nenhuma regeneração chama APIs pagas." : `${staticCount} páginas visuais serão preservadas, ${illustrationCount} cenas terão ilustrações editoriais e ${videoCount} cenas usarão movimento sem palavras.`}</span>
      </div>

      {job.creative_direction && (
        <section className="creative-direction-card">
          <div>
            <span>Direção criativa</span>
            <h3>{job.creative_direction.hook_question || "Narrativa editorial integrada"}</h3>
            <p>{job.creative_direction.throughline}</p>
            {job.creative_direction.central_thesis && (
              <p><strong>Tese central:</strong> {job.creative_direction.central_thesis}</p>
            )}
          </div>
          <dl>
            {job.creative_direction.narrative_device && (
              <div><dt>Dispositivo narrativo</dt><dd>{job.creative_direction.narrative_device}</dd></div>
            )}
            {job.creative_direction.transformation_from && job.creative_direction.transformation_to && (
              <div>
                <dt>Transformação</dt>
                <dd>{job.creative_direction.transformation_from} → {job.creative_direction.transformation_to}</dd>
              </div>
            )}
            {job.creative_direction.recurring_visual_principle && (
              <div>
                <dt>Princípio recorrente</dt>
                <dd>{job.creative_direction.recurring_visual_principle}</dd>
              </div>
            )}
            <div><dt>Motivo visual</dt><dd>{job.creative_direction.visual_motif}</dd></div>
            <div><dt>Ritmo</dt><dd>{job.creative_direction.pacing}</dd></div>
            <div><dt>Acento</dt><dd>{job.creative_direction.accent_color}</dd></div>
            {job.creative_direction.reveal_scene_number && (
              <div><dt>Revelação</dt><dd>Cena {job.creative_direction.reveal_scene_number}</dd></div>
            )}
          </dl>
          {(job.creative_direction.concept_mappings ?? []).length > 0 && (
            <div className="visual-card__concepts">
              <strong>Correspondências da narrativa</strong>
              {(job.creative_direction.concept_mappings ?? []).map((mapping, index) => (
                <small key={`${mapping.source_concept}-${mapping.target_concept}-${index}`}>
                  {mapping.source_concept} → {mapping.target_concept}
                  {mapping.narrative_meaning ? ` — ${mapping.narrative_meaning}` : ""}
                </small>
              ))}
            </div>
          )}
        </section>
      )}

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
                  {isVideo
                    ? "Vídeo sem texto"
                    : scene.preserve_source_frame ? "Slide fixo" : "Ilustração editorial"}
                </em>
                {(isVideo || !scene.preserve_source_frame) ? <button
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
              {(isVideo || !scene.preserve_source_frame) && editorOpen && <div className="visual-card__body" id={editorId}>
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
                {(scene.must_show_concepts ?? []).length > 0 && (
                  <div className="visual-card__concepts">
                    <strong>Conceitos obrigatórios</strong>
                    <span>{scene.must_show_concepts.join(" · ")}</span>
                    {scene.concept_visualization && <small>{scene.concept_visualization}</small>}
                  </div>
                )}
                {scene.relationship_to_thesis && (
                  <div className="visual-card__concepts">
                    <strong>Função na narrativa</strong>
                    <span>{scene.scene_purpose}</span>
                    <small>{scene.relationship_to_thesis}</small>
                    {scene.narrative_progress && <small>Avanço: {scene.narrative_progress}</small>}
                  </div>
                )}
                <span><Sparkles size={13} /> {scene.camera_motion} · {scene.motion_preset.replaceAll("_", " ")}</span>
                <span><Clapperboard size={13} /> {scene.entrance_motion} → {scene.transition_out}</span>
                {(scene.visual_beats ?? []).length > 0 && (
                  <div className="visual-beat-strip">
                    {(scene.visual_beats ?? []).map((beat) => (
                      <span key={beat.beat_number}>
                        {beat.kind.replaceAll("_", " ")} · {Math.round(beat.duration_seconds)}s
                      </span>
                    ))}
                  </div>
                )}
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
        <div><CheckCircle2 size={20} /><span><strong>{staticCount} páginas + {illustrationCount} ilustrações + {videoCount} vídeos</strong>Páginas de texto corrido serão substituídas por imagens baseadas no storytelling.</span></div>
        <button className="primary-button" type="button" disabled={isActing} onClick={() => void onApprove()}>
          {isActing ? <LoaderCircle className="spin" size={18} /> : <CheckCircle2 size={18} />}
          Aprovar e gerar vídeo
        </button>
      </div>
    </div>
  );
}
