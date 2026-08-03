import { ChevronDown, CircleHelp, Clock3, Film, GraduationCap, History, Mic2, PenTool, Presentation, Sparkles, WandSparkles } from "lucide-react";
import { type FormEvent, useEffect, useState } from "react";

import type { CreateVideoInput, ProductionMode, ProductionPreset } from "../api/contracts";
import { FileDropzone } from "./file-dropzone";

interface ConfigurationFormProps {
  disabled: boolean;
  isResuming: boolean;
  presets: ProductionPreset[];
  onSubmit(input: CreateVideoInput): Promise<void>;
  onResume(jobId: string): Promise<void>;
}

const AUDIENCES = [
  { value: "executive", label: "Executiva" },
  { value: "technical", label: "Técnica" },
  { value: "commercial", label: "Comercial" },
  { value: "training", label: "Treinamento" },
];

const TONES = [
  { value: "professional and natural", label: "Profissional e natural" },
  { value: "concise and objective", label: "Conciso e objetivo" },
  { value: "engaging and persuasive", label: "Envolvente e persuasivo" },
  { value: "didactic and approachable", label: "Didático e acessível" },
];

const FALLBACK_PRESETS: ProductionPreset[] = [
  {
    id: "hybrid_presentation",
    label: "Apresentação híbrida",
    description: "Combina cenas geradas com páginas fixas quando informações precisam ser lidas.",
    icon: "presentation",
    strategy: "hybrid",
    narrative_direction: "",
    options: [],
  },
  {
    id: "cinematic_story",
    label: "História cinematográfica",
    description: "Cria imagens e cenas originais do início ao fim, sem mostrar slides fixos.",
    icon: "film",
    strategy: "cinematic",
    narrative_direction: "",
    options: [
      {
        id: "speech_mode",
        label: "Voz do vídeo",
        type: "select",
        default: "narration",
        choices: [
          { value: "narration", label: "Narração" },
          { value: "character_dialogue", label: "Diálogo entre personagens" },
        ],
      },
      {
        id: "visual_style",
        label: "Visual style",
        type: "select",
        default: "default",
        choices: [
          { value: "default", label: "Default — realistic editorial documentary" },
          { value: "disney_animation", label: "Disney animation" },
          { value: "pixar_style_3d", label: "Pixar-style 3D" },
          { value: "anime", label: "Anime" },
          { value: "live_action", label: "Live action" },
          { value: "stylized_3d", label: "Stylized 3D" },
          { value: "comic_book", label: "Comic book" },
          { value: "fantasy", label: "Fantasy" },
          { value: "sci_fi", label: "Sci-fi" },
          { value: "horror", label: "Horror" },
          { value: "stop_motion", label: "Stop motion" },
        ],
      },
    ],
  },
  {
    id: "corporate_training",
    label: "Treinamento corporativo",
    description: "Alterna exemplos, processos e páginas fiéis conforme o objetivo de cada cena.",
    icon: "graduation-cap",
    strategy: "training",
    narrative_direction: "",
    options: [],
  },
];

export function ConfigurationForm({
  disabled,
  isResuming,
  presets,
  onSubmit,
  onResume,
}: ConfigurationFormProps) {
  const [file, setFile] = useState<File | null>(null);
  const [durationMinutes, setDurationMinutes] = useState(5);
  const [language, setLanguage] = useState("pt-BR");
  const [audience, setAudience] = useState("executive");
  const [tone, setTone] = useState("professional and natural");
  const [productionMode, setProductionMode] = useState<ProductionMode>("hybrid_presentation");
  const [presetOptions, setPresetOptions] = useState<Record<string, string>>({});
  const [fileError, setFileError] = useState<string | null>(null);
  const [resumeJobId, setResumeJobId] = useState("");
  const [resumeError, setResumeError] = useState<string | null>(null);
  const availablePresets = presets.length > 0 ? presets : FALLBACK_PRESETS;
  const selectedPreset = availablePresets.find((preset) => preset.id === productionMode);

  useEffect(() => {
    if (!selectedPreset) return;
    setPresetOptions((current) => Object.fromEntries(
      selectedPreset.options.map((option) => [option.id, current[option.id] ?? option.default]),
    ));
  }, [selectedPreset]);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (!file) {
      setFileError("Adicione uma apresentação para continuar.");
      return;
    }
    await onSubmit({
      file,
      targetSeconds: durationMinutes * 60,
      language,
      audience,
      tone,
      productionMode,
      presetOptions,
    });
  };

  const resume = async (event: FormEvent) => {
    event.preventDefault();
    const normalized = resumeJobId.trim().toLowerCase();
    if (!/^[0-9a-f]{32}$/.test(normalized)) {
      setResumeError("Informe o ID completo do job com 32 caracteres.");
      return;
    }
    setResumeError(null);
    await onResume(normalized);
  };

  return (
    <div className="configuration-stack">
    <form className="configuration-form" onSubmit={submit}>
      <section className="form-section">
        <div className="section-heading">
          <span className="section-number">1</span>
          <div>
            <h2>Apresentação</h2>
            <p>O documento será usado como fonte factual para construir o vídeo.</p>
          </div>
        </div>
        <FileDropzone
          file={file}
          disabled={disabled}
          onChange={(nextFile) => {
            setFile(nextFile);
            setFileError(null);
          }}
          onError={setFileError}
        />
        {fileError && <p className="field-error">{fileError}</p>}
      </section>

      <section className="form-section">
        <div className="section-heading">
          <span className="section-number">2</span>
          <div>
            <h2>Narrativa</h2>
            <p>Defina para quem a apresentação será contada.</p>
          </div>
        </div>

        <fieldset className="production-mode-field">
          <legend>Formato visual</legend>
          {availablePresets.map((preset) => (
            <label
              className={productionMode === preset.id ? "production-mode-card is-selected" : "production-mode-card"}
              key={preset.id}
            >
              <input
                type="radio"
                name="production-mode"
                value={preset.id}
                checked={productionMode === preset.id}
                onChange={() => setProductionMode(preset.id)}
                disabled={disabled}
              />
              {preset.icon === "film"
                ? <Film size={20} />
                : preset.icon === "pen-tool"
                  ? <PenTool size={20} />
                  : preset.icon === "graduation-cap"
                    ? <GraduationCap size={20} />
                  : <Presentation size={20} />}
              <span>
                <strong>{preset.label}</strong>
                <small>{preset.description}</small>
              </span>
            </label>
          ))}
        </fieldset>

        {selectedPreset && selectedPreset.options.length > 0 && (
          <div className="preset-options">
            <div className="preset-options__heading">
              <PenTool size={17} />
              <span><strong>Personalize o preset</strong><small>Opções próprias de {selectedPreset.label}.</small></span>
            </div>
            <div className="field-grid">
              {selectedPreset.options.map((option) => (
                <label className="field" key={option.id}>
                  <span>{option.label}</span>
                  <div className="select-wrap">
                    <select
                      value={presetOptions[option.id] ?? option.default}
                      disabled={disabled}
                      onChange={(event) => setPresetOptions((current) => ({
                        ...current,
                        [option.id]: event.target.value,
                      }))}
                    >
                      {option.choices.map((choice) => (
                        <option value={choice.value} key={choice.value}>{choice.label}</option>
                      ))}
                    </select>
                    <ChevronDown size={16} />
                  </div>
                </label>
              ))}
            </div>
          </div>
        )}

        <div className="field-grid">
          <label className="field">
            <span><Mic2 size={16} /> Idioma</span>
            <div className="select-wrap">
              <select value={language} onChange={(event) => setLanguage(event.target.value)} disabled={disabled}>
                <option value="pt-BR">Português (Brasil)</option>
                <option value="en-US">English (US)</option>
                <option value="es-ES">Español</option>
              </select>
              <ChevronDown size={16} />
            </div>
          </label>

          <label className="field">
            <span>
              <Sparkles size={16} /> Público
              <span
                className="field-help"
                data-tooltip={"Executiva — destaca decisões, impacto, riscos e resultados.\nTécnica — usa mais detalhes, conceitos e precisão operacional.\nComercial — enfatiza valor, benefícios e poder de convencimento.\nTreinamento — prioriza clareza, exemplos e aplicação prática."}
                aria-label="Diferenças entre os públicos: Executiva destaca decisões e resultados; Técnica prioriza detalhes e precisão; Comercial enfatiza valor e convencimento; Treinamento prioriza clareza e aplicação prática."
                tabIndex={0}
              >
                <CircleHelp size={14} />
              </span>
            </span>
            <div className="select-wrap">
              <select value={audience} onChange={(event) => setAudience(event.target.value)} disabled={disabled}>
                {AUDIENCES.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}
              </select>
              <ChevronDown size={16} />
            </div>
          </label>
        </div>

        <label className="field">
          <span>
            <WandSparkles size={16} /> Tom da apresentação
            <span
              className="field-help"
              data-tooltip={"Profissional e natural — equilibrado, fluido e institucional sem soar rígido.\nConciso e objetivo — frases diretas, menos contexto e foco no essencial.\nEnvolvente e persuasivo — cria interesse e reforça argumentos e benefícios.\nDidático e acessível — explica conceitos gradualmente com linguagem simples."}
              aria-label="Diferenças entre os tons: Profissional e natural é equilibrado e fluido; Conciso e objetivo foca no essencial; Envolvente e persuasivo reforça argumentos; Didático e acessível explica gradualmente com linguagem simples."
              tabIndex={0}
            >
              <CircleHelp size={14} />
            </span>
          </span>
          <div className="select-wrap">
            <select value={tone} onChange={(event) => setTone(event.target.value)} disabled={disabled}>
              {TONES.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}
            </select>
            <ChevronDown size={16} />
          </div>
        </label>

        <label className="duration-field">
          <div className="duration-field__header">
            <span><Clock3 size={16} /> Duração desejada</span>
            <strong>{durationMinutes} min</strong>
          </div>
          <input
            type="range"
            min="1"
            max="10"
            step="1"
            value={durationMinutes}
            onChange={(event) => setDurationMinutes(Number(event.target.value))}
            disabled={disabled}
          />
          <div className="range-labels"><span>1 min</span><span>10 min</span></div>
        </label>
      </section>

      <button type="submit" className="primary-button" disabled={disabled}>
        <WandSparkles size={19} />
        {disabled
          ? "Iniciando processamento…"
          : productionMode === "cinematic_story" && presetOptions.speech_mode === "character_dialogue"
            ? "Criar vídeo com diálogos"
            : "Criar vídeo narrado"}
      </button>
      <p className="form-footnote">O arquivo é processado cena a cena para permitir retries e edição isolada.</p>
    </form>
    <form className="resume-job-form" onSubmit={resume}>
      <div className="resume-job-form__heading">
        <span><History size={18} /></span>
        <div>
          <h2>Retomar processamento</h2>
          <p>Informe o ID de um job interrompido para continuar usando os artefatos existentes.</p>
        </div>
      </div>
      <label className="resume-job-field">
        <span>ID do job</span>
        <input
          value={resumeJobId}
          onChange={(event) => {
            setResumeJobId(event.target.value);
            setResumeError(null);
          }}
          placeholder="e6c0e8a14e034f28b074d48b00500726"
          spellCheck={false}
          autoComplete="off"
          disabled={disabled || isResuming}
        />
      </label>
      {resumeError && <p className="field-error">{resumeError}</p>}
      <button
        type="submit"
        className="secondary-button resume-job-button"
        disabled={disabled || isResuming || !resumeJobId.trim()}
      >
        <History size={17} />
        {isResuming ? "Retomando…" : "Retomar pelo ID"}
      </button>
    </form>
    </div>
  );
}
