import React from "react";
import ReactDOM from "react-dom/client";
import AdminRoot from "./AdminRoot";
import "./design/baseline.css";
import "./design/tokens.css";
import "./design/controls.css";
import "./design/layout.css";
import "./design/collections.css";
import "./design/dashboard.css";
import "./design/workspaces.css";
import "./design/workflow.css";
import "./design/surfaces.css";

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <AdminRoot />
  </React.StrictMode>,
);
