import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { ReadingPage } from "./ReadingPage";
import "./reading.css";

createRoot(document.getElementById("root")!).render(<StrictMode><ReadingPage /></StrictMode>);
