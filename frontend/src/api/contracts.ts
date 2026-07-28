export type JobStatus =
  | "received"
  | "ingesting"
  | "scripting"
  | "duration_validating"
  | "awaiting_duration_approval"
  | "synthesizing"
  | "scene_planning"
  | "visual_planning"
  | "prompt_compiling"
  | "rule_validating"
  | "generating_images"
  | "awaiting_visual_approval"
  | "generating_video"
  | "visual_qa"
  | "rendering"
  | "assembling"
  | "captioning"
  | "completed"
  | "cancelled"
  | "failed";

export interface VideoJob {
  job_id: string;
  status: JobStatus;
  progress_percent: number;
  detail: string;
  file_name: string;
  target_seconds: number;
  requested_target_seconds: number | null;
  estimated_duration_seconds: number | null;
  narration_word_count: number | null;
  language: string;
  audience: string;
  tone: string;
  production_mode: ProductionMode;
  created_at: string;
  updated_at: string;
  start_datetime: string;
  end_datetime: string | null;
  duration_seconds: number | null;
  video_url: string | null;
  script_url: string | null;
  visual_plan_url: string | null;
  captions_vtt_url: string | null;
  captions_srt_url: string | null;
  scene_images: SceneImage[];
  regenerating_scene_numbers: number[];
  debug_mode: boolean;
  creative_direction: CreativeDirection | null;
}

export type ProductionMode = "hybrid_presentation" | "cinematic_story";

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
  shot_number: number;
  shot_duration_seconds: number | null;
  narration_excerpt: string;
  story_function: string;
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
  productionMode: ProductionMode;
}

export interface RuntimeConfig {
  debug_mode: boolean;
  debug_max_scenes: number | null;
  debug_replay_job_id: string | null;
}

export type WorkflowRunStatus =
  | "pending"
  | "running"
  | "waiting"
  | "completed"
  | "failed"
  | "cancelled";

export type WorkflowStepStatus =
  | "pending"
  | "running"
  | "waiting"
  | "completed"
  | "failed"
  | "skipped";

export interface WorkflowStepDefinition {
  id: string;
  uses: string;
  needs: string[];
  inputs: Record<string, unknown>;
  config: Record<string, unknown>;
  outputs: Record<string, string>;
  when: boolean | string;
  foreach: string | null;
  parallelism: number;
  retry: {
    attempts: number;
    backoff_seconds: number;
    exponential: boolean;
  };
  checkpoint: "human" | null;
  timeout_seconds: number | null;
  continue_on_error: boolean;
}

export interface WorkflowDefinition {
  id: string;
  version: string;
  description: string;
  inputs: Record<string, { type: string; required: boolean; default: unknown }>;
  settings: Record<string, unknown>;
  steps: WorkflowStepDefinition[];
}

export interface WorkflowStepRun {
  run_id: string;
  step_id: string;
  uses: string;
  status: WorkflowStepStatus;
  attempt: number;
  inputs: Record<string, unknown>;
  outputs: Record<string, unknown>;
  error: string | null;
  started_at: string | null;
  finished_at: string | null;
}

export interface WorkflowSnapshot {
  run: {
    run_id: string;
    workflow_id: string;
    workflow_version: string;
    status: WorkflowRunStatus;
    inputs: Record<string, unknown>;
    outputs: Record<string, unknown>;
    error: string | null;
    created_at: string;
    updated_at: string;
  };
  steps: WorkflowStepRun[];
  definition: WorkflowDefinition | null;
}

export interface VideoGateway {
  getRuntimeConfig(): Promise<RuntimeConfig>;
  getWorkflows(): Promise<WorkflowDefinition[]>;
  getWorkflowRun(jobId: string): Promise<WorkflowSnapshot>;
  createVideo(input: CreateVideoInput): Promise<VideoJob>;
  getVideo(jobId: string): Promise<VideoJob>;
  regenerateScene(jobId: string, sceneNumber: number, shotNumber: number, prompt: string): Promise<VideoJob>;
  approveVisuals(jobId: string): Promise<VideoJob>;
  decideDuration(jobId: string, decision: "summarize" | "accept" | "cancel"): Promise<VideoJob>;
  resumeVideo(jobId: string): Promise<VideoJob>;
  assetUrl(path: string): string;
}
