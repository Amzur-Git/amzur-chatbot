import { useEffect, useRef } from "react";

export default function ChatComposer({
  value,
  onChange,
  onSend,
  sending,
  attachments,
  onPickFiles,
  onRemoveAttachment,
  uploading,
  imageOptions,
  onImageOptionsChange,
  dbQueryMode,
  onToggleDbQueryMode,
  focusKey,
}) {
  const fileInputRef = useRef(null);
  const textareaRef = useRef(null);

  const updateImageOptions = (patch) => {
    onImageOptionsChange({
      ...imageOptions,
      ...patch,
    });
  };

  const syncTextareaHeight = () => {
    const textarea = textareaRef.current;
    if (!textarea) {
      return;
    }

    textarea.style.height = "auto";

    const maxHeight = parseFloat(window.getComputedStyle(textarea).maxHeight) || 120;
    const nextHeight = Math.min(textarea.scrollHeight, maxHeight);

    textarea.style.height = `${nextHeight}px`;
    textarea.style.overflowY = textarea.scrollHeight > maxHeight ? "auto" : "hidden";
  };

  useEffect(() => {
    if (textareaRef.current) {
      textareaRef.current.focus();
    }
  }, [focusKey]);

  useEffect(() => {
    syncTextareaHeight();
  }, [value]);

  const handleSubmit = (event) => {
    event.preventDefault();
    onSend();
  };

  const handlePickClick = () => {
    fileInputRef.current?.click();
  };

  const handleFilesChange = (event) => {
    const files = Array.from(event.target.files || []);
    if (files.length > 0) {
      onPickFiles(files);
    }
    event.target.value = "";
  };

  const handleTextareaKeyDown = (event) => {
    if (event.key !== "Enter" || event.shiftKey || event.nativeEvent?.isComposing) {
      return;
    }

    const canSend = !sending && !uploading && (value.trim() || attachments?.length > 0);
    if (!canSend) {
      return;
    }

    event.preventDefault();
    onSend();
  };

  return (
    <form className="chat-composer" onSubmit={handleSubmit}>
      <input
        ref={fileInputRef}
        type="file"
        multiple
        className="composer-file-input"
        onChange={handleFilesChange}
        disabled={sending || uploading}
      />

      {attachments?.length ? (
        <div className="composer-attachments">
          {attachments.map((attachment) => (
            <div className="composer-attachment-item" key={attachment.client_id || attachment.id}>
              <span className="composer-attachment-item__name">{attachment.file_name}</span>
              <span className="composer-attachment-item__meta">
                {attachment.uploading ? `${attachment.progress ?? 0}%` : attachment.file_type}
              </span>
              <button
                type="button"
                onClick={() => onRemoveAttachment(attachment)}
                disabled={sending || attachment.uploading}
              >
                Remove
              </button>
            </div>
          ))}
        </div>
      ) : null}

      <textarea
        ref={textareaRef}
        value={value}
        onChange={(event) => onChange(event.target.value)}
        onKeyDown={handleTextareaKeyDown}
        placeholder={dbQueryMode ? "Ask a question about your database..." : "Ask anything..."}
        rows={2}
        disabled={sending || uploading}
      />

      <p className="composer-hint">Press Enter to send</p>

      <button
        className={`secondary-btn composer-db-toggle ${dbQueryMode ? "composer-db-toggle--active" : ""}`}
        type="button"
        onClick={onToggleDbQueryMode}
        disabled={sending || uploading}
        aria-label={dbQueryMode ? "Disable DB Query Mode" : "Enable DB Query Mode"}
        title={dbQueryMode ? "Disable DB Query Mode" : "Enable DB Query Mode"}
      >
        <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">
          <ellipse cx="12" cy="5" rx="7" ry="3" fill="none" stroke="currentColor" strokeWidth="1.8" />
          <path d="M5 5v10c0 1.7 3.1 3 7 3s7-1.3 7-3V5" fill="none" stroke="currentColor" strokeWidth="1.8" />
          <path d="M5 10c0 1.7 3.1 3 7 3s7-1.3 7-3" fill="none" stroke="currentColor" strokeWidth="1.8" />
        </svg>
      </button>

      {!dbQueryMode ? (
        <details className="composer-image-popover">
          <summary
            className="secondary-btn composer-image-popover__trigger composer-image-btn"
            aria-label="Advanced image settings"
            title="Advanced image settings"
          >
            <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">
              <path
                d="M4 7h8M16 7h4M10 7a2 2 0 104 0 2 2 0 00-4 0zM4 17h4M12 17h8M8 17a2 2 0 104 0 2 2 0 00-4 0z"
                fill="none"
                stroke="currentColor"
                strokeWidth="1.8"
                strokeLinecap="round"
                strokeLinejoin="round"
              />
            </svg>
          </summary>

          <div className="composer-image-popover__content">
            <div className="composer-image-popover__controls">
              <label>
                Images
                <input
                  type="number"
                  min={1}
                  max={4}
                  value={imageOptions?.numImages ?? 1}
                  onChange={(event) =>
                    updateImageOptions({ numImages: Number(event.target.value || 1) })
                  }
                  disabled={sending || uploading}
                />
              </label>

              <label>
                Aspect Ratio
                <select
                  value={imageOptions?.aspectRatio || "1:1"}
                  onChange={(event) => updateImageOptions({ aspectRatio: event.target.value })}
                  disabled={sending || uploading}
                >
                  <option value="1:1">1:1</option>
                  <option value="3:4">3:4</option>
                  <option value="4:3">4:3</option>
                  <option value="16:9">16:9</option>
                  <option value="9:16">9:16</option>
                </select>
              </label>

              <label className="composer-image-popover__enhance">
                <input
                  type="checkbox"
                  checked={Boolean(imageOptions?.enhancePrompt)}
                  onChange={(event) =>
                    updateImageOptions({ enhancePrompt: event.target.checked })
                  }
                  disabled={sending || uploading}
                />
                Enhance prompt
              </label>
            </div>

            <label className="composer-image-popover__negative-group">
              <span>Negative Prompt (optional)</span>
              <textarea
                value={imageOptions?.negativePrompt || ""}
                onChange={(event) =>
                  updateImageOptions({ negativePrompt: event.target.value })
                }
                placeholder="e.g., low quality, blurry, watermark"
                rows={2}
                className="composer-image-popover__negative"
                disabled={sending || uploading}
              />
            </label>
          </div>
        </details>
      ) : null}

      <button
        className="secondary-btn composer-attach-btn"
        type="button"
        onClick={handlePickClick}
        disabled={sending || uploading}
        aria-label={uploading ? "Uploading files" : "Attach files"}
        title={uploading ? "Uploading files" : "Attach files"}
      >
        <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">
          <path
            d="M21.44 11.05l-8.49 8.49a6 6 0 11-8.49-8.49l8.49-8.49a4 4 0 115.66 5.66l-8.49 8.49a2 2 0 11-2.83-2.83l7.78-7.78"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.8"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        </svg>
      </button>

      <button
        className="primary-btn composer-send-btn"
        type="submit"
        aria-label={sending ? "Sending message" : "Send message"}
        title={sending ? "Sending..." : "Send"}
        disabled={
          sending ||
          uploading ||
          (!value.trim() && !(attachments?.length > 0))
        }
      >
        <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">
          <path
            d="M4 12l15-7-4 7 4 7-15-7zm0 0h11"
            transform="translate(24 0) scale(-1 1)"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.8"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        </svg>
      </button>
    </form>
  );
}
