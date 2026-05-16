import { useMemo, useState } from "react";
import katex from "katex";
import { Prism as SyntaxHighlighter } from "react-syntax-highlighter";
import { oneDark } from "react-syntax-highlighter/dist/esm/styles/prism";

import { attachmentsApi } from "../../lib/api";
import "katex/dist/katex.min.css";

function downloadAttachment(attachmentId, fileName) {
  const url = attachmentsApi.downloadUrl(attachmentId);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.target = "_blank";
  anchor.rel = "noopener noreferrer";
  anchor.download = fileName;
  anchor.click();
}

function ImageCard({ attachment }) {
  const metadata = attachment.metadata || {};
  const isGenerated = Boolean(metadata.generated);
  const isTruncated = typeof metadata.base64_preview === "string" && metadata.base64_preview.includes("[truncated]");
  const preview = metadata.base64_preview && !isTruncated
    ? `data:image/*;base64,${metadata.base64_preview}`
    : null;
  const [canShowPreview, setCanShowPreview] = useState(true);
  const previewUrl = preview || attachmentsApi.downloadUrl(attachment.id);

  return (
    <div className={`att-card att-card--image ${isGenerated ? "att-card--generated" : ""}`}>
      {canShowPreview ? (
        <img
          src={previewUrl}
          alt={attachment.file_name}
          loading="lazy"
          onError={() => setCanShowPreview(false)}
        />
      ) : (
        <p className="muted">Preview unavailable</p>
      )}
      <p>{attachment.file_name}</p>
      {isGenerated ? (
        <div className="generated-image-meta">
          <span className="generated-image-badge">Generated</span>
          <span className="muted">{metadata.aspect_ratio || "1:1"}</span>
          <span className="muted">{metadata.source || "gemini"}</span>
        </div>
      ) : null}
      <button type="button" onClick={() => downloadAttachment(attachment.id, attachment.file_name)}>
        Download
      </button>
    </div>
  );
}

function VideoCard({ attachment }) {
  const metadata = attachment.metadata || {};

  return (
    <div className="att-card">
      <p>{attachment.file_name}</p>
      <p className="muted">Duration: {metadata.duration_seconds ?? "unknown"}s</p>
      <button type="button" onClick={() => downloadAttachment(attachment.id, attachment.file_name)}>
        Download
      </button>
    </div>
  );
}

function TableCard({ attachment }) {
  const metadata = attachment.metadata || {};
  const rows = metadata.sample || [];
  const columns = metadata.column_names || [];

  return (
    <div className="att-card att-card--table">
      <p>{attachment.file_name}</p>
      <p className="muted">
        {metadata.rows ?? "?"} rows x {metadata.columns ?? "?"} columns
      </p>
      {columns.length > 0 ? (
        <div className="att-table-wrap">
          <table>
            <thead>
              <tr>
                {columns.map((column) => (
                  <th key={column}>{column}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((row, index) => (
                <tr key={`${attachment.id}-${index}`}>
                  {columns.map((column) => (
                    <td key={`${column}-${index}`}>{String(row[column] ?? "")}</td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
      <button type="button" onClick={() => downloadAttachment(attachment.id, attachment.file_name)}>
        Download
      </button>
    </div>
  );
}

function CodeCard({ attachment }) {
  const metadata = attachment.metadata || {};
  const code = metadata.content || "";
  const language = metadata.language || "text";

  return (
    <div className="att-card att-card--code">
      <p>{attachment.file_name}</p>
      <SyntaxHighlighter language={language} style={oneDark} PreTag="div">
        {code}
      </SyntaxHighlighter>
      <button type="button" onClick={() => downloadAttachment(attachment.id, attachment.file_name)}>
        Download
      </button>
    </div>
  );
}

function FormulaCard({ attachment }) {
  const metadata = attachment.metadata || {};
  const latex = metadata.latex || "";

  const html = useMemo(() => {
    try {
      return katex.renderToString(latex, { throwOnError: false, displayMode: true });
    } catch {
      return `<pre>${latex}</pre>`;
    }
  }, [latex]);

  return (
    <div className="att-card att-card--formula">
      <p>{attachment.file_name}</p>
      <div dangerouslySetInnerHTML={{ __html: html }} />
      <button type="button" onClick={() => downloadAttachment(attachment.id, attachment.file_name)}>
        Download
      </button>
    </div>
  );
}

export default function AttachmentRenderer({ attachments }) {
  const hasGeneratedImage = attachments?.some(
    (attachment) => attachment.file_type === "image" && Boolean(attachment.metadata?.generated),
  );
  const [expanded, setExpanded] = useState(Boolean(hasGeneratedImage));

  const isExpanded = hasGeneratedImage ? true : expanded;

  if (!attachments || attachments.length === 0) {
    return null;
  }

  return (
    <section className="bubble-attachments">
      <div className="bubble-attachments__header">
        <span>{attachments.length} attachment{attachments.length > 1 ? "s" : ""}</span>
        <button type="button" onClick={() => setExpanded((current) => !current)}>
          {isExpanded ? "Hide" : "Show"}
        </button>
      </div>

      {isExpanded ? (
        <div className="bubble-attachments__grid">
          {attachments.map((attachment) => {
            if (attachment.file_type === "image") {
              return (
                <ImageCard
                  key={attachment.id}
                  attachment={attachment}
                />
              );
            }
            if (attachment.file_type === "video") {
              return <VideoCard key={attachment.id} attachment={attachment} />;
            }
            if (attachment.file_type === "table") {
              return <TableCard key={attachment.id} attachment={attachment} />;
            }
            if (attachment.file_type === "code") {
              return <CodeCard key={attachment.id} attachment={attachment} />;
            }
            if (attachment.file_type === "formula") {
              return <FormulaCard key={attachment.id} attachment={attachment} />;
            }

            return (
              <div className="att-card" key={attachment.id}>
                <p>{attachment.file_name}</p>
                <button
                  type="button"
                  onClick={() => downloadAttachment(attachment.id, attachment.file_name)}
                >
                  Download
                </button>
              </div>
            );
          })}
        </div>
      ) : null}
    </section>
  );
}
