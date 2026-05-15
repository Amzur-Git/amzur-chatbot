import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { Prism as SyntaxHighlighter } from "react-syntax-highlighter";
import { oneDark } from "react-syntax-highlighter/dist/esm/styles/prism";
import AttachmentRenderer from "../attachments/AttachmentRenderer";

export default function MessageBubble({
  message,
  showRetry = false,
  onRetry,
  retrying = false,
}) {
  const isUser = message.role === "user";
  const intermediateSteps = Array.isArray(message?.metadata?.intermediate_steps)
    ? message.metadata.intermediate_steps
    : [];

  return (
    <article className={`bubble ${isUser ? "bubble--user" : "bubble--assistant"}`}>
      <header className="bubble__meta">
        <span>{isUser ? "You" : "Assistant"}</span>
      </header>

      <div className="bubble__body markdown-content">
        {isUser ? (
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

      {isUser && showRetry ? (
        <div className="bubble__retry">
          <button type="button" onClick={onRetry} disabled={retrying}>
            {retrying ? "Retrying..." : "Retry"}
          </button>
        </div>
      ) : null}
    </article>
  );
}
