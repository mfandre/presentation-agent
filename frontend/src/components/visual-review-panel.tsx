import { useState } from "react";
import {
  CheckCircle2,
  Clapperboard,
  FileText,
  Image,
  LoaderCircle,
  PencilLine,
  PanelsTopLeft,
  RefreshCw,
  Sparkles,
} from "lucide-react";

import type { VideoJob } from "../api/contracts";

interface VisualReviewPanelProps {
  job: VideoJob;
  isActing: boolean;
  error: string | null;
  assetUrl(path: string): string;
  onRegenerate(sceneNumber: number, shotNumber: number, prompt: string): Promise<void>;
  onUseSourceSlide(sceneNumber: number, shotNumber: number, sourceSlideNumber: number): Promise<void>;
  onGenerateFromSourceSlide(sceneNumber: number, shotNumber: number, sourceSlideNumber: number, prompt: string): Promise<void>;
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

const instructionalBadges = {
  concept: {
    label: "Conceito",
    description: "Explica uma ideia central por meio de uma representação visual.",
  },
  process: {
    label: "Processo",
    description: "Demonstra uma sequência de etapas ou ações em ordem.",
  },
  rule: {
    label: "Regra",
    description: "Apresenta uma norma, obrigação, prazo, limite ou critério importante.",
  },
  behavior: {
    label: "Comportamento",
    description: "Mostra uma conduta esperada ou uma situação prática de trabalho.",
  },
  system_demo: {
    label: "Sistema",
    description: "Ensina uma interação com sistema, portal, tela ou formulário.",
  },
  recap: {
    label: "Resumo",
    description: "Reforça os principais aprendizados apresentados anteriormente.",
  },
} as const;

export function VisualReviewPanel({
  job,
  isActing,
  error,
  assetUrl,
  onRegenerate,
  onUseSourceSlide,
  onGenerateFromSourceSlide,
  onApprove,
}: VisualReviewPanelProps) {
  const [editedPrompts, setEditedPrompts] = useState<Record<string, string>>({});
  const [openEditors, setOpenEditors] = useState<Record<string, boolean>>(() => {
    const first = job.scene_images.find((scene) => scene.media_mode === "video");
    return first ? { [`${first.scene_number}-${first.shot_number}`]: true } : {};
  });
  const [selectedPages, setSelectedPages] = useState<Record<string, number>>({});
  const [activeSourceAction, setActiveSourceAction] = useState<{
    key: string;
    action: "use" | "generate";
  } | null>(null);

  const staticCount = job.scene_images.filter((scene) => scene.preserve_source_frame).length;
  const illustrationCount = job.scene_images.filter(
    (scene) => scene.media_mode === "static" && !scene.preserve_source_frame,
  ).length;
  const videoCount = job.scene_images.filter((scene) => scene.media_mode === "video").length;

  const toggleEditor = (key: string) => {
    setOpenEditors((current) => ({
      ...current,
      [key]: !current[key],
    }));
  };

  const runSourceAction = async (
    key: string,
    action: "use" | "generate",
    operation: () => Promise<void>,
  ) => {
    setActiveSourceAction({ key, action });
    try {
      await operation();
    } finally {
      setActiveSourceAction(null);
    }
  };

  return (
    <div className="visual-review">
      <div className="visual-review__heading">
        <span className="review-icon"><Image size={22} /></span>
        <div>
          <span>Revisão visual</span>
          <h2>
            {job.production_mode === "cinematic_story"
              ? "Aprove o storyboard cinematográfico"
              : job.production_mode === "whiteboard_explainer"
                ? "Aprove o storyboard whiteboard"
                : job.production_mode === "corporate_training"
                  ? "Aprove o storyboard instrucional"
              : "Aprove o storyboard híbrido"}
          </h2>
          <p>
            {job.production_mode === "cinematic_story"
              ? "Cada cartão é um take de até 8 segundos, sincronizado a um trecho específico da narração."
              : job.production_mode === "whiteboard_explainer"
                ? "Cada cartão mostra um quadro didático que será construído progressivamente durante a narração."
                : job.production_mode === "corporate_training"
                  ? "Todo o conteúdo é reconstruído na identidade do treinamento; informações densas usam composições editoriais estáticas e cenas demonstrativas usam takes curtos."
              : "Slides fixos preservam informações legíveis. Somente os frames marcados como vídeo serão animados."}
          </p>
        </div>
      </div>

      <div className="visual-review__tip">
        <Sparkles size={17} />
        <span><strong>{job.debug_mode ? "Modo debug ativo." : "Ritmo editorial planejado."}</strong> {job.debug_mode ? "Nenhuma regeneração chama APIs pagas." : job.production_mode === "cinematic_story" ? `${videoCount} takes serão gerados e compostos sem loops nem slides fixos.` : `${staticCount} páginas visuais serão preservadas, ${illustrationCount} cenas terão ilustrações editoriais e ${videoCount} cenas usarão movimento sem palavras.`}</span>
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
          const shotKey = `${scene.scene_number}-${scene.shot_number}`;
          const isVideo = scene.media_mode === "video";
          const isInstructionalStill = (
            job.production_mode === "corporate_training"
            && scene.media_mode === "static"
            && !scene.preserve_source_frame
          );
          const canPromptRegenerate = isVideo || !scene.preserve_source_frame;
          const isEditable = true;
          const regenerating = job.regenerating_scene_numbers.includes(scene.scene_number);
          const editedPrompt = editedPrompts[shotKey] ?? scene.prompt;
          const editorOpen = Boolean(openEditors[shotKey]);
          const editorId = `scene-${scene.scene_number}-shot-${scene.shot_number}-prompt-editor`;
          const selectedPage = selectedPages[shotKey] ?? scene.source_slide_number ?? scene.source_slide_numbers[0] ?? job.source_pages[0]?.number;
          const usingSourceSlide = activeSourceAction?.key === shotKey && activeSourceAction.action === "use";
          const generatingFromSource = activeSourceAction?.key === shotKey && activeSourceAction.action === "generate";
          return (
            <article
              className={`visual-card visual-card--${scene.media_mode}${editorOpen ? " visual-card--editing" : ""}`}
              key={`${shotKey}-${scene.revision}`}
            >
              <div className="visual-card__image">
                <img src={assetUrl(scene.image_url)} alt={`Frame planejado para a cena ${scene.scene_number}`} />
                <span>Cena {scene.scene_number} · take {scene.shot_number}</span>
                <small>versão {scene.revision}</small>
                <em className={`visual-card__mode visual-card__mode--${scene.media_mode}`}>
                  {isVideo ? <Clapperboard size={13} /> : <FileText size={13} />}
                  {isVideo
                    ? "Vídeo sem texto"
                    : isInstructionalStill
                      ? "Ilustração instrucional"
                      : scene.preserve_source_frame ? "Captura fiel" : "Ilustração editorial"}
                </em>
                {scene.instructional_type && (
                  <span
                    className="visual-card__instructional-type"
                    data-tooltip={instructionalBadges[scene.instructional_type].description}
                    aria-label={`${instructionalBadges[scene.instructional_type].label}: ${instructionalBadges[scene.instructional_type].description}`}
                    tabIndex={0}
                  >
                    {instructionalBadges[scene.instructional_type].label}
                  </span>
                )}
                {isEditable ? <button
                  className="visual-card__edit"
                  type="button"
                  aria-expanded={editorOpen}
                  aria-controls={editorId}
                  onClick={() => toggleEditor(shotKey)}
                >
                  <PencilLine size={15} />
                  {editorOpen ? "Fechar editor" : "Editar prompt e regenerar"}
                </button> : <div className="visual-card__static-note">
                  Conteúdo preservado da página {scene.source_slide_number ?? scene.source_slide_numbers[0]}
                </div>}
              </div>
              {isEditable && editorOpen && <div className="visual-card__body" id={editorId}>
                <div className="visual-card__editor-heading">
                  <div>
                    <strong>Personalize o take {scene.shot_number} da cena {scene.scene_number}</strong>
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
                      [shotKey]: event.target.value,
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
                {scene.narration_excerpt && (
                  <div className="visual-card__concepts">
                    <strong>Trecho sincronizado · {scene.shot_duration_seconds?.toFixed(1)}s</strong>
                    <span>{scene.narration_excerpt}</span>
                    <small>Função: {scene.story_function.replaceAll("_", " ")}</small>
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
                {canPromptRegenerate && <button
                  className="secondary-button visual-card__action"
                  type="button"
                  disabled={isActing || regenerating || editedPrompt.trim().length < 3}
                  onClick={() => void onRegenerate(
                    scene.scene_number,
                    scene.shot_number,
                    editedPrompt.trim(),
                  )}
                >
                  {regenerating ? <LoaderCircle className="spin" size={16} /> : <RefreshCw size={16} />}
                  {regenerating ? "Gerando nova versão..." : "Regenerar imagem com este prompt"}
                </button>}
                {job.source_pages.length > 0 && job.production_mode !== "whiteboard_explainer" && <div className="source-slide-tools">
                  <div className="source-slide-tools__heading"><PanelsTopLeft size={16} /><strong>Usar conteúdo existente</strong></div>
                  <div className="source-slide-gallery">
                    {job.source_pages.map((page) => (
                      <button
                        className={selectedPage === page.number ? "is-selected" : ""}
                        type="button"
                        key={page.number}
                        onClick={() => setSelectedPages((current) => ({ ...current, [shotKey]: page.number }))}
                      >
                        <img src={assetUrl(page.image_url)} alt={`Página ${page.number}: ${page.title}`} />
                        <span>Página {page.number}</span>
                      </button>
                    ))}
                  </div>
                  <div className="source-slide-tools__actions">
                    {job.production_mode === "hybrid_presentation" && <button
                      className="secondary-button"
                      type="button"
                      disabled={isActing || !selectedPage}
                      onClick={() => selectedPage && void runSourceAction(
                        shotKey,
                        "use",
                        () => onUseSourceSlide(scene.scene_number, scene.shot_number, selectedPage),
                      )}
                    >
                      {usingSourceSlide ? <LoaderCircle className="spin" size={15} /> : <FileText size={15} />}
                      {usingSourceSlide ? "Aplicando slide..." : "Usar slide sem animar"}
                    </button>}
                    <button
                      className="secondary-button"
                      type="button"
                      disabled={isActing || regenerating || !selectedPage}
                      onClick={() => selectedPage && void runSourceAction(
                        shotKey,
                        "generate",
                        () => onGenerateFromSourceSlide(
                          scene.scene_number,
                          scene.shot_number,
                          selectedPage,
                          editedPrompt.trim(),
                        ),
                      )}
                    >
                      {generatingFromSource ? <LoaderCircle className="spin" size={15} /> : <Sparkles size={15} />}
                      {generatingFromSource ? "Criando nova imagem..." : "Criar imagem baseada nele"}
                    </button>
                  </div>
                </div>}
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
