import React from "react";
import ReactDOM from "react-dom/client";
import { QueryClientProvider } from "@tanstack/react-query";
import App from "./App";
import { queryClient } from "./lib/queryClient";
import "./styles.css";

if (import.meta.env.DEV && typeof window !== "undefined") {
  const { protocol, hostname, pathname, search, hash } = window.location;
  if (hostname !== "localhost" && hostname !== "127.0.0.1") {
    const target = `${protocol}//localhost:5173${pathname}${search}${hash}`;
    window.location.replace(target);
  }
}

ReactDOM.createRoot(document.getElementById("root")).render(
  <React.StrictMode>
    <QueryClientProvider client={queryClient}>
      <App />
    </QueryClientProvider>
  </React.StrictMode>
);
