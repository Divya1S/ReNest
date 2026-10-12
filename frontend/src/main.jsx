import "./sentry"; // must be first: initialises Sentry before any other module runs
import React from "react";
import ReactDOM from "react-dom/client";

import App from "./App";
import { registerServiceWorker } from "./lib/registerServiceWorker";
import "./index.css";

ReactDOM.createRoot(document.getElementById("root")).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);

registerServiceWorker({ isProduction: import.meta.env.PROD });
