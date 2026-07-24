import { AlertTriangle, RotateCcw } from "lucide-react";

interface ErrorPanelProps {
  message: string;
  onReset(): void;
}

export function ErrorPanel({ message, onReset }: ErrorPanelProps) {
  return (
    <div className="error-panel">
      <span className="error-panel__icon"><AlertTriangle size={28} /></span>
      <h2>Não foi possível criar o vídeo</h2>
      <p>{message}</p>
      <button type="button" className="secondary-button" onClick={onReset}><RotateCcw size={17} /> Tentar novamente</button>
    </div>
  );
}
