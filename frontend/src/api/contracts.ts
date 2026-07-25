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
  creative_direction: CreativeDirection | null;
}

export interface CreativeDirection {
  hook_question: string;
  throughline: string;
  visual_motif: string;
  palette: string[];
  accent_color: string;
  pacing: "measured" | "dynamic" | "cinematic";
  reveal_scene_number: number | null;
  central_thesis: string;
  narrative_device: string;
  transformation_from: string;
  transformation_to: string;
  recurring_visual_principle: string;
  concept_mappings: ConceptMapping[];
}

export interface ConceptMapping {
  source_concept: string;
  target_concept: string;
  narrative_meaning: string;
}

export interface SceneImage {
  scene_number: number;
  source_slide_numbers: number[];
  image_url: string;
  prompt: string;
  camera_motion: string;
  motion_preset: "none" | "slow_push" | "pull_back" | "pan_left" | "pan_right" | "drift_up";
  entrance_motion: string;
  focal_action: string;
  transition_out: string;
  transition_preset: "cut" | "dissolve" | "fade" | "page_wipe";
  visual_beats: VisualBeat[];
  revision: number;
  media_mode: "static" | "video";
  story_beat: string;
  must_show_concepts: string[];
  concept_visualization: string;
  scene_purpose: string;
  relationship_to_thesis: string;
  narrative_progress: string;
  visible_evidence: string[];
  forbidden_substitutions: string[];
  source_slide_number: number | null;
  preserve_source_frame: boolean;
}

export interface VisualBeat {
  beat_number: number;
  kind: "generated_video" | "generated_image" | "source_slide" | "motion_graphic";
  duration_seconds: number;
  motion_preset: "none" | "slow_push" | "pull_back" | "pan_left" | "pan_right" | "drift_up";
  transition: "cut" | "dissolve" | "fade" | "page_wipe";
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
  resumeVideo(jobId: string): Promise<VideoJob>;
  assetUrl(path: string): string;
}
