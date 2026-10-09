import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { VocabularyPage } from "./VocabularyPage";
import "./vocabulary.css";

createRoot(document.getElementById("root")!).render(<StrictMode><VocabularyPage /></StrictMode>);
