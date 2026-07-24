import { AudioLines, Layers3, Play, Sparkles } from "lucide-react";

export function EmptyPreview() {
  return (
    <div className="empty-preview">
      <div className="preview-stage">
        <div className="preview-stage__topbar"><span /><span /><span /></div>
        <div className="preview-stage__content">
          <div className="preview-copy">
            <span className="preview-kicker">ESTRATÉGIA DE DADOS</span>
            <h3>Transforme ideias em uma narrativa clara.</h3>
            <p>O conteúdo do documento ganha voz, ritmo e uma história coerente.</p>
            <div className="preview-lines"><span /><span /><span /></div>
          </div>
          <div className="preview-graphic">
            <div className="preview-ring"><Sparkles size={30} /></div>
            <span className="preview-dot preview-dot--one" />
            <span className="preview-dot preview-dot--two" />
          </div>
        </div>
        <div className="preview-stage__controls">
          <button type="button" aria-label="Reproduzir prévia"><Play size={16} fill="currentColor" /></button>
          <div><span /></div>
          <small>00:00 / 05:00</small>
        </div>
      </div>

      <div className="preview-features">
        <div><span><Sparkles size={17} /></span><strong>Roteiro por IA</strong><small>Cenas independentes das páginas</small></div>
        <div><span><AudioLines size={17} /></span><strong>Narração natural</strong><small>Áudio por cena</small></div>
        <div><span><Layers3 size={17} /></span><strong>Render modular</strong><small>Retry independente</small></div>
      </div>
    </div>
  );
}
