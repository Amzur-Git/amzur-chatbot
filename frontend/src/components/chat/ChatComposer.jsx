import { useRef } from "react";

export default function ChatComposer({
  value,
  onChange,
  onSend,
  sending,
  attachments,
  onPickFiles,
  onRemoveAttachment,
  uploading,
  formulaText,
  onFormulaTextChange,
  imageOptions,
  onImageOptionsChange,
}) {
  const fileInputRef = useRef(null);

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
        value={value}
        onChange={(event) => onChange(event.target.value)}
        placeholder="Ask anything..."
        rows={2}
        disabled={sending || uploading}
      />

      <textarea
        value={formulaText}
        onChange={(event) => onFormulaTextChange(event.target.value)}
        placeholder="Optional LaTeX formula, e.g. \\int_0^1 x^2 dx"
        rows={2}
        className="composer-formula"
        disabled={sending || uploading}
      />

      <div className="composer-image-panel">
        <label className="composer-image-panel__toggle">
          <input
            type="checkbox"
            checked={Boolean(imageOptions?.enabled)}
            onChange={(event) =>
              onImageOptionsChange({
                ...imageOptions,
                enabled: event.target.checked,
              })
            }
            disabled={sending || uploading}
          />
          Generate image(s) from this prompt
        </label>

        {imageOptions?.enabled ? (
          <div className="composer-image-panel__controls">
            <label>
              Images
              <input
                type="number"
                min={1}
                max={4}
                value={imageOptions?.numImages ?? 1}
                onChange={(event) =>
                  onImageOptionsChange({
                    ...imageOptions,
                    numImages: Number(event.target.value || 1),
                  })
                }
                disabled={sending || uploading}
              />
            </label>
            <label>
              Aspect Ratio
              <select
                value={imageOptions?.aspectRatio || "1:1"}
                onChange={(event) =>
                  onImageOptionsChange({
                    ...imageOptions,
                    aspectRatio: event.target.value,
                  })
                }
                disabled={sending || uploading}
              >
                <option value="1:1">1:1</option>
                <option value="3:4">3:4</option>
                <option value="4:3">4:3</option>
                <option value="16:9">16:9</option>
                <option value="9:16">9:16</option>
              </select>
            </label>
            <label className="composer-image-panel__enhance">
              <input
                type="checkbox"
                checked={Boolean(imageOptions?.enhancePrompt)}
                onChange={(event) =>
                  onImageOptionsChange({
                    ...imageOptions,
                    enhancePrompt: event.target.checked,
                  })
                }
                disabled={sending || uploading}
              />
              Enhance prompt
            </label>
          </div>
        ) : null}

        {imageOptions?.enabled ? (
          <textarea
            value={imageOptions?.negativePrompt || ""}
            onChange={(event) =>
              onImageOptionsChange({
                ...imageOptions,
                negativePrompt: event.target.value,
              })
            }
            placeholder="Optional negative prompt, e.g. low quality, blurry, watermark"
            rows={2}
            className="composer-image-panel__negative"
            disabled={sending || uploading}
          />
        ) : null}
      </div>

      <button
        className="secondary-btn"
        type="button"
        onClick={handlePickClick}
        disabled={sending || uploading}
      >
        {uploading ? "Uploading..." : "Attach files"}
      </button>

      <button
        className="primary-btn"
        type="submit"
        disabled={
          sending ||
          uploading ||
          (!value.trim() && !formulaText.trim() && !(attachments?.length > 0))
        }
      >
        {sending ? "Sending..." : "Send"}
      </button>
    </form>
  );
}
