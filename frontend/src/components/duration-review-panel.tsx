import { Clock3, Scissors, TimerReset, XCircle } from "lucide-react";

import type { VideoJob } from "../api/contracts";

interface DurationReviewPanelProps {
  job: VideoJob;
  isActing: boolean;
  error: string | null;
  onDecision(decision: "summarize" | "accept" | "cancel"): Promise<void>;
}

function durationLabel(seconds: number): string {
  const minutes = Math.floor(seconds / 60);
  const remainder = seconds % 60;
  return `${minutes}min${String(remainder).padStart(2, "0")}s`;
}

export function DurationReviewPanel({
  job,
  isActing,
  error,
  onDecision,
}: DurationReviewPanelProps) {
  const requested = job.requested_target_seconds ?? job.target_seconds;
  const estimated = job.estimated_duration_seconds ?? job.target_seconds;

  return (
    <div className="duration-review-panel">
      <span className="duration-review-panel__icon"><Clock3 size={29} /></span>
      <div>
        <span className="eyebrow">Checkpoint de duração</span>
        <h2>A história precisa de um pouco mais de tempo</h2>
        <p>
          A narração possui <strong>{job.narration_word_count ?? "mais"} palavras</strong> e foi
          estimada em <strong>{durationLabel(estimated)}</strong>, enquanto você solicitou{" "}
          <strong>{durationLabel(requested)}</strong>.
        </p>
      </div>

      <div className="duration-review-panel__comparison">
        <div><small>Solicitado</small><strong>{durationLabel(requested)}</strong></div>
        <TimerReset size={22} />
        <div><small>Estimado</small><strong>{durationLabel(estimated)}</strong></div>
      </div>

      {error && <p className="field-error">{error}</p>}

      <div className="duration-review-panel__actions">
        <button
          type="button"
          className="primary-button"
          disabled={isActing}
          onClick={() => void onDecision("summarize")}
        >
          <Scissors size={18} /> Resumir para {durationLabel(requested)}
        </button>
        <button
          type="button"
          className="secondary-button"
          disabled={isActing}
          onClick={() => void onDecision("accept")}
        >
          <Clock3 size={18} /> Prosseguir com {durationLabel(estimated)}
        </button>
        <button
          type="button"
          className="text-button"
          disabled={isActing}
          onClick={() => void onDecision("cancel")}
        >
          <XCircle size={17} /> Cancelar
        </button>
      </div>
    </div>
  );
}
