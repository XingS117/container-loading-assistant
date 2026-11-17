import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import App from "./App";
import { captureUtmParams, initAnalytics } from "./lib/analytics";
import "./styles.css";

initAnalytics();
captureUtmParams();

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
