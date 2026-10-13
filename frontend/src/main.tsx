import React from "react";
import ReactDOM from "react-dom/client";
import { lazy, Suspense } from "react";
import App from "./app/App";
import "./styles.css";

const ActiveApp = import.meta.env.VITE_RAG_IMPLEMENTATION === "weknora_python"
  ? lazy(() => import("./weknora_port/WeKnoraPythonApp").then(module => ({ default: module.WeKnoraPythonApp })))
  : App;

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <Suspense fallback={<div className="app-shell" aria-busy="true">正在加载工作台…</div>}>
      <ActiveApp />
    </Suspense>
  </React.StrictMode>,
);
