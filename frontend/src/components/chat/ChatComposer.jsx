export default function ChatComposer({ value, onChange, onSend, sending }) {
  const handleSubmit = (event) => {
    event.preventDefault();
    onSend();
  };

  return (
    <form className="chat-composer" onSubmit={handleSubmit}>
      <textarea
        value={value}
        onChange={(event) => onChange(event.target.value)}
        placeholder="Ask anything..."
        rows={2}
        disabled={sending}
      />

      <button className="primary-btn" type="submit" disabled={sending || !value.trim()}>
        {sending ? "Sending..." : "Send"}
      </button>
    </form>
  );
}
