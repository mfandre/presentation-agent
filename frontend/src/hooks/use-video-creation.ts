import { useCallback, useEffect, useRef, useState } from "react";

import type { CreateVideoInput, VideoGateway, VideoJob } from "../api/contracts";
import { HttpVideoGateway, HttpVideoGatewayError } from "../api/http-video-gateway";

interface UseVideoCreationResult {
  job: VideoJob | null;
  debugMode: boolean;
  debugMaxScenes: number | null;
  isSubmitting: boolean;
  isActing: boolean;
  error: string | null;
  createVideo(input: CreateVideoInput): Promise<void>;
  regenerateScene(sceneNumber: number, prompt: string): Promise<void>;
  approveVisuals(): Promise<void>;
  resumeVideo(jobId?: string): Promise<void>;
  reset(): void;
  assetUrl(path: string): string;
}

const POLLING_STOP_STATUSES = new Set(["awaiting_visual_approval", "completed", "failed"]);
const STORED_JOB_KEY = "presentation-video-active-job";

const defaultGateway: VideoGateway = new HttpVideoGateway();

function loadStoredJob(): VideoJob | null {
  try {
    const stored = window.localStorage.getItem(STORED_JOB_KEY);
    if (!stored) return null;
    const parsed = JSON.parse(stored) as Partial<VideoJob>;
    const hasCurrentSceneContract = (
      Array.isArray(parsed.regenerating_scene_numbers)
      && Array.isArray(parsed.scene_images)
      && parsed.scene_images.every((scene) => (
        typeof scene.scene_number === "number"
        && (scene.media_mode === "static" || scene.media_mode === "video")
      ))
    );
    if (!parsed.job_id || !parsed.status || !hasCurrentSceneContract) {
      window.localStorage.removeItem(STORED_JOB_KEY);
      return null;
    }
    return parsed as VideoJob;
  } catch {
    window.localStorage.removeItem(STORED_JOB_KEY);
    return null;
  }
}

export function useVideoCreation(
  gateway: VideoGateway = defaultGateway,
): UseVideoCreationResult {
  const [job, setJob] = useState<VideoJob | null>(loadStoredJob);
  const [debugMode, setDebugMode] = useState(false);
  const [debugMaxScenes, setDebugMaxScenes] = useState<number | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [isActing, setIsActing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const mustVerifyStoredJob = useRef(job !== null);

  useEffect(() => {
    let cancelled = false;
    gateway.getRuntimeConfig().then((config) => {
      if (cancelled) return;
      setDebugMode(config.debug_mode);
      setDebugMaxScenes(config.debug_max_scenes);
    }).catch(() => {
      // Job responses still carry debug_mode, so a transient config failure is non-blocking.
    });
    return () => { cancelled = true; };
  }, [gateway]);

  useEffect(() => {
    if (job) {
      window.localStorage.setItem(STORED_JOB_KEY, JSON.stringify(job));
    } else {
      window.localStorage.removeItem(STORED_JOB_KEY);
    }
  }, [job]);

  const createVideo = useCallback(
    async (input: CreateVideoInput) => {
      setIsSubmitting(true);
      setError(null);
      setJob(null);
      try {
        setJob(await gateway.createVideo(input));
      } catch (caught) {
        setError(caught instanceof Error ? caught.message : "Não foi possível iniciar o vídeo.");
      } finally {
        setIsSubmitting(false);
      }
    },
    [gateway],
  );

  useEffect(() => {
    if (!job || (POLLING_STOP_STATUSES.has(job.status) && !mustVerifyStoredJob.current)) {
      return;
    }

    let cancelled = false;
    let timer: number | undefined;

    const poll = async () => {
      try {
        const updated = await gateway.getVideo(job.job_id);
        if (cancelled) return;
        mustVerifyStoredJob.current = false;
        setJob(updated);
        setError(updated.status === "failed" ? updated.detail || "A geração do vídeo falhou." : null);
      } catch (caught) {
        if (!cancelled) {
          const message = caught instanceof Error ? caught.message : "Falha ao consultar o processamento.";
          if (caught instanceof HttpVideoGatewayError && caught.status === 404) {
            mustVerifyStoredJob.current = false;
            const interrupted = "O job não está mais disponível no servidor. O processo pode ter sido reiniciado durante a geração.";
            setError(interrupted);
            setJob((current) => current ? {
              ...current,
              status: "failed",
              detail: interrupted,
            } : current);
            return;
          }
          setError(message);
          timer = window.setTimeout(poll, 2200);
        }
      }
    };

    timer = window.setTimeout(poll, 1400);
    return () => {
      cancelled = true;
      if (timer !== undefined) window.clearTimeout(timer);
    };
  }, [gateway, job]);

  const regenerateScene = useCallback(async (sceneNumber: number, prompt: string) => {
    if (!job) return;
    setIsActing(true);
    setError(null);
    setJob((current) => current ? {
      ...current,
      regenerating_scene_numbers: [sceneNumber],
    } : current);
    try {
      setJob(await gateway.regenerateScene(job.job_id, sceneNumber, prompt));
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Não foi possível regenerar a imagem.");
    } finally {
      setIsActing(false);
      setJob((current) => current ? { ...current, regenerating_scene_numbers: [] } : current);
    }
  }, [gateway, job]);

  const approveVisuals = useCallback(async () => {
    if (!job) return;
    setIsActing(true);
    setError(null);
    try {
      setJob(await gateway.approveVisuals(job.job_id));
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Não foi possível aprovar os visuais.");
    } finally {
      setIsActing(false);
    }
  }, [gateway, job]);

  const resumeVideo = useCallback(async (jobId?: string) => {
    const targetJobId = (jobId ?? job?.job_id ?? "").trim().toLowerCase();
    if (!targetJobId) return;
    setIsActing(true);
    setError(null);
    try {
      setJob(await gateway.resumeVideo(targetJobId));
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Não foi possível retomar o vídeo.");
    } finally {
      setIsActing(false);
    }
  }, [gateway, job]);

  const reset = useCallback(() => {
    window.localStorage.removeItem(STORED_JOB_KEY);
    setJob(null);
    setError(null);
    setIsSubmitting(false);
    setIsActing(false);
  }, []);

  return {
    job,
    debugMode: debugMode || Boolean(job?.debug_mode),
    debugMaxScenes,
    isSubmitting,
    isActing,
    error,
    createVideo,
    regenerateScene,
    approveVisuals,
    resumeVideo,
    reset,
    assetUrl: (path: string) => gateway.assetUrl(path),
  };
}
