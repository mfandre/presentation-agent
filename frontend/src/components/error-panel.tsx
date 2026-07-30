import { AlertTriangle, RotateCcw } from "lucide-react";

interface ErrorPanelProps {
  message: string;
  onReset(): void;
  onResume?(): void;
  isActing?: boolean;
}

export function ErrorPanel({ message, onReset, onResume, isActing = false }: ErrorPanelProps) {
  return (
    <div className="error-panel">
      <span className="error-panel__icon"><AlertTriangle size={28} /></span>
      <h2>Não foi possível criar o vídeo</h2>
      <p>{message}</p>
      {onResume && (
        <button
          type="button"
          className="primary-button"
          disabled={isActing}
          onClick={() => onResume()}
        >
          <RotateCcw size={17} /> {isActing ? "Retomando…" : "Retomar processamento"}
        </button>
      )}
      <button type="button" className="secondary-button" disabled={isActing} onClick={onReset}>
        Começar novamente
      </button>
    </div>
  );
}
