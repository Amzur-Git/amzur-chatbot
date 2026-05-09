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
