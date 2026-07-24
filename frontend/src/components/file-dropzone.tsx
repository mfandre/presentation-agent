import { FileText, Presentation, UploadCloud, X } from "lucide-react";
import { useRef, useState } from "react";

import { formatBytes } from "../utils/format";

interface FileDropzoneProps {
  file: File | null;
  disabled?: boolean;
  onChange(file: File | null): void;
  onError(message: string | null): void;
}

const ALLOWED_EXTENSIONS = [".pptx", ".pdf"];
const MAX_BYTES = 100 * 1024 * 1024;

function validateFile(file: File): string | null {
  const lowerName = file.name.toLowerCase();
  if (!ALLOWED_EXTENSIONS.some((extension) => lowerName.endsWith(extension))) {
    return "Selecione um arquivo PPTX ou PDF.";
  }
  if (file.size > MAX_BYTES) {
    return "O arquivo deve ter no máximo 100 MB.";
  }
  return null;
}

export function FileDropzone({ file, disabled, onChange, onError }: FileDropzoneProps) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [isDragging, setIsDragging] = useState(false);

  const acceptFile = (candidate?: File) => {
    if (!candidate) return;
    const validationError = validateFile(candidate);
    onError(validationError);
    if (!validationError) onChange(candidate);
  };

  if (file) {
    const isPdf = file.name.toLowerCase().endsWith(".pdf");
    const Icon = isPdf ? FileText : Presentation;
    return (
      <div className="selected-file" aria-label="Arquivo selecionado">
        <div className="selected-file__icon">
          <Icon size={23} aria-hidden="true" />
        </div>
        <div className="selected-file__content">
          <strong title={file.name}>{file.name}</strong>
          <span>{formatBytes(file.size)} · {isPdf ? "PDF" : "PowerPoint"}</span>
        </div>
        <button
          type="button"
          className="icon-button"
          onClick={() => onChange(null)}
          disabled={disabled}
          aria-label="Remover arquivo"
        >
          <X size={18} />
        </button>
      </div>
    );
  }

  return (
    <button
      type="button"
      className={`dropzone ${isDragging ? "dropzone--active" : ""}`}
      onClick={() => inputRef.current?.click()}
      onDragEnter={(event) => {
        event.preventDefault();
        setIsDragging(true);
      }}
      onDragOver={(event) => event.preventDefault()}
      onDragLeave={(event) => {
        event.preventDefault();
        setIsDragging(false);
      }}
      onDrop={(event) => {
        event.preventDefault();
        setIsDragging(false);
        acceptFile(event.dataTransfer.files[0]);
      }}
      disabled={disabled}
    >
      <input
        ref={inputRef}
        hidden
        type="file"
        accept=".pptx,.pdf,application/pdf,application/vnd.openxmlformats-officedocument.presentationml.presentation"
        onChange={(event) => acceptFile(event.target.files?.[0])}
      />
      <span className="dropzone__icon"><UploadCloud size={27} /></span>
      <strong>Arraste sua apresentação</strong>
      <span>ou clique para selecionar um arquivo</span>
      <small>PPTX ou PDF · até 100 MB</small>
    </button>
  );
}
