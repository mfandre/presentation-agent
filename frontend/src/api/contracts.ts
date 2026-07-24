export type JobStatus =
  | "received"
  | "ingesting"
  | "scripting"
  | "visual_planning"
  | "generating_images"
  | "awaiting_visual_approval"
  | "generating_video"
  | "synthesizing"
  | "rendering"
  | "assembling"
  | "completed"
  | "failed";

export interface VideoJob {
  job_id: string;
  status: JobStatus;
  progress_percent: number;
  detail: string;
  file_name: string;
  target_seconds: number;
  language: string;
  audience: string;
  tone: string;
  created_at: string;
  updated_at: string;
  duration_seconds: number | null;
  video_url: string | null;
  script_url: string | null;
  visual_plan_url: string | null;
  scene_images: SceneImage[];
  regenerating_scene_numbers: number[];
  debug_mode: boolean;
}

export interface SceneImage {
  scene_number: number;
  source_slide_numbers: number[];
  image_url: string;
  prompt: string;
  camera_motion: string;
  revision: number;
  media_mode: "static" | "video";
  story_beat: string;
  source_slide_number: number | null;
}

export interface CreateVideoInput {
  file: File;
  targetSeconds: number;
  language: string;
  audience: string;
  tone: string;
}

export interface RuntimeConfig {
  debug_mode: boolean;
  debug_max_scenes: number | null;
}

export interface VideoGateway {
  getRuntimeConfig(): Promise<RuntimeConfig>;
  createVideo(input: CreateVideoInput): Promise<VideoJob>;
  getVideo(jobId: string): Promise<VideoJob>;
  regenerateScene(jobId: string, sceneNumber: number, prompt: string): Promise<VideoJob>;
  approveVisuals(jobId: string): Promise<VideoJob>;
  assetUrl(path: string): string;
}
