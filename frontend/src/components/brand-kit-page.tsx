import { useEffect, useState } from "react";
import { ImagePlus, LoaderCircle, Palette, Save, Upload } from "lucide-react";

import type { BrandKit, BrandKitUpdate } from "../api/contracts";
import { HttpVideoGateway } from "../api/http-video-gateway";

const gateway = new HttpVideoGateway();

export function BrandKitPage() {
  const [kit, setKit] = useState<BrandKit | null>(null);
  const [draft, setDraft] = useState<BrandKitUpdate | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

  useEffect(() => {
    gateway.getBrandKit().then((loaded) => {
      setKit(loaded);
      setDraft(toUpdate(loaded));
    }).catch((error: Error) => setMessage(error.message));
  }, []);

  const save = async () => {
    if (!draft) return;
    setBusy(true);
    setMessage(null);
    try {
      const updated = await gateway.updateBrandKit(draft);
      setKit(updated);
      setDraft(toUpdate(updated));
      setMessage(`Identidade salva como versão ${updated.version}.`);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Não foi possível salvar.");
    } finally {
      setBusy(false);
    }
  };

  const upload = async (
    kind: "logo" | "opening_image" | "closing_image",
    file: File | undefined,
  ) => {
    if (!file) return;
    setBusy(true);
    setMessage(null);
    try {
      const updated = await gateway.uploadBrandAsset(kind, file);
      setKit(updated);
      setDraft(toUpdate(updated));
      setMessage(`Ativo atualizado na versão ${updated.version}.`);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Não foi possível enviar o ativo.");
    } finally {
      setBusy(false);
    }
  };

  if (!kit || !draft) {
    return <main className="brand-kit-page"><LoaderCircle className="spin" /> Carregando identidade…</main>;
  }

  const set = <K extends keyof BrandKitUpdate>(key: K, value: BrandKitUpdate[K]) => {
    setDraft((current) => current ? { ...current, [key]: value } : current);
  };

  return (
    <main className="brand-kit-page">
      <header className="brand-kit-hero">
        <span><Palette size={20} /> Identidade global</span>
        <h1>Uma linguagem visual consistente em todos os vídeos.</h1>
        <p>Novos jobs recebem um snapshot desta configuração. Alterações futuras não mudam jobs já iniciados.</p>
      </header>
      {message && <div className="brand-kit-message" role="status">{message}</div>}
      <div className="brand-kit-layout">
        <section className="brand-kit-card">
          <div className="brand-kit-card__heading">
            <div><span>Configuração</span><h2>Marca e direção visual</h2></div>
            <small>versão {kit.version}</small>
          </div>
          <label>Nome da identidade<input value={draft.name} onChange={(event) => set("name", event.target.value)} /></label>
          <div className="brand-color-grid">
            {([
              ["primary_color", "Principal"], ["secondary_color", "Secundária"],
              ["accent_color", "Destaque"], ["background_color", "Fundo"],
            ] as const).map(([key, label]) => (
              <label key={key}>{label}<span className="brand-color-input"><input type="color" value={draft[key]} onChange={(event) => set(key, event.target.value)} /><input value={draft[key]} onChange={(event) => set(key, event.target.value)} /></span></label>
            ))}
          </div>
          <div className="brand-field-grid">
            <label>Fonte de títulos<input value={draft.heading_font} onChange={(event) => set("heading_font", event.target.value)} /></label>
            <label>Fonte de corpo<input value={draft.body_font} onChange={(event) => set("body_font", event.target.value)} /></label>
          </div>
          <label>Direção visual<textarea rows={4} value={draft.visual_style} onChange={(event) => set("visual_style", event.target.value)} /></label>
          <label>Texto dentro das imagens
            <select value={draft.image_text_policy} onChange={(event) => set("image_text_policy", event.target.value as BrandKitUpdate["image_text_policy"])}>
              <option value="avoid">Evitar</option><option value="minimal">Mínimo</option><option value="allowed">Permitido quando necessário</option>
            </select>
          </label>
          <button className="primary-button" type="button" disabled={busy} onClick={() => void save()}>
            {busy ? <LoaderCircle className="spin" size={17} /> : <Save size={17} />} Salvar identidade
          </button>
        </section>
        <section className="brand-kit-card">
          <div className="brand-kit-card__heading"><div><span>Ativos</span><h2>Abertura e encerramento</h2></div><ImagePlus size={22} /></div>
          <AssetInput label="Logo de abertura" url={kit.logo_url} onFile={(file) => void upload("logo", file)} />
          <AssetInput label="Imagem de abertura" url={kit.opening_image_url} onFile={(file) => void upload("opening_image", file)} />
          <AssetInput label="Imagem da cena final" url={kit.closing_image_url} onFile={(file) => void upload("closing_image", file)} />
        </section>
      </div>
    </main>
  );
}

function AssetInput({ label, url, onFile }: { label: string; url: string | null; onFile(file?: File): void }) {
  return <label className="brand-asset"><span>{label}</span><div>{url ? <img src={gateway.assetUrl(url)} alt={label} /> : <ImagePlus size={30} />}</div><span className="secondary-button"><Upload size={15} /> {url ? "Substituir arquivo" : "Enviar arquivo"}</span><input hidden type="file" accept=".png,.jpg,.jpeg,.webp,.svg" onChange={(event) => onFile(event.target.files?.[0])} /></label>;
}

function toUpdate(kit: BrandKit): BrandKitUpdate {
  const { version: _version, logo_url: _logo, opening_image_url: _opening, closing_image_url: _closing, ...update } = kit;
  return update;
}
