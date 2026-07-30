import type {
  CreateVideoInput,
  BrandKit,
  BrandKitUpdate,
  ProductionPreset,
  RuntimeConfig,
  VideoGateway,
  VideoJob,
  WorkflowDefinition,
  WorkflowSnapshot,
} from "./contracts";

interface ApiErrorPayload {
  detail?: string;
}

export class HttpVideoGatewayError extends Error {
  constructor(message: string, readonly status: number) {
    super(message);
    this.name = "HttpVideoGatewayError";
  }
}

export class HttpVideoGateway implements VideoGateway {
  constructor(private readonly baseUrl = import.meta.env.VITE_API_BASE_URL ?? "") {}

  async getRuntimeConfig(): Promise<RuntimeConfig> {
    return this.request<RuntimeConfig>("/v1/config");
  }

  async getProductionPresets(): Promise<ProductionPreset[]> {
    return this.request<ProductionPreset[]>("/v1/production-presets");
  }

  async getBrandKit(): Promise<BrandKit> {
    return this.request<BrandKit>("/v1/brand-kit");
  }

  async updateBrandKit(input: BrandKitUpdate): Promise<BrandKit> {
    return this.request<BrandKit>("/v1/brand-kit", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(input),
    });
  }

  async uploadBrandAsset(kind: "logo" | "opening_image" | "closing_image", file: File): Promise<BrandKit> {
    const form = new FormData();
    form.append("file", file);
    return this.request<BrandKit>(`/v1/brand-kit/assets/${kind}`, {
      method: "POST",
      body: form,
    });
  }

  async getWorkflows(): Promise<WorkflowDefinition[]> {
    return this.request<WorkflowDefinition[]>("/v1/workflows");
  }

  async getWorkflowRun(jobId: string): Promise<WorkflowSnapshot> {
    return this.request<WorkflowSnapshot>(`/v1/workflow-runs/${jobId}`);
  }

  async createVideo(input: CreateVideoInput): Promise<VideoJob> {
    const form = new FormData();
    form.append("file", input.file);
    form.append("target_seconds", String(input.targetSeconds));
    form.append("language", input.language);
    form.append("audience", input.audience);
    form.append("tone", input.tone);
    form.append("production_mode", input.productionMode);
    form.append("preset_options", JSON.stringify(input.presetOptions));

    return this.request<VideoJob>("/v1/videos", {
      method: "POST",
      body: form,
    });
  }

  async getVideo(jobId: string): Promise<VideoJob> {
    return this.request<VideoJob>(`/v1/videos/${jobId}`);
  }

  async regenerateScene(jobId: string, sceneNumber: number, shotNumber: number, prompt: string): Promise<VideoJob> {
    return this.request<VideoJob>(`/v1/videos/${jobId}/scenes/${sceneNumber}/regenerate?shot_number=${shotNumber}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ prompt }),
    });
  }

  async useSourceSlide(jobId: string, sceneNumber: number, shotNumber: number, sourceSlideNumber: number): Promise<VideoJob> {
    return this.request<VideoJob>(`/v1/videos/${jobId}/scenes/${sceneNumber}/use-source-slide?shot_number=${shotNumber}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ source_slide_number: sourceSlideNumber }),
    });
  }

  async generateFromSourceSlide(jobId: string, sceneNumber: number, shotNumber: number, sourceSlideNumber: number, prompt: string): Promise<VideoJob> {
    return this.request<VideoJob>(`/v1/videos/${jobId}/scenes/${sceneNumber}/generate-from-source-slide?shot_number=${shotNumber}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ source_slide_number: sourceSlideNumber, prompt }),
    });
  }

  async approveVisuals(jobId: string): Promise<VideoJob> {
    return this.request<VideoJob>(`/v1/videos/${jobId}/approve-visuals`, { method: "POST" });
  }

  async decideDuration(jobId: string, decision: "summarize" | "accept" | "cancel"): Promise<VideoJob> {
    return this.request<VideoJob>(`/v1/videos/${jobId}/duration-decision`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ decision }),
    });
  }

  async cancelVideo(jobId: string): Promise<VideoJob> {
    return this.request<VideoJob>(`/v1/videos/${jobId}/cancel`, { method: "POST" });
  }

  async resumeVideo(jobId: string): Promise<VideoJob> {
    return this.request<VideoJob>(`/v1/videos/${jobId}/resume`, { method: "POST" });
  }

  assetUrl(path: string): string {
    if (/^https?:\/\//i.test(path)) {
      return path;
    }
    return `${this.baseUrl}${path}`;
  }

  private async request<T>(path: string, init?: RequestInit): Promise<T> {
    const response = await fetch(`${this.baseUrl}${path}`, init);
    if (!response.ok) {
      let message = `Erro ${response.status}`;
      try {
        const payload = (await response.json()) as ApiErrorPayload;
        message = payload.detail ?? message;
      } catch {
        // The server did not return JSON; keep the HTTP status message.
      }
      throw new HttpVideoGatewayError(message, response.status);
    }
    return (await response.json()) as T;
  }
}
