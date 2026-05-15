from __future__ import annotations

import math
from typing import List, Optional, Tuple

Board = List[str]
WIN_LINES = [
    (0, 1, 2),
    (3, 4, 5),
    (6, 7, 8),
    (0, 3, 6),
    (1, 4, 7),
    (2, 5, 8),
    (0, 4, 8),
    (2, 4, 6),
]


def print_board(board: Board) -> None:
    def v(idx: int) -> str:
        return board[idx] if board[idx] != " " else str(idx + 1)

    print(f"\n{v(0)} | {v(1)} | {v(2)}")
    print("---------")
    print(f"{v(3)} | {v(4)} | {v(5)}")
    print("---------")
    print(f"{v(6)} | {v(7)} | {v(8)}\n")


def winner(board: Board) -> Optional[str]:
    for a, b, c in WIN_LINES:
        if board[a] != " " and board[a] == board[b] == board[c]:
            return board[a]
    return None


def is_draw(board: Board) -> bool:
    return all(cell != " " for cell in board) and winner(board) is None


def available_moves(board: Board) -> List[int]:
    return [i for i, cell in enumerate(board) if cell == " "]


def minimax(
    board: Board,
    depth: int,
    maximizing: bool,
    ai_symbol: str,
    human_symbol: str,
    alpha: int,
    beta: int,
) -> Tuple[int, Optional[int]]:
    w = winner(board)
    if w == ai_symbol:
        return 10 - depth, None
    if w == human_symbol:
        return depth - 10, None
    if is_draw(board):
        return 0, None

    if maximizing:
        best_score = -math.inf
        best_move: Optional[int] = None
        for move in available_moves(board):
            board[move] = ai_symbol
            score, _ = minimax(
                board,
                depth + 1,
                False,
                ai_symbol,
                human_symbol,
                alpha,
                beta,
            )
            board[move] = " "
            if score > best_score:
                best_score = score
                best_move = move
            alpha = max(alpha, best_score)
            if beta <= alpha:
                break
        return int(best_score), best_move

    best_score = math.inf
    best_move = None
    for move in available_moves(board):
        board[move] = human_symbol
        score, _ = minimax(
            board,
            depth + 1,
            True,
            ai_symbol,
            human_symbol,
            alpha,
            beta,
        )
        board[move] = " "
        if score < best_score:
            best_score = score
            best_move = move
        beta = min(beta, best_score)
        if beta <= alpha:
            break
    return int(best_score), best_move


def choose_human_symbol() -> str:
    while True:
        choice = input("Choose your symbol (X/O): ").strip().upper()
        if choice in {"X", "O"}:
            return choice
        print("Please type X or O.")


def choose_first_player() -> str:
    while True:
        choice = input("Who plays first? (human/ai): ").strip().lower()
        if choice in {"human", "ai"}:
            return choice
        print("Please type 'human' or 'ai'.")


def choose_human_move(board: Board) -> int:
    moves = available_moves(board)
    allowed = {str(m + 1) for m in moves}

    while True:
        move = input("Enter your move (1-9): ").strip()
        if move in allowed:
            return int(move) - 1
        print("Invalid move. Choose an open position from the board.")


def play_game() -> None:
    board: Board = [" "] * 9

    human = choose_human_symbol()
    ai = "O" if human == "X" else "X"
    turn = choose_first_player()

    print("\nYou are", human, "| AI is", ai)

    while True:
        print_board(board)

        if turn == "human":
            move = choose_human_move(board)
            board[move] = human
        else:
            print("AI is thinking...")
            _, best_move = minimax(board, 0, True, ai, human, -math.inf, math.inf)
            if best_move is None:
                # Defensive fallback; should only occur on terminal states.
                break
            board[best_move] = ai
            print(f"AI plays at position {best_move + 1}")

        w = winner(board)
        if w is not None:
            print_board(board)
            if w == human:
                print("You win!")
            else:
                print("AI wins!")
            return

        if is_draw(board):
            print_board(board)
            print("It's a draw.")
            return

        turn = "ai" if turn == "human" else "human"


def main() -> None:
    print("=== Tic Tac Toe Agent ===")
    while True:
        play_game()
        again = input("\nPlay again? (y/n): ").strip().lower()
        if again not in {"y", "yes"}:
            print("Thanks for playing!")
            return


if __name__ == "__main__":
    main()
