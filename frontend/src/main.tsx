import React from "react";
import ReactDOM from "react-dom/client";
import { WeKnoraPythonApp } from "./weknora_port/WeKnoraPythonApp";
import "./styles.css";

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <WeKnoraPythonApp />
  </React.StrictMode>,
);
