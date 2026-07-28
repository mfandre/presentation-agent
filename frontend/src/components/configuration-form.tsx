import { ChevronDown, Clock3, Film, History, Mic2, Presentation, Sparkles, WandSparkles } from "lucide-react";
import { type FormEvent, useState } from "react";

import type { CreateVideoInput } from "../api/contracts";
import { FileDropzone } from "./file-dropzone";

interface ConfigurationFormProps {
  disabled: boolean;
  isResuming: boolean;
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

export function ConfigurationForm({
  disabled,
  isResuming,
  onSubmit,
  onResume,
}: ConfigurationFormProps) {
  const [file, setFile] = useState<File | null>(null);
  const [durationMinutes, setDurationMinutes] = useState(5);
  const [language, setLanguage] = useState("pt-BR");
  const [audience, setAudience] = useState("executive");
  const [tone, setTone] = useState("professional and natural");
  const [productionMode, setProductionMode] = useState<"hybrid_presentation" | "cinematic_story">("hybrid_presentation");
  const [fileError, setFileError] = useState<string | null>(null);
  const [resumeJobId, setResumeJobId] = useState("");
  const [resumeError, setResumeError] = useState<string | null>(null);

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
          <label className={productionMode === "hybrid_presentation" ? "production-mode-card is-selected" : "production-mode-card"}>
            <input
              type="radio"
              name="production-mode"
              value="hybrid_presentation"
              checked={productionMode === "hybrid_presentation"}
              onChange={() => setProductionMode("hybrid_presentation")}
              disabled={disabled}
            />
            <Presentation size={20} />
            <span>
              <strong>Apresentação híbrida</strong>
              <small>Combina cenas geradas com páginas fixas quando números, tabelas ou diagramas precisam ser lidos.</small>
            </span>
          </label>
          <label className={productionMode === "cinematic_story" ? "production-mode-card is-selected" : "production-mode-card"}>
            <input
              type="radio"
              name="production-mode"
              value="cinematic_story"
              checked={productionMode === "cinematic_story"}
              onChange={() => setProductionMode("cinematic_story")}
              disabled={disabled}
            />
            <Film size={20} />
            <span>
              <strong>História cinematográfica</strong>
              <small>Cria imagens e cenas originais do início ao fim, sem mostrar slides, páginas ou documentos fixos.</small>
            </span>
          </label>
        </fieldset>

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
            <span><Sparkles size={16} /> Público</span>
            <div className="select-wrap">
              <select value={audience} onChange={(event) => setAudience(event.target.value)} disabled={disabled}>
                {AUDIENCES.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}
              </select>
              <ChevronDown size={16} />
            </div>
          </label>
        </div>

        <label className="field">
          <span><WandSparkles size={16} /> Tom da apresentação</span>
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
        {disabled ? "Iniciando processamento…" : "Criar vídeo narrado"}
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
