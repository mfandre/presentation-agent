import { Timer } from "lucide-react";
import { useEffect, useState } from "react";

import type { VideoJob } from "../api/contracts";

interface JobTimerProps {
  job: VideoJob;
}

function elapsedLabel(milliseconds: number): string {
  const totalSeconds = Math.max(0, Math.floor(milliseconds / 1000));
  const hours = Math.floor(totalSeconds / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  const seconds = totalSeconds % 60;
  return hours > 0
    ? `${String(hours).padStart(2, "0")}:${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}`
    : `${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}`;
}

export function JobTimer({ job }: JobTimerProps) {
  const [now, setNow] = useState(() => Date.now());
  const finished = Boolean(job.end_datetime);

  useEffect(() => {
    if (finished) return;
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [finished]);

  const start = new Date(job.start_datetime || job.created_at).getTime();
  const end = job.end_datetime ? new Date(job.end_datetime).getTime() : now;

  return (
    <div className={`job-timer${finished ? " job-timer--finished" : ""}`} role="timer">
      <Timer size={16} />
      <span>{finished ? "Tempo total" : "Tempo de processamento"}</span>
      <strong>{elapsedLabel(end - start)}</strong>
    </div>
  );
}
