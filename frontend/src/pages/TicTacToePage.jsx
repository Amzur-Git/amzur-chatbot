import { useMemo, useState } from "react";
import { Link } from "react-router-dom";

const WIN_LINES = [
  [0, 1, 2],
  [3, 4, 5],
  [6, 7, 8],
  [0, 3, 6],
  [1, 4, 7],
  [2, 5, 8],
  [0, 4, 8],
  [2, 4, 6],
];

const EMPTY_BOARD = Array(9).fill(null);
const HUMAN = "X";
const AI = "O";

function findWinner(board) {
  for (const [a, b, c] of WIN_LINES) {
    if (board[a] && board[a] === board[b] && board[a] === board[c]) {
      return board[a];
    }
  }
  return null;
}

function isDraw(board) {
  return !findWinner(board) && board.every((cell) => cell !== null);
}

function availableMoves(board) {
  const moves = [];
  for (let index = 0; index < board.length; index += 1) {
    if (!board[index]) {
      moves.push(index);
    }
  }
  return moves;
}

function minimax(board, maximizing, alpha, beta, depth) {
  const winner = findWinner(board);
  if (winner === AI) {
    return { score: 10 - depth, move: null };
  }
  if (winner === HUMAN) {
    return { score: depth - 10, move: null };
  }
  if (isDraw(board)) {
    return { score: 0, move: null };
  }

  if (maximizing) {
    let bestScore = -Infinity;
    let bestMove = null;

    for (const move of availableMoves(board)) {
      const nextBoard = [...board];
      nextBoard[move] = AI;
      const result = minimax(nextBoard, false, alpha, beta, depth + 1);

      if (result.score > bestScore) {
        bestScore = result.score;
        bestMove = move;
      }

      alpha = Math.max(alpha, bestScore);
      if (beta <= alpha) {
        break;
      }
    }

    return { score: bestScore, move: bestMove };
  }

  let bestScore = Infinity;
  let bestMove = null;

  for (const move of availableMoves(board)) {
    const nextBoard = [...board];
    nextBoard[move] = HUMAN;
    const result = minimax(nextBoard, true, alpha, beta, depth + 1);

    if (result.score < bestScore) {
      bestScore = result.score;
      bestMove = move;
    }

    beta = Math.min(beta, bestScore);
    if (beta <= alpha) {
      break;
    }
  }

  return { score: bestScore, move: bestMove };
}

function getBestAIMove(board) {
  return minimax(board, true, -Infinity, Infinity, 0).move;
}

export default function TicTacToePage() {
  const [board, setBoard] = useState(EMPTY_BOARD);
  const [turn, setTurn] = useState(HUMAN);

  const winner = useMemo(() => findWinner(board), [board]);
  const draw = useMemo(() => isDraw(board), [board]);
  const gameOver = Boolean(winner || draw);

  const statusText = useMemo(() => {
    if (winner === HUMAN) {
      return "You win.";
    }
    if (winner === AI) {
      return "AI wins.";
    }
    if (draw) {
      return "Draw game.";
    }
    return turn === HUMAN ? "Your turn." : "AI is thinking.";
  }, [draw, turn, winner]);

  const handleReset = () => {
    setBoard(EMPTY_BOARD);
    setTurn(HUMAN);
  };

  const handleMove = (index) => {
    if (gameOver || turn !== HUMAN || board[index]) {
      return;
    }

    const afterHuman = [...board];
    afterHuman[index] = HUMAN;

    if (findWinner(afterHuman) || isDraw(afterHuman)) {
      setBoard(afterHuman);
      return;
    }

    const aiMove = getBestAIMove(afterHuman);
    if (aiMove === null || aiMove === undefined) {
      setBoard(afterHuman);
      return;
    }

    const afterAI = [...afterHuman];
    afterAI[aiMove] = AI;
    setBoard(afterAI);
    setTurn(HUMAN);
  };

  return (
    <main className="ttt-layout">
      <section className="ttt-panel">
        <div className="ttt-header">
          <p className="eyebrow">Project 11</p>
          <h1>Tic Tac Toe Agent</h1>
          <p className="muted">Standard minimax with alpha-beta pruning. You are X, AI is O.</p>
        </div>

        <p className="ttt-status" aria-live="polite">
          {statusText}
        </p>

        <div className="ttt-grid" role="grid" aria-label="Tic Tac Toe board">
          {board.map((cell, index) => (
            <button
              key={index}
              type="button"
              className="ttt-cell"
              role="gridcell"
              aria-label={`Cell ${index + 1}${cell ? `, ${cell}` : ""}`}
              onClick={() => handleMove(index)}
              disabled={gameOver || turn !== HUMAN || Boolean(cell)}
            >
              {cell || ""}
            </button>
          ))}
        </div>

        <div className="ttt-actions">
          <button className="primary-btn" type="button" onClick={handleReset}>
            New Game
          </button>
          <Link className="secondary-btn research-link" to="/chat">
            Back To Chat
          </Link>
        </div>
      </section>
    </main>
  );
}
