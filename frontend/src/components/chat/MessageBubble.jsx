import { useEffect, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { Prism as SyntaxHighlighter } from "react-syntax-highlighter";
import { oneDark } from "react-syntax-highlighter/dist/esm/styles/prism";
import { Check, Copy, Pencil, RotateCcw } from "lucide-react";
import AttachmentRenderer from "../attachments/AttachmentRenderer";

export default function MessageBubble({
  message,
  canEdit = false,
  editing = false,
  editingValue = "",
  onEditingChange,
  onStartEdit,
  onCancelEdit,
  onSubmitEdit,
  editSubmitting = false,
  canRetry = false,
  onRetry,
  retrying = false,
}) {
  const [copyState, setCopyState] = useState("idle");
  const isUser = message.role === "user";
  const intermediateSteps = Array.isArray(message?.metadata?.intermediate_steps)
    ? message.metadata.intermediate_steps
    : [];

  useEffect(() => {
    if (copyState !== "copied") {
      return;
    }

    const timeout = window.setTimeout(() => setCopyState("idle"), 1400);
    return () => window.clearTimeout(timeout);
  }, [copyState]);

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(String(message.content || ""));
      setCopyState("copied");
    } catch {
      setCopyState("error");
      window.setTimeout(() => setCopyState("idle"), 1400);
    }
  };

  const handleEditSubmit = () => {
    if (!onSubmitEdit) {
      return;
    }
    onSubmitEdit();
  };

  return (
    <article className={`bubble ${isUser ? "bubble--user" : "bubble--assistant"}`}>
      <header className="bubble__meta">
        <span>{isUser ? "You" : "Assistant"}</span>
      </header>

      {!editing ? (
        <div className="bubble__actions" role="toolbar" aria-label="Message actions">
          <button
            type="button"
            className="bubble__action-btn"
            onClick={handleCopy}
            aria-label={copyState === "copied" ? "Copied" : "Copy message"}
            title={copyState === "copied" ? "Copied" : "Copy"}
          >
            {copyState === "copied" ? <Check size={14} /> : <Copy size={14} />}
          </button>

          {canEdit ? (
            <button
              type="button"
              className="bubble__action-btn"
              onClick={onStartEdit}
              aria-label="Edit message"
              title="Edit"
              disabled={editSubmitting}
            >
              <Pencil size={14} />
            </button>
          ) : null}

          {canRetry ? (
            <button
              type="button"
              className="bubble__action-btn"
              onClick={onRetry}
              aria-label="Retry response"
              title="Retry"
              disabled={retrying}
            >
              <RotateCcw size={14} />
            </button>
          ) : null}
        </div>
      ) : null}

      <div className="bubble__body markdown-content">
        {editing ? (
          <div className="bubble__edit">
            <textarea
              value={editingValue}
              onChange={(event) => onEditingChange?.(event.target.value)}
              rows={4}
              disabled={editSubmitting}
              aria-label="Edit message"
            />
            <div className="bubble__edit-actions">
              <button
                type="button"
                className="secondary-btn"
                onClick={onCancelEdit}
                disabled={editSubmitting}
              >
                Cancel
              </button>
              <button
                type="button"
                className="primary-btn"
                onClick={handleEditSubmit}
                disabled={editSubmitting || !String(editingValue || "").trim()}
              >
                {editSubmitting ? "Saving..." : "Save & Regenerate"}
              </button>
            </div>
          </div>
        ) : isUser ? (
          <p>{message.content}</p>
        ) : (
          <ReactMarkdown
            remarkPlugins={[remarkGfm]}
            components={{
              code(props) {
                const { children, className, ...rest } = props;
                const match = /language-(\w+)/.exec(className || "");
                const codeText = String(children).replace(/\n$/, "");
                return match ? (
                  <SyntaxHighlighter
                    {...rest}
                    PreTag="div"
                    language={match[1]}
                    style={oneDark}
                    customStyle={{
                      margin: "0.45rem 0",
                      padding: "0.56rem 0.7rem",
                      borderRadius: "7px",
                    }}
                    codeTagProps={{
                      style: {
                        fontSize: "0.84rem",
                        lineHeight: 1.45,
                      },
                    }}
                  >
                    {codeText}
                  </SyntaxHighlighter>
                ) : (
                  <code {...rest} className={className}>
                    {children}
                  </code>
                );
              },
            }}
          >
            {message.content}
          </ReactMarkdown>
        )}
      </div>

      <AttachmentRenderer attachments={message.attachments || []} />

      {!isUser && intermediateSteps.length > 0 ? (
        <details className="bubble__intermediate">
          <summary>Show reasoning steps ({intermediateSteps.length})</summary>
          <div className="bubble__intermediate-content">
            {intermediateSteps.map((step, index) => (
              <div className="bubble__intermediate-item" key={`${message.id}-step-${index}`}>
                {step?.tool ? <p><strong>Tool:</strong> {String(step.tool)}</p> : null}
                {step?.tool_input ? <p><strong>Input:</strong> {String(step.tool_input)}</p> : null}
                {step?.observation ? <p><strong>Observation:</strong> {String(step.observation)}</p> : null}
                {step?.log ? <p className="muted"><strong>Log:</strong> {String(step.log)}</p> : null}
                {!step?.tool && !step?.tool_input && !step?.observation && !step?.log ? (
                  <p>{String(step?.detail || "No details")}</p>
                ) : null}
              </div>
            ))}
          </div>
        </details>
      ) : null}
    </article>
  );
}
