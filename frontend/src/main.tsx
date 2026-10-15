import React from "react";
import ReactDOM from "react-dom/client";
import { lazy, Suspense } from "react";
import "./styles.css";

const implementation = import.meta.env.VITE_RAG_IMPLEMENTATION ?? "weknora_python";
if (!["weknora_python", "legacy"].includes(implementation)) throw new Error("UNKNOWN_RAG_IMPLEMENTATION");
const ActiveApp = implementation === "legacy"
  ? lazy(() => import("./app/App"))
  : lazy(() => import("./weknora_port/WeKnoraPythonApp").then(module => ({ default: module.WeKnoraPythonApp })));

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <Suspense fallback={<div className="app-shell" aria-busy="true">正在加载工作台…</div>}>
      <ActiveApp />
    </Suspense>
  </React.StrictMode>,
);
