import { useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";

import { API_BASE_URL } from "../lib/api";

function parseSseBlock(block) {
  const lines = block.split("\n");
  let event = "message";
  const dataLines = [];

  for (const line of lines) {
    if (line.startsWith("event:")) {
      event = line.slice(6).trim();
      continue;
    }
    if (line.startsWith("data:")) {
      dataLines.push(line.slice(5).trim());
    }
  }

  const rawData = dataLines.join("\n");
  let data = null;
  try {
    data = rawData ? JSON.parse(rawData) : null;
  } catch {
    data = { message: rawData };
  }

  return { event, data };
}

function keepUniqueByEntryId(items) {
  const byId = new Map();
  for (const item of items) {
    if (item?.entry_id) {
      byId.set(item.entry_id, item);
    }
  }
  return Array.from(byId.values());
}

export default function ResearchDigestPage() {
  const [topic, setTopic] = useState("large language model agents for scientific discovery");
  const [maxRounds, setMaxRounds] = useState(3);
  const [papersPerRound, setPapersPerRound] = useState(5);
  const [minPapers, setMinPapers] = useState(6);

  const [isRunning, setIsRunning] = useState(false);
  const [statusLines, setStatusLines] = useState([]);
  const [papers, setPapers] = useState([]);
  const [decisions, setDecisions] = useState([]);
  const [digest, setDigest] = useState("");
  const [error, setError] = useState("");

  const abortRef = useRef(null);

  const canRun = topic.trim().length >= 3 && !isRunning;

  const stats = useMemo(() => {
    return {
      papers: papers.length,
      decisions: decisions.length,
      status: statusLines.length,
    };
  }, [decisions.length, papers.length, statusLines.length]);

  const appendStatus = (message) => {
    const text = String(message || "").trim();
    if (!text) {
      return;
    }
    setStatusLines((prev) => [...prev, text]);
  };

  const handleStop = () => {
    if (abortRef.current) {
      abortRef.current.abort();
      abortRef.current = null;
    }
    setIsRunning(false);
    appendStatus("Streaming stopped by user.");
  };

  const handleSubmit = async (event) => {
    event.preventDefault();
    if (!canRun) {
      return;
    }

    setIsRunning(true);
    setError("");
    setDigest("");
    setPapers([]);
    setDecisions([]);
    setStatusLines([]);

    const controller = new AbortController();
    abortRef.current = controller;

    try {
      const response = await fetch(`${API_BASE_URL}/api/research-digest/stream`, {
        method: "POST",
        credentials: "include",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          topic: topic.trim(),
          max_rounds: Number(maxRounds),
          papers_per_round: Number(papersPerRound),
          min_papers: Number(minPapers),
        }),
        signal: controller.signal,
      });

      if (!response.ok || !response.body) {
        throw new Error(`Request failed with status ${response.status}`);
      }

      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";

      while (true) {
        const { value, done } = await reader.read();
        if (done) {
          break;
        }

        buffer += decoder.decode(value, { stream: true });

        let boundary = buffer.indexOf("\n\n");
        while (boundary !== -1) {
          const block = buffer.slice(0, boundary).trim();
          buffer = buffer.slice(boundary + 2);

          if (block) {
            const { event: eventName, data } = parseSseBlock(block);

            if (eventName === "status") {
              appendStatus(data?.message || "Working...");
            } else if (eventName === "papers") {
              const payload = Array.isArray(data?.papers) ? data.papers : [];
              setPapers((prev) => keepUniqueByEntryId([...prev, ...payload]));
              appendStatus(
                `Round ${data?.round ?? "?"}: +${data?.new_count ?? payload.length} papers (total ${data?.total_count ?? "?"})`
              );
            } else if (eventName === "decision") {
              setDecisions((prev) => [...prev, data]);
              appendStatus(`Decision: ${data?.reason || "Continuing..."}`);
            } else if (eventName === "digest_chunk") {
              setDigest((prev) => prev + (data?.chunk || ""));
            } else if (eventName === "final_digest") {
              if (typeof data?.digest === "string" && data.digest.trim()) {
                setDigest(data.digest);
              }
              if (Array.isArray(data?.papers)) {
                setPapers(keepUniqueByEntryId(data.papers));
              }
              appendStatus("Digest complete.");
            } else if (eventName === "error") {
              const message = data?.message || "The digest agent reported an error.";
              setError(message);
              appendStatus(message);
            } else if (eventName === "done") {
              appendStatus(data?.ok ? "Done." : "Finished with errors.");
              setIsRunning(false);
            }
          }

          boundary = buffer.indexOf("\n\n");
        }
      }
    } catch (requestError) {
      if (requestError?.name !== "AbortError") {
        const message = requestError?.message || "Failed to stream research digest.";
        setError(message);
        appendStatus(message);
      }
    } finally {
      abortRef.current = null;
      setIsRunning(false);
    }
  };

  return (
    <main className="research-layout">
      <section className="research-panel research-panel--controls">
        <div className="research-header">
          <p className="eyebrow">Project 10</p>
          <h1>Research Digest Agent</h1>
          <p className="muted">
            Autonomous arXiv exploration with real-time digest streaming.
          </p>
        </div>

        <form className="research-form" onSubmit={handleSubmit}>
          <label className="field">
            <span>Topic</span>
            <textarea
              value={topic}
              onChange={(e) => setTopic(e.target.value)}
              rows={4}
              placeholder="Describe the research area to investigate"
            />
          </label>

          <div className="research-grid">
            <label className="field">
              <span>Max Rounds</span>
              <input
                type="number"
                min={1}
                max={5}
                value={maxRounds}
                onChange={(e) => setMaxRounds(e.target.value)}
              />
            </label>
            <label className="field">
              <span>Papers / Round</span>
              <input
                type="number"
                min={3}
                max={10}
                value={papersPerRound}
                onChange={(e) => setPapersPerRound(e.target.value)}
              />
            </label>
            <label className="field">
              <span>Minimum Papers</span>
              <input
                type="number"
                min={3}
                max={20}
                value={minPapers}
                onChange={(e) => setMinPapers(e.target.value)}
              />
            </label>
          </div>

          <div className="research-actions">
            <button className="primary-btn" type="submit" disabled={!canRun}>
              {isRunning ? "Running..." : "Run Agent"}
            </button>
            <button
              className="secondary-btn"
              type="button"
              onClick={handleStop}
              disabled={!isRunning}
            >
              Stop
            </button>
            <Link className="secondary-btn research-link" to="/chat">
              Back To Chat
            </Link>
          </div>
        </form>

        <div className="research-stats">
          <span>Statuses: {stats.status}</span>
          <span>Papers: {stats.papers}</span>
          <span>Decisions: {stats.decisions}</span>
        </div>

        {error ? <p className="error-text">{error}</p> : null}
      </section>

      <section className="research-panel research-panel--stream">
        <div className="research-stream-columns">
          <article className="research-card">
            <h2>Status</h2>
            <div className="research-scroll">
              {statusLines.length === 0 ? <p className="muted">No status yet.</p> : null}
              {statusLines.map((line, index) => (
                <p key={`${line}-${index}`}>{line}</p>
              ))}
            </div>
          </article>

          <article className="research-card">
            <h2>Papers</h2>
            <div className="research-scroll">
              {papers.length === 0 ? <p className="muted">No papers yet.</p> : null}
              {papers.map((paper) => (
                <div className="research-paper" key={paper.entry_id || paper.url || paper.title}>
                  <a href={paper.url} target="_blank" rel="noreferrer">
                    {paper.title}
                  </a>
                  <p className="muted">
                    {paper.published} · {paper.authors}
                  </p>
                </div>
              ))}
            </div>
          </article>
        </div>

        <article className="research-card research-card--digest">
          <h2>Structured Digest</h2>
          <div className="research-scroll">
            {digest ? (
              <pre className="research-digest-text">{digest}</pre>
            ) : (
              <p className="muted">Digest content will stream here.</p>
            )}
          </div>
        </article>
      </section>
    </main>
  );
}
