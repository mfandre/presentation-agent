import { Captions, CheckCircle2, Download, FileJson2, RotateCcw } from "lucide-react";

import type { VideoJob } from "../api/contracts";
import { formatDuration } from "../utils/format";

interface ResultPanelProps {
  job: VideoJob;
  assetUrl(path: string): string;
  onReset(): void;
}

export function ResultPanel({ job, assetUrl, onReset }: ResultPanelProps) {
  const videoUrl = job.video_url ? assetUrl(job.video_url) : null;
  const scriptUrl = job.script_url ? assetUrl(job.script_url) : null;
  const captionsVttUrl = job.captions_vtt_url ? assetUrl(job.captions_vtt_url) : null;
  const captionsSrtUrl = job.captions_srt_url ? assetUrl(job.captions_srt_url) : null;

  return (
    <div className="result-panel">
      <div className="result-heading">
        <span className="success-icon"><CheckCircle2 size={23} /></span>
        <div><span>Processamento concluído</span><h2>Seu vídeo está pronto</h2></div>
      </div>

      {videoUrl && (
        <div className="video-frame">
          <video controls preload="metadata" src={videoUrl}>
            Seu navegador não suporta vídeo HTML5.
          </video>
        </div>
      )}

      <div className="result-summary">
        <div><span>Arquivo</span><strong title={job.file_name}>{job.file_name}</strong></div>
        <div><span>Duração</span><strong>{job.duration_seconds ? formatDuration(job.duration_seconds) : "—"}</strong></div>
        <div><span>Idioma</span><strong>{job.language}</strong></div>
      </div>

      <div className="result-actions">
        {videoUrl && <a className="primary-button" href={videoUrl} download><Download size={18} /> Baixar MP4</a>}
        {captionsVttUrl && <a className="secondary-button" href={captionsVttUrl} download><Captions size={18} /> Baixar VTT</a>}
        {captionsSrtUrl && <a className="secondary-button" href={captionsSrtUrl} download><Captions size={18} /> Baixar SRT</a>}
        {scriptUrl && <a className="secondary-button" href={scriptUrl} download><FileJson2 size={18} /> Baixar roteiro</a>}
        <button className="ghost-button" type="button" onClick={onReset}><RotateCcw size={17} /> Novo vídeo</button>
      </div>
    </div>
  );
}
