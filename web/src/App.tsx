import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import {
  GlobalWorkerOptions,
  Util,
  getDocument,
  type PDFDocumentProxy,
  type PDFPageProxy,
} from "pdfjs-dist";
import workerUrl from "pdfjs-dist/build/pdf.worker.min.mjs?url";
import {
  ApiError,
  createSession,
  getContext,
  listPapers,
  paperFileUrl,
  saveState,
  uploadPaper,
  type Paper,
  type Quad,
  type SelectionState,
  type Annotation,
  type UserAnnotationStyle,
  type UserAnnotationMarkType,
  type StickyNote,
  listAnnotations,
  createUserAnnotation,
  createVocabularyFromSelection,
  updateUserAnnotation,
  deleteUserAnnotation,
  revokeAssistantRemember,
  listStickyNotes,
  createStickyNote,
  updateStickyNote,
  deleteStickyNote,
  listSummaryNotes,
  createSummaryNote,
  updateSummaryNote,
  deleteSummaryNote,
  type SummaryNote,
} from "./api";
import { openReadingTask as openReadingTaskApi, type ReadingTaskDetail } from "./readingApi";
import { ReadingTaskPanel } from "./ReadingTaskPanel";

GlobalWorkerOptions.workerSrc = workerUrl;

const MAX_PAGE_TEXT = 4_000;
const ACTIVE_PAPER = "caeliae.read.activePaperId";
const ACTIVE_SESSION = "caeliae.read.activeSessionId";
const LAST_USER_ANNOTATION_STYLE = "caeliae.read.lastUserAnnotationStyle";
const DESKTOP_SIDEBAR_WIDTH = 290;
const DESKTOP_MIN_READER_WIDTH = 680;
const DESKTOP_LAYOUT_WIDTH = DESKTOP_SIDEBAR_WIDTH + DESKTOP_MIN_READER_WIDTH;
const DESKTOP_RESTORE_MARGIN = 64;
const STICKY_PLACEMENT_MESSAGE = "点击页面空白处放置便签 · Esc 取消";
const SUMMARY_PLACEMENT_MESSAGE = "点击页面左侧空白处添加大意 · Esc 取消";

type SelectionDraft = Omit<SelectionState, "revision">;
type StickyNoteDraft = { x: number; y: number; text: string; style_key: UserAnnotationStyle; id?: string };
type SummaryNoteDraft = { normalized_y: number; text: string; id?: string };
type StickyDragState = {
  pointerId: number;
  noteId: string;
  startX: number;
  startY: number;
  originalX: number;
  originalY: number;
  currentX: number;
  currentY: number;
  text: string;
  style_key: UserAnnotationStyle;
  moved: boolean;
};
type OverlayKind = "composer" | "annotation" | "sticky" | "summary";
type OverlaySize = { width: number; height: number };
type OverlaySizes = Partial<Record<OverlayKind, OverlaySize>>;
type AnnotationIndexFilter = "all" | "underline" | "highlight" | "sticky" | "summary" | "note";
type SyncState = "idle" | "saving" | "saved" | "error";

const OVERLAY_SIZES_KEY = "caeliae.read.overlaySizes";
const OVERLAY_MIN_SIZES: Record<OverlayKind, OverlaySize> = {
  sticky: { width: 260, height: 220 },
  summary: { width: 280, height: 220 },
  composer: { width: 300, height: 260 },
  annotation: { width: 320, height: 280 },
};
const PAPERS_PANEL_KEY = "caeliae.read.papersPanelOpen";
const ANNOTATIONS_PANEL_KEY = "caeliae.read.annotationsPanelOpen";
const READING_TASK_ID = new URLSearchParams(window.location.search).get("task_id");
const READING_MODE = new URLSearchParams(window.location.search).get("mode") === "reading" && Boolean(READING_TASK_ID);

function normalizeText(value: string): string {
  return value.replace(/\s+/g, " ").trim();
}

function bounded(value: number): number {
  return Math.max(0, Math.min(1, value));
}

function readOverlaySizes(): OverlaySizes {
  try {
    const parsed = JSON.parse(localStorage.getItem(OVERLAY_SIZES_KEY) ?? "null");
    if (!parsed || typeof parsed !== "object") return {};
    return parsed as OverlaySizes;
  } catch {
    return {};
  }
}

function readPanelOpen(key: string, fallback: boolean): boolean {
  try {
    const value = localStorage.getItem(key);
    return value === null ? fallback : value === "true";
  } catch {
    return fallback;
  }
}

type PixelRect = { left: number; top: number; right: number; bottom: number; width: number; height: number };

function mergeHighlightQuads(quads: Quad[]): Quad[] {
  const lines: Array<{ items: Quad[]; top: number; bottom: number; center: number }> = [];
  const ordered = [...quads].sort((a, b) => a.y - b.y || a.x - b.x);
  for (const quad of ordered) {
    const bottom = quad.y + quad.height;
    const center = quad.y + quad.height / 2;
    const line = lines.find((candidate) => {
      const overlap = Math.min(candidate.bottom, bottom) - Math.max(candidate.top, quad.y);
      const overlapRatio = overlap / Math.min(candidate.bottom - candidate.top, quad.height);
      return overlapRatio >= 0.5 && Math.abs(candidate.center - center) <= Math.max(candidate.bottom - candidate.top, quad.height) * 0.4;
    });
    if (line) {
      line.items.push(quad);
      line.top = Math.min(line.top, quad.y);
      line.bottom = Math.max(line.bottom, bottom);
      line.center = (line.top + line.bottom) / 2;
    } else {
      lines.push({ items: [quad], top: quad.y, bottom, center });
    }
  }

  return lines.flatMap((line) => {
    const items = [...line.items].sort((a, b) => a.x - b.x);
    const merged: Quad[] = [];
    for (const item of items) {
      const previous = merged[merged.length - 1];
      const gap = previous ? item.x - (previous.x + previous.width) : Infinity;
      const allowedGap = Math.min(0.018, Math.max(0.006, Math.max(previous?.height ?? item.height, item.height) * 2.5));
      if (previous && gap <= allowedGap) {
        const right = Math.max(previous.x + previous.width, item.x + item.width);
        const top = Math.min(previous.y, item.y);
        const bottom = Math.max(previous.y + previous.height, item.y + item.height);
        previous.x = Math.min(previous.x, item.x);
        previous.y = top;
        previous.width = right - previous.x;
        previous.height = bottom - top;
      } else {
        merged.push({ ...item });
      }
    }
    return merged;
  });
}

function eventTargetElement(target: EventTarget | null): Element | null {
  if (target instanceof Element) return target;
  if (target instanceof Text) return target.parentElement;
  return null;
}

function textLayerSpanFromTarget(target: EventTarget | null): HTMLSpanElement | null {
  const element = eventTargetElement(target);
  const span = element?.closest(".text-layer span");
  return span instanceof HTMLSpanElement ? span : null;
}

function isTextLayerContentTarget(target: EventTarget | null): boolean {
  const span = textLayerSpanFromTarget(target);
  return Boolean(span && span.textContent);
}

function normalizedStickyPoint(pageRect: DOMRect, clientX: number, clientY: number): { x: number; y: number } | null {
  if (pageRect.width <= 0 || pageRect.height <= 0) return null;
  return {
    x: bounded((clientX - pageRect.left) / pageRect.width),
    y: bounded((clientY - pageRect.top) / pageRect.height),
  };
}

function normalizedPageY(pageRect: DOMRect, clientY: number): number | null {
  if (pageRect.height <= 0) return null;
  return bounded((clientY - pageRect.top) / pageRect.height);
}

function boundaryOffsetInSpan(container: Node, offset: number, span: HTMLSpanElement, start: boolean): number {
  const textLength = span.textContent?.length ?? 0;
  if (container === span) return start ? 0 : textLength;
  if (container instanceof Text && span.contains(container)) return Math.max(0, Math.min(textLength, offset));
  return start ? 0 : textLength;
}

function intersectsSpan(selectionRange: Range, span: HTMLSpanElement): boolean {
  return selectionRange.intersectsNode(span);
}

function selectionVisualRects(selectionRange: Range, layer: HTMLElement): PixelRect[] {
  const rects: PixelRect[] = [];
  for (const span of Array.from(layer.querySelectorAll<HTMLSpanElement>("span"))) {
    const value = span.textContent ?? "";
    if (!value || !intersectsSpan(selectionRange, span)) continue;
    const start = boundaryOffsetInSpan(selectionRange.startContainer, selectionRange.startOffset, span, true);
    const end = boundaryOffsetInSpan(selectionRange.endContainer, selectionRange.endOffset, span, false);
    let from = Math.max(0, Math.min(value.length, start));
    let to = Math.max(from, Math.min(value.length, end));
    while (from < to && /\s/.test(value[from] ?? "")) from += 1;
    while (to > from && /\s/.test(value[to - 1] ?? "")) to -= 1;
    if (to <= from || !span.firstChild) continue;
    const visualRange = document.createRange();
    visualRange.setStart(span.firstChild, from);
    visualRange.setEnd(span.firstChild, to);
    for (const rect of Array.from(visualRange.getClientRects())) {
      if (rect.width > 0.5 && rect.height > 0.5) {
        rects.push({ left: rect.left, top: rect.top, right: rect.right, bottom: rect.bottom, width: rect.width, height: rect.height });
      }
    }
  }
  return rects;
}

function mergeVisualSelectionRects(rects: PixelRect[]): PixelRect[] {
  const lines: PixelRect[][] = [];
  for (const rect of rects.sort((a, b) => a.top - b.top || a.left - b.left)) {
    const center = rect.top + rect.height / 2;
    const line = lines.find((candidate) => {
      const top = Math.max(candidate[0].top, rect.top);
      const bottom = Math.min(candidate[0].bottom, rect.bottom);
      const overlap = Math.max(0, bottom - top);
      const candidateCenter = (candidate[0].top + candidate[0].bottom) / 2;
      const minHeight = Math.min(candidate[0].height, rect.height);
      // A visual line must share substantial vertical coverage and a similar
      // baseline. Center proximity alone can incorrectly join a heading to
      // the first body line below it.
      return overlap >= minHeight * 0.45 && Math.abs(candidateCenter - center) <= minHeight * 0.3;
    });
    if (line) line.push(rect); else lines.push([rect]);
  }
  const merged: PixelRect[] = [];
  for (const line of lines) {
    line.sort((a, b) => a.left - b.left);
    let current: PixelRect | null = null;
    for (const rect of line) {
      if (!current) { current = { ...rect }; continue; }
      const gap = rect.left - current.right;
      const adjacentLimit = Math.max(1.5, Math.min(8, Math.max(current.height, rect.height) * 0.35));
      if (gap <= adjacentLimit) {
        current.right = Math.max(current.right, rect.right);
        current.bottom = Math.max(current.bottom, rect.bottom);
        current.top = Math.min(current.top, rect.top);
        current.width = current.right - current.left;
        current.height = current.bottom - current.top;
      } else {
        merged.push(current);
        current = { ...rect };
      }
    }
    if (current) merged.push(current);
  }
  return merged;
}

function buildNormalizedSelectionQuads(selectionRange: Range, layer: HTMLElement): Quad[] {
  const layerRect = layer.getBoundingClientRect();
  if (layerRect.width <= 0 || layerRect.height <= 0) return [];
  return mergeVisualSelectionRects(selectionVisualRects(selectionRange, layer))
    .slice(0, 128)
    .map((rect) => ({
      x: bounded((rect.left - layerRect.left) / layerRect.width),
      y: bounded((rect.top - layerRect.top) / layerRect.height),
      width: bounded(rect.width / layerRect.width),
      height: bounded(rect.height / layerRect.height),
    }))
    .filter((quad) => quad.width > 0 && quad.height > 0);
}

function isWordCharacter(value: string | undefined): boolean {
  return Boolean(value && /[\p{L}\p{N}]/u.test(value));
}

function isClosingPunctuation(value: string | undefined): boolean {
  return Boolean(value && /^[,.;:!?%\)\]\}\u2019\u201d]$/.test(value));
}

function selectionTextFromTextLayer(selectionRange: Range, layer: HTMLElement): string {
  const pieces: Array<{ text: string; rect: DOMRect; hasEol: boolean }> = [];
  for (const span of Array.from(layer.querySelectorAll<HTMLSpanElement>("span"))) {
    const value = span.textContent ?? "";
    if (!value || !selectionRange.intersectsNode(span) || !span.firstChild) continue;
    const from = boundaryOffsetInSpan(selectionRange.startContainer, selectionRange.startOffset, span, true);
    const to = boundaryOffsetInSpan(selectionRange.endContainer, selectionRange.endOffset, span, false);
    if (to <= from) continue;
    pieces.push({ text: value.slice(from, to), rect: span.getBoundingClientRect(), hasEol: span.dataset.hasEol === "true" });
  }
  if (!pieces.length) return "";
  let result = pieces[0].text;
  for (let index = 1; index < pieces.length; index += 1) {
    const previous = pieces[index - 1];
    const current = pieces[index];
    const previousChar = previous.text.at(-1);
    const currentChar = current.text.at(0);
    const sameLine = Math.abs((previous.rect.top + previous.rect.height / 2) - (current.rect.top + current.rect.height / 2)) <= Math.max(previous.rect.height, current.rect.height) * 0.45;
    const gap = current.rect.left - previous.rect.right;
    const normalWordGap = sameLine && gap > Math.max(1.5, Math.min(12, Math.max(previous.rect.height, current.rect.height) * 0.08));
    const hasBoundaryWhitespace = /\s$/.test(previous.text) || /^\s/.test(current.text);
    const wordBoundary = isWordCharacter(previousChar) && isWordCharacter(currentChar);
    const preserveHyphen = previousChar === "-" || currentChar === "-";
    const separator = !hasBoundaryWhitespace && !preserveHyphen && !isClosingPunctuation(currentChar) && (previous.hasEol || (normalWordGap && wordBoundary)) ? " " : "";
    result += separator + current.text;
  }
  return normalizeText(result);
}

function compareSelectionPoints(aNode: Node, aOffset: number, bNode: Node, bOffset: number): number {
  try {
    const a = document.createRange();
    a.setStart(aNode, aOffset);
    a.collapse(true);
    const b = document.createRange();
    b.setStart(bNode, bOffset);
    b.collapse(true);
    return a.compareBoundaryPoints(Range.START_TO_START, b);
  } catch {
    return 0;
  }
}

function sameStrictVisualLine(first: DOMRect, second: DOMRect): boolean {
  const overlap = Math.max(0, Math.min(first.bottom, second.bottom) - Math.max(first.top, second.top));
  const minimumHeight = Math.min(first.height, second.height);
  const centerDelta = Math.abs((first.top + first.bottom) / 2 - (second.top + second.bottom) / 2);
  return minimumHeight > 0 && overlap >= minimumHeight * 0.45 && centerDelta <= minimumHeight * 0.3;
}

function correctSelectionEndpointAtPointer(
  event: Pick<PointerEvent, "clientX" | "clientY">,
  layer: HTMLElement,
): boolean {
  const selection = document.getSelection();
  if (!selection || selection.isCollapsed || !selection.rangeCount) return false;
  const anchorNode = selection.anchorNode;
  const focusNode = selection.focusNode;
  if (!anchorNode || !(focusNode instanceof Text) || !layer.contains(anchorNode) || !layer.contains(focusNode)) return false;

  const focusSpan = focusNode.parentElement?.closest("span");
  if (!focusSpan || !layer.contains(focusSpan)) return false;
  const focusRect = focusSpan.getBoundingClientRect();
  const lineTolerance = Math.max(2, Math.min(8, focusRect.height * 0.5));
  const sameVisualLine = event.clientY >= focusRect.top - lineTolerance
    && event.clientY <= focusRect.bottom + lineTolerance;
  if (!sameVisualLine) return false;

  const direction = compareSelectionPoints(
    anchorNode,
    selection.anchorOffset,
    focusNode,
    selection.focusOffset,
  );
  const forward = direction < 0;
  const backward = direction > 0;
  const textLength = focusNode.data.length;
  let correctedOffset: number | null = null;

  if (forward && event.clientX > focusRect.right && selection.focusOffset < textLength) {
    correctedOffset = textLength;
  } else if (backward && event.clientX < focusRect.left && selection.focusOffset > 0) {
    correctedOffset = 0;
  }

  let correctedNode: Text = focusNode;
  if (correctedOffset === null) {
    let caretNode: Node | null = null;
    let caretOffset = 0;
    const position = document.caretPositionFromPoint?.(event.clientX, event.clientY);
    if (position) {
      caretNode = position.offsetNode;
      caretOffset = position.offset;
    } else {
      const range = document.caretRangeFromPoint?.(event.clientX, event.clientY);
      if (range) {
        caretNode = range.startContainer;
        caretOffset = range.startOffset;
      }
    }
    if (!(caretNode instanceof Text) || !layer.contains(caretNode)) return false;
    const caretSpan = caretNode.parentElement?.closest("span");
    if (!caretSpan || caretSpan === focusSpan || !layer.contains(caretSpan)) return false;
    const caretRect = caretSpan.getBoundingClientRect();
    const caretSameLine = sameStrictVisualLine(focusRect, caretRect);
    if (!caretSameLine) return false;
    const adjacentForward = forward && event.clientX >= focusRect.right && caretRect.left >= focusRect.right - 2;
    const adjacentBackward = backward && event.clientX <= focusRect.left && caretRect.right <= focusRect.left + 2;
    if (!adjacentForward && !adjacentBackward) return false;
    correctedNode = caretNode;
    correctedOffset = Math.max(0, Math.min(caretOffset, caretNode.data.length));
  }

  const targetSpan = correctedNode.parentElement?.closest("span");
  if (correctedOffset !== null && targetSpan && layer.contains(targetSpan)) {
    const targetRect = targetSpan.getBoundingClientRect();
    const targetSameLine = sameStrictVisualLine(focusRect, targetRect);
    if (targetSameLine) {
      const length = correctedNode.data.length;
      if (forward && event.clientX >= targetRect.right) {
        correctedOffset = length;
      } else if (backward && event.clientX <= targetRect.left) {
        correctedOffset = 0;
      } else {
        const characterRects: Array<{ index: number; left: number; right: number }> = [];
        for (let index = 0; index < length; index += 1) {
          const characterRange = document.createRange();
          characterRange.setStart(correctedNode, index);
          characterRange.setEnd(correctedNode, index + 1);
          const rect = characterRange.getBoundingClientRect();
          if (rect.width > 0.1 && rect.height > 0.1) characterRects.push({ index, left: rect.left, right: rect.right });
        }
        if (characterRects.length) {
          if (forward) {
            correctedOffset = 0;
            for (const character of characterRects) {
              if (event.clientX >= (character.left + character.right) / 2) correctedOffset = character.index + 1;
              else break;
            }
          } else if (backward) {
            correctedOffset = length;
            for (const character of characterRects) {
              if (event.clientX <= (character.left + character.right) / 2) {
                correctedOffset = character.index;
                break;
              }
            }
          }
        }
      }
    }
  }

  if (correctedOffset === null || (correctedNode === focusNode && correctedOffset === selection.focusOffset)) return false;
  try {
    selection.setBaseAndExtent(
      anchorNode,
      selection.anchorOffset,
      correctedNode,
      correctedOffset,
    );
    return true;
  } catch {
    return false;
  }
}

export function App() {
  const [papers, setPapers] = useState<Paper[]>([]);
  const [paper, setPaper] = useState<Paper | null>(null);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [readingTask, setReadingTask] = useState<ReadingTaskDetail | null>(null);
  const [readingPanelOpen, setReadingPanelOpen] = useState(READING_MODE);
  const [readingPanelKind, setReadingPanelKind] = useState<"questions" | "annotations">("questions");
  const [documentProxy, setDocumentProxy] = useState<PDFDocumentProxy | null>(null);
  const [pageNumber, setPageNumber] = useState(1);
  const [pageCount, setPageCount] = useState(0);
  const [viewerWidth, setViewerWidth] = useState(0);
  const [viewerHeight, setViewerHeight] = useState(0);
  const [syncState, setSyncState] = useState<SyncState>("idle");
  const [message, setMessage] = useState("选择一篇 PDF 开始共读");
  const [uploading, setUploading] = useState(false);
  const [pdfZoom, setPdfZoom] = useState<number | null>(null);
  const [compact, setCompact] = useState(false);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [papersPanelOpen, setPapersPanelOpen] = useState(() => readPanelOpen(PAPERS_PANEL_KEY, true));
  const [annotationsPanelOpen, setAnnotationsPanelOpen] = useState(() => readPanelOpen(ANNOTATIONS_PANEL_KEY, false));
  const [annotationIndexFilter, setAnnotationIndexFilter] = useState<AnnotationIndexFilter>("all");
  const [annotationIndexFocus, setAnnotationIndexFocus] = useState<string | null>(null);
  const [toolbarSlim, setToolbarSlim] = useState(false);
  const [headerCompact, setHeaderCompact] = useState(false);
  const [renderReadyPage, setRenderReadyPage] = useState<number | null>(null);
  const workspaceRef = useRef<HTMLElement>(null);
  const readerRef = useRef<HTMLElement>(null);
  const headerRef = useRef<HTMLElement>(null);
  const [annotations, setAnnotations] = useState<Annotation[]>([]);
  const [activeAnnotationId, setActiveAnnotationId] = useState<string | null>(null);
  const [annotationCardOpen, setAnnotationCardOpen] = useState(false);
  const [annotationEditNote, setAnnotationEditNote] = useState("");
  const [annotationExcerptExpanded, setAnnotationExcerptExpanded] = useState(false);
  const [isSelectingText, setIsSelectingText] = useState(false);
  const [selectionPreviewQuads, setSelectionPreviewQuads] = useState<Quad[]>([]);
  const [selectionComposerDraft, setSelectionComposerDraft] = useState<SelectionDraft | null>(null);
  const [selectionComposerNote, setSelectionComposerNote] = useState("");
  const [selectionVocabularyKey, setSelectionVocabularyKey] = useState("");
  const [vocabularySaving, setVocabularySaving] = useState(false);
  const [selectionComposerStyle, setSelectionComposerStyle] = useState<UserAnnotationStyle>(() => {
    try {
      const stored = localStorage.getItem(LAST_USER_ANNOTATION_STYLE);
      return stored === "secondary" || stored === "tertiary" ? stored : "primary";
    } catch { return "primary"; }
  });
  const [selectionComposerMarkType, setSelectionComposerMarkType] = useState<UserAnnotationMarkType>("underline");
  const [stickyNotes, setStickyNotes] = useState<StickyNote[]>([]);
  const [stickyPlacementMode, setStickyPlacementMode] = useState(false);
  const [stickyComposerDraft, setStickyComposerDraft] = useState<StickyNoteDraft | null>(null);
  const [stickyComposerText, setStickyComposerText] = useState("");
  const [stickyComposerStyle, setStickyComposerStyle] = useState<UserAnnotationStyle>(selectionComposerStyle);
  const [draggingStickyId, setDraggingStickyId] = useState<string | null>(null);
  const [summaryNotes, setSummaryNotes] = useState<SummaryNote[]>([]);
  const [summaryPlacementMode, setSummaryPlacementMode] = useState(false);
  const [summaryComposerDraft, setSummaryComposerDraft] = useState<SummaryNoteDraft | null>(null);
  const [summaryComposerText, setSummaryComposerText] = useState("");
  const [isGrabScrolling, setIsGrabScrolling] = useState(false);
  const [cardPosition, setCardPosition] = useState<{ left: number; top: number; docked: boolean }>({ left: 12, top: 12, docked: false });
  const [cardLayoutVersion, setCardLayoutVersion] = useState(0);
  const [overlayPosition, setOverlayPosition] = useState<{ left: number; top: number } | null>(null);
  const [overlayDragging, setOverlayDragging] = useState<OverlayKind | null>(null);
  const [overlayResizing, setOverlayResizing] = useState<OverlayKind | null>(null);
  const [overlaySizes, setOverlaySizes] = useState<OverlaySizes>(readOverlaySizes);
  const markRefs = useRef(new Map<string, HTMLButtonElement>());
  const cardRef = useRef<HTMLElement>(null);
  const selectionComposerRef = useRef<HTMLElement>(null);
  const stickyComposerRef = useRef<HTMLElement>(null);
  const summaryComposerRef = useRef<HTMLElement>(null);
  const overlayDragRef = useRef<{ kind: OverlayKind; pointerId: number; startX: number; startY: number; left: number; top: number } | null>(null);
  const overlayResizeRef = useRef<{ kind: OverlayKind; pointerId: number; startX: number; startY: number; width: number; height: number } | null>(null);
  const annotationNoteEditorRef = useRef<HTMLTextAreaElement>(null);
  const stickyNoteEditorRef = useRef<HTMLTextAreaElement>(null);
  const summaryNoteEditorRef = useRef<HTMLTextAreaElement>(null);
  const pendingAnnotationFocusRef = useRef<string | null>(null);
  const pendingSummaryFocusRef = useRef<string | null>(null);
  const summaryMarkerRefs = useRef(new Map<string, HTMLButtonElement>());

  const canvasRef = useRef<HTMLCanvasElement>(null);
  const textLayerRef = useRef<HTMLDivElement>(null);
  const pageShellRef = useRef<HTMLDivElement>(null);
  const toolbarRef = useRef<HTMLElement>(null);
  const pdfPageRef = useRef<HTMLDivElement>(null);
  const viewportOriginalRef = useRef({ width: 1, height: 1, rotation: 0 as 0 | 90 | 180 | 270 });
  const pageTextRef = useRef("");
  const revisionRef = useRef(0);
  const saveTimerRef = useRef<number | null>(null);
  const writeChainRef = useRef<Promise<void>>(Promise.resolve());
  const drawerOpenRef = useRef(false);
  const papersPanelOpenRef = useRef(papersPanelOpen);
  const annotationsPanelOpenRef = useRef(annotationsPanelOpen);
  const annotationCardOpenRef = useRef(false);
  const selectingTextRef = useRef(false);
  const selectionPointerDownRef = useRef(false);
  const toolbarOperatingRef = useRef(false);
  const headerOperatingRef = useRef(false);
  const selectionReleaseTimerRef = useRef<number | null>(null);
  const selectionPreviewFrameRef = useRef<number | null>(null);
  const selectionPromptOpenRef = useRef(false);
  const selectionComposerOpenRef = useRef(false);
  const toolbarReleaseTimerRef = useRef<number | null>(null);
  const headerReleaseTimerRef = useRef<number | null>(null);
  const toolbarRevealTimerRef = useRef<number | null>(null);
  const toolbarManualOpenRef = useRef(false);
  const grabRef = useRef<{
    pointerId: number;
    startX: number;
    startY: number;
    startLeft: number;
    startTop: number;
    moved: boolean;
    forcePan: boolean;
  } | null>(null);
  const suppressStageClickRef = useRef(false);
  const spaceHeldRef = useRef(false);
  const stickyPointerRef = useRef<{ pointerId: number } | null>(null);
  const summaryPointerRef = useRef<{ pointerId: number } | null>(null);
  const stickyDragRef = useRef<StickyDragState | null>(null);
  const stickyClickSuppressedRef = useRef<string | null>(null);

  const clearSelectionPreview = useCallback(() => {
    if (selectionPreviewFrameRef.current !== null) {
      window.cancelAnimationFrame(selectionPreviewFrameRef.current);
      selectionPreviewFrameRef.current = null;
    }
    setSelectionPreviewQuads([]);
  }, []);

  const overlayNode = useCallback((kind: OverlayKind) => kind === "composer" ? selectionComposerRef.current : kind === "annotation" ? cardRef.current : kind === "sticky" ? stickyComposerRef.current : summaryComposerRef.current, []);
  const persistOverlaySizes = useCallback((next: OverlaySizes) => {
    setOverlaySizes(next);
    try { localStorage.setItem(OVERLAY_SIZES_KEY, JSON.stringify(next)); } catch { /* storage is optional */ }
  }, []);
  const clampOverlaySize = useCallback((kind: OverlayKind, size: OverlaySize): OverlaySize => {
    const reader = readerRef.current;
    if (!reader) return size;
    const bounds = reader.getBoundingClientRect();
    const contentBounds = pageShellRef.current?.getBoundingClientRect();
    const availableWidth = contentBounds?.width ?? bounds.width;
    const minimum = OVERLAY_MIN_SIZES[kind];
    const maxWidth = kind === "annotation"
      ? Math.max(0, Math.min(560, availableWidth * .52))
      : Math.max(0, availableWidth - 32);
    const viewportHeight = typeof window === "undefined" ? bounds.height : window.innerHeight;
    const maxHeight = kind === "annotation"
      ? Math.max(0, Math.min(viewportHeight * .72, bounds.height * .76))
      : Math.max(0, bounds.height * .85);
    const minWidth = Math.min(minimum.width, maxWidth);
    const minHeight = Math.min(minimum.height, maxHeight);
    return {
      width: Math.max(minWidth, Math.min(maxWidth, size.width)),
      height: Math.max(minHeight, Math.min(maxHeight, size.height)),
    };
  }, []);
  const clampOverlayPosition = useCallback((left: number, top: number, node: HTMLElement | null = null) => {
    const reader = readerRef.current;
    if (!reader) return { left, top };
    const bounds = reader.getBoundingClientRect();
    const contentBounds = pageShellRef.current?.getBoundingClientRect();
    const width = node?.offsetWidth ?? 0;
    const height = node?.offsetHeight ?? 0;
    const rightEdge = contentBounds ? contentBounds.right - bounds.left : bounds.width;
    return {
      left: Math.max(8, Math.min(Math.max(8, rightEdge - width - 8), left)),
      top: Math.max(8, Math.min(Math.max(8, bounds.height - height - 8), top)),
    };
  }, []);
  const beginOverlayDrag = useCallback((kind: OverlayKind, event: React.PointerEvent<HTMLElement>) => {
    if (overlayResizeRef.current) return;
    if (event.button !== 0 || event.target instanceof Element && event.target.closest("button, input, textarea, select, a, [contenteditable=true]")) return;
    const node = overlayNode(kind);
    if (!node) return;
    const reader = readerRef.current;
    if (!reader) return;
    const readerRect = reader.getBoundingClientRect();
    const current = overlayPosition ?? (kind === "annotation" ? { left: node.getBoundingClientRect().left - readerRect.left, top: node.getBoundingClientRect().top - readerRect.top } : { left: node.getBoundingClientRect().left - readerRect.left, top: node.getBoundingClientRect().top - readerRect.top });
    if (kind === "annotation") setCardPosition((position) => position.docked ? { ...position, docked: false } : position);
    overlayDragRef.current = { kind, pointerId: event.pointerId, startX: event.clientX, startY: event.clientY, left: current.left, top: current.top };
    setOverlayDragging(kind);
    event.preventDefault();
    event.stopPropagation();
    document.getSelection()?.removeAllRanges();
    const handle = event.currentTarget;
    if (handle instanceof HTMLElement && handle.setPointerCapture) handle.setPointerCapture(event.pointerId);
    else node.setPointerCapture(event.pointerId);
  }, [overlayNode, overlayPosition]);
  const moveOverlayDrag = useCallback((kind: OverlayKind, event: React.PointerEvent<HTMLElement>) => {
    const drag = overlayDragRef.current;
    if (!drag || drag.kind !== kind || drag.pointerId !== event.pointerId) return;
    const next = clampOverlayPosition(drag.left + event.clientX - drag.startX, drag.top + event.clientY - drag.startY, overlayNode(kind));
    setOverlayPosition(next);
    event.preventDefault();
    event.stopPropagation();
  }, [clampOverlayPosition, overlayNode]);
  const endOverlayDrag = useCallback((kind: OverlayKind, event: React.PointerEvent<HTMLElement>) => {
    const drag = overlayDragRef.current;
    if (!drag || drag.kind !== kind || drag.pointerId !== event.pointerId) return;
    overlayDragRef.current = null;
    setOverlayDragging(null);
    const node = overlayNode(kind);
    const handle = event.currentTarget;
    if (handle instanceof HTMLElement && handle.hasPointerCapture(event.pointerId)) handle.releasePointerCapture(event.pointerId);
    else if (node?.hasPointerCapture(event.pointerId)) node.releasePointerCapture(event.pointerId);
    event.stopPropagation();
  }, [overlayNode]);

  const beginOverlayResize = useCallback((kind: OverlayKind, event: React.PointerEvent<HTMLElement>) => {
    if (event.button !== 0 || overlayDragRef.current) return;
    const node = overlayNode(kind);
    if (!node) return;
    const current = { width: node.offsetWidth, height: node.offsetHeight };
    overlayResizeRef.current = { kind, pointerId: event.pointerId, startX: event.clientX, startY: event.clientY, width: current.width, height: current.height };
    setOverlayResizing(kind);
    event.preventDefault();
    event.stopPropagation();
    document.getSelection()?.removeAllRanges();
    event.currentTarget.setPointerCapture(event.pointerId);
  }, [overlayNode]);
  const moveOverlayResize = useCallback((kind: OverlayKind, event: React.PointerEvent<HTMLElement>) => {
    const resize = overlayResizeRef.current;
    if (!resize || resize.kind !== kind || resize.pointerId !== event.pointerId) return;
    const next = clampOverlaySize(kind, { width: resize.width + event.clientX - resize.startX, height: resize.height + event.clientY - resize.startY });
    setOverlaySizes((current) => {
      const updated = { ...current, [kind]: next };
      try { localStorage.setItem(OVERLAY_SIZES_KEY, JSON.stringify(updated)); } catch { /* storage is optional */ }
      return updated;
    });
    event.preventDefault();
    event.stopPropagation();
  }, [clampOverlaySize]);
  const endOverlayResize = useCallback((kind: OverlayKind, event: React.PointerEvent<HTMLElement>) => {
    const resize = overlayResizeRef.current;
    if (!resize || resize.kind !== kind || resize.pointerId !== event.pointerId) return;
    overlayResizeRef.current = null;
    setOverlayResizing(null);
    if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId);
    event.preventDefault();
    event.stopPropagation();
  }, []);
  const resetOverlaySize = useCallback((kind: OverlayKind) => {
    const next = { ...overlaySizes };
    delete next[kind];
    persistOverlaySizes(next);
  }, [overlaySizes, persistOverlaySizes]);

  const clearToolbarRevealTimer = useCallback(() => {
    if (toolbarRevealTimerRef.current !== null) {
      window.clearTimeout(toolbarRevealTimerRef.current);
      toolbarRevealTimerRef.current = null;
    }
  }, []);

  const scheduleToolbarCollapse = useCallback(() => {
    clearToolbarRevealTimer();
    if (drawerOpenRef.current || annotationCardOpenRef.current || selectingTextRef.current || selectionComposerOpenRef.current) return;
    const node = pageShellRef.current;
    if (!node || node.scrollTop < 24) return;
    if (toolbarRef.current?.contains(document.activeElement)) return;
    toolbarRevealTimerRef.current = window.setTimeout(() => {
      toolbarRevealTimerRef.current = null;
      toolbarManualOpenRef.current = false;
      if (!drawerOpenRef.current && !annotationCardOpenRef.current && !selectingTextRef.current && !selectionComposerOpenRef.current && !toolbarOperatingRef.current && !toolbarRef.current?.contains(document.activeElement)) {
        setToolbarSlim(true);
      }
    }, 1900);
  }, [clearToolbarRevealTimer]);

  const revealToolbar = useCallback(() => {
    clearToolbarRevealTimer();
    toolbarManualOpenRef.current = true;
    setToolbarSlim(false);
  }, [clearToolbarRevealTimer]);

  const isDragSurfaceTarget = useCallback((target: EventTarget | null) => {
    const element = eventTargetElement(target);
    if (!element) return false;
    if (isTextLayerContentTarget(target)) return false;
    if (element.closest(".annotation-mark, .annotation-card, button, a, input, label, [role=dialog]")) return false;
    return true;
  }, []);

  const isSummaryPlacementTarget = useCallback((target: EventTarget | null, clientX: number) => {
    const page = pdfPageRef.current;
    const element = eventTargetElement(target);
    if (!page || !element || isTextLayerContentTarget(target)) return false;
    if (element.closest(".annotation-mark, .sticky-note-marker, .summary-note-marker, button, a, input, label, textarea, select, [role=dialog], .selection-composer, .annotation-card, .sticky-composer, .summary-note-composer")) return false;
    const pageRect = page.getBoundingClientRect();
    const inBlankLayer = element === page || element === canvasRef.current || element === textLayerRef.current || Boolean(element.closest(".text-layer, .selection-preview-layer, .annotation-layer, .sticky-layer, .summary-note-layer"));
    if (!inBlankLayer) return false;
    const gutterWidth = Math.min(72, Math.max(32, pageRect.width * 0.14));
    return clientX >= pageRect.left && clientX <= pageRect.left + gutterWidth;
  }, []);

  const handleStagePointerDown = useCallback((event: React.PointerEvent<HTMLDivElement>) => {
    const forcePan = spaceHeldRef.current;
    if (event.button !== 0 || drawerOpenRef.current || annotationCardOpenRef.current) return;
    if (event.target instanceof HTMLElement && event.target.closest(".annotation-mark, .annotation-card, button, a, input, label, [role=dialog]")) return;
    if (!forcePan && !isDragSurfaceTarget(event.target)) return;
    const selection = document.getSelection();
    if (!forcePan && selection && !selection.isCollapsed) return;
    const node = pageShellRef.current;
    if (!node) return;
    grabRef.current = {
      pointerId: event.pointerId,
      startX: event.clientX,
      startY: event.clientY,
      startLeft: node.scrollLeft,
      startTop: node.scrollTop,
      moved: false,
      forcePan,
    };
    node.setPointerCapture(event.pointerId);
  }, [isDragSurfaceTarget]);

  const handleStagePointerMove = useCallback((event: React.PointerEvent<HTMLDivElement>) => {
    const drag = grabRef.current;
    const node = pageShellRef.current;
    if (!drag || !node || drag.pointerId !== event.pointerId) return;
    const distance = Math.hypot(event.clientX - drag.startX, event.clientY - drag.startY);
    if (!drag.moved && distance < 5) return;
    drag.moved = true;
    suppressStageClickRef.current = true;
    node.scrollLeft = drag.startLeft - (event.clientX - drag.startX);
    node.scrollTop = drag.startTop - (event.clientY - drag.startY);
    setIsGrabScrolling(true);
    event.preventDefault();
  }, []);

  const handleStagePointerUp = useCallback((event: React.PointerEvent<HTMLDivElement>) => {
    const drag = grabRef.current;
    const node = pageShellRef.current;
    if (!drag || !node || drag.pointerId !== event.pointerId) return;
    grabRef.current = null;
    if (node.hasPointerCapture(event.pointerId)) node.releasePointerCapture(event.pointerId);
    setIsGrabScrolling(false);
    window.setTimeout(() => { suppressStageClickRef.current = false; }, 120);
    return drag.moved || drag.forcePan;
  }, []);

  const handleStagePointerCancel = useCallback((event: React.PointerEvent<HTMLDivElement>) => {
    if (stickyPointerRef.current?.pointerId === event.pointerId) stickyPointerRef.current = null;
    if (summaryPointerRef.current?.pointerId === event.pointerId) summaryPointerRef.current = null;
    const drag = grabRef.current;
    if (!drag || drag.pointerId !== event.pointerId) return;
    grabRef.current = null;
    const node = pageShellRef.current;
    if (node?.hasPointerCapture(event.pointerId)) node.releasePointerCapture(event.pointerId);
    setIsGrabScrolling(false);
    window.setTimeout(() => { suppressStageClickRef.current = false; }, 120);
  }, []);

  const openStickyComposerAt = useCallback((clientX: number, clientY: number) => {
    if (!pdfPageRef.current || !paper) return false;
    const pageRect = pdfPageRef.current.getBoundingClientRect();
    const point = normalizedStickyPoint(pageRect, clientX, clientY);
    if (!point) return false;
    setStickyComposerDraft({ ...point, text: "", style_key: selectionComposerStyle });
    setStickyComposerText("");
    setStickyComposerStyle(selectionComposerStyle);
    setStickyPlacementMode(false);
    setMessage("便签草稿已创建 · 输入内容后保存");
    return true;
  }, [paper, selectionComposerStyle]);

  const openSummaryComposerAt = useCallback((clientX: number, clientY: number) => {
    if (!pdfPageRef.current || !paper) return false;
    const pageRect = pdfPageRef.current.getBoundingClientRect();
    const normalizedY = normalizedPageY(pageRect, clientY);
    if (normalizedY === null) return false;
    setSummaryComposerDraft({ normalized_y: normalizedY, text: "" });
    setSummaryComposerText("");
    setSummaryPlacementMode(false);
    setMessage("大意草稿已创建 · 输入内容后保存");
    return true;
  }, [paper]);

  const beginStickyDrag = useCallback((event: React.PointerEvent<HTMLButtonElement>, note: StickyNote) => {
    event.stopPropagation();
    if (event.button !== 0 || spaceHeldRef.current || !pdfPageRef.current) return;
    const pageRect = pdfPageRef.current.getBoundingClientRect();
    const point = normalizedStickyPoint(pageRect, event.clientX, event.clientY);
    if (!point) return;
    stickyDragRef.current = {
      pointerId: event.pointerId,
      noteId: note.id,
      startX: event.clientX,
      startY: event.clientY,
      originalX: note.x,
      originalY: note.y,
      currentX: note.x,
      currentY: note.y,
      text: note.text,
      style_key: note.style_key,
      moved: false,
    };
    event.currentTarget.setPointerCapture(event.pointerId);
  }, []);

  const moveStickyDrag = useCallback((event: React.PointerEvent<HTMLButtonElement>, noteId: string) => {
    const drag = stickyDragRef.current;
    if (!drag || drag.pointerId !== event.pointerId || drag.noteId !== noteId || !pdfPageRef.current) return;
    event.stopPropagation();
    const distance = Math.hypot(event.clientX - drag.startX, event.clientY - drag.startY);
    if (!drag.moved && distance < 5) return;
    drag.moved = true;
    setDraggingStickyId(noteId);
    const point = normalizedStickyPoint(pdfPageRef.current.getBoundingClientRect(), event.clientX, event.clientY);
    if (!point) return;
    drag.currentX = point.x;
    drag.currentY = point.y;
    setStickyNotes((items) => items.map((item) => item.id === noteId ? { ...item, x: point.x, y: point.y } : item));
    event.preventDefault();
  }, []);

  const finishStickyDrag = useCallback(async (event: React.PointerEvent<HTMLButtonElement>, noteId: string, cancelled: boolean) => {
    const drag = stickyDragRef.current;
    if (!drag || drag.pointerId !== event.pointerId || drag.noteId !== noteId) return;
    event.stopPropagation();
    if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId);
    stickyDragRef.current = null;
    setDraggingStickyId(null);
    if (cancelled || !drag.moved) {
      if (cancelled) setStickyNotes((items) => items.map((item) => item.id === noteId ? { ...item, x: drag.originalX, y: drag.originalY } : item));
      return;
    }
    const point = pdfPageRef.current ? normalizedStickyPoint(pdfPageRef.current.getBoundingClientRect(), event.clientX, event.clientY) : null;
    const x = point?.x ?? drag.currentX;
    const y = point?.y ?? drag.currentY;
    drag.currentX = x;
    drag.currentY = y;
    stickyClickSuppressedRef.current = noteId;
    window.setTimeout(() => {
      if (stickyClickSuppressedRef.current === noteId) stickyClickSuppressedRef.current = null;
    }, 0);
    setStickyNotes((items) => items.map((item) => item.id === noteId ? { ...item, x, y } : item));
    try {
      const result = await updateStickyNote(noteId, { x, y, text: drag.text, style_key: drag.style_key });
      setStickyNotes((items) => items.map((item) => item.id === noteId ? result.sticky_note : item));
      setMessage("便签位置已保存");
    } catch {
      setStickyNotes((items) => items.map((item) => item.id === noteId ? { ...item, x: drag.originalX, y: drag.originalY } : item));
      setMessage("便签位置保存失败");
    }
  }, []);

  const handleStageClick = useCallback((event: React.MouseEvent<HTMLDivElement>) => {
    if (suppressStageClickRef.current || !isDragSurfaceTarget(event.target)) return;
    if (drawerOpenRef.current || annotationCardOpenRef.current) return;
    if (stickyPlacementMode && openStickyComposerAt(event.clientX, event.clientY)) return;
    if (summaryPlacementMode && isSummaryPlacementTarget(event.target, event.clientX) && openSummaryComposerAt(event.clientX, event.clientY)) return;
    if (toolbarSlim) revealToolbar();
    else setToolbarSlim(true);
  }, [isDragSurfaceTarget, isSummaryPlacementTarget, openStickyComposerAt, openSummaryComposerAt, revealToolbar, stickyPlacementMode, summaryPlacementMode, toolbarSlim]);

  const handleReaderPointerMove = useCallback((event: React.PointerEvent<HTMLElement>) => {
    const toolbar = toolbarRef.current;
    if (toolbar && event.clientY - toolbar.getBoundingClientRect().top <= 8) revealToolbar();
  }, [revealToolbar]);

  const handleReaderPointerDown = useCallback((event: React.PointerEvent<HTMLElement>) => {
    const toolbar = toolbarRef.current;
    if (toolbar && event.clientY - toolbar.getBoundingClientRect().top <= 8) revealToolbar();
  }, [revealToolbar]);

  useEffect(() => () => {
    clearToolbarRevealTimer();
  }, [clearToolbarRevealTimer]);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.code !== "Space" || event.repeat) return;
      if (event.target instanceof HTMLElement && event.target.closest("button, a, input, textarea, select, [contenteditable=true]")) return;
      event.preventDefault();
      spaceHeldRef.current = true;
    };
    const onKeyUp = (event: KeyboardEvent) => {
      if (event.code === "Space") spaceHeldRef.current = false;
    };
    const onBlur = () => { spaceHeldRef.current = false; };
    window.addEventListener("keydown", onKeyDown);
    window.addEventListener("keyup", onKeyUp);
    window.addEventListener("blur", onBlur);
    return () => {
      window.removeEventListener("keydown", onKeyDown);
      window.removeEventListener("keyup", onKeyUp);
      window.removeEventListener("blur", onBlur);
    };
  }, []);

  useLayoutEffect(() => {
    const node = workspaceRef.current; if (!node) return;
    const updateLayout = (width: number) => setCompact((current) => current
      ? width < DESKTOP_LAYOUT_WIDTH + DESKTOP_RESTORE_MARGIN
      : width < DESKTOP_LAYOUT_WIDTH);
    updateLayout(node.getBoundingClientRect().width);
    const observer = new ResizeObserver(([entry]) => updateLayout(entry.contentRect.width));
    observer.observe(node); return () => observer.disconnect();
  }, []);

  const updateDrawerOpen = useCallback((open: boolean) => {
    drawerOpenRef.current = open;
    setDrawerOpen(open);
    if (open) setToolbarSlim(false);
  }, []);

  const updatePapersPanelOpen = useCallback((open: boolean) => {
    papersPanelOpenRef.current = open;
    setPapersPanelOpen(open);
    try { localStorage.setItem(PAPERS_PANEL_KEY, String(open)); } catch { /* storage is optional */ }
    if (open && compact) {
      annotationsPanelOpenRef.current = false;
      setAnnotationsPanelOpen(false);
      try { localStorage.setItem(ANNOTATIONS_PANEL_KEY, "false"); } catch { /* storage is optional */ }
      updateDrawerOpen(true);
    } else if (!open) {
      updateDrawerOpen(false);
    }
  }, [compact, updateDrawerOpen]);

  const updateAnnotationsPanelOpen = useCallback((open: boolean) => {
    annotationsPanelOpenRef.current = open;
    setAnnotationsPanelOpen(open);
    try { localStorage.setItem(ANNOTATIONS_PANEL_KEY, String(open)); } catch { /* storage is optional */ }
    if (open && compact) {
      papersPanelOpenRef.current = false;
      setPapersPanelOpen(false);
      try { localStorage.setItem(PAPERS_PANEL_KEY, "false"); } catch { /* storage is optional */ }
      updateDrawerOpen(false);
    }
    if (open) {
      setToolbarSlim(false);
    }
  }, [compact, updateDrawerOpen]);

  const updateAnnotationCardOpen = useCallback((open: boolean) => {
    if (open && selectionComposerOpenRef.current) {
      selectionComposerOpenRef.current = false;
      selectionPromptOpenRef.current = false;
      setSelectionComposerDraft(null);
      setSelectionComposerNote("");
      setSelectionVocabularyKey("");
      clearSelectionPreview();
    }
    annotationCardOpenRef.current = open;
    setAnnotationCardOpen(open);
    if (open) setToolbarSlim(false);
  }, [clearSelectionPreview]);

  useEffect(() => {
    if (!compact) { updateDrawerOpen(false); setHeaderCompact(false); }
    else updateDrawerOpen(false);
  }, [compact, updateDrawerOpen]);
  useEffect(() => {
    const node = pageShellRef.current; if (!node) { setToolbarSlim(false); return; }
    if (!compact) setToolbarSlim(false);
    let last = node.scrollTop; let down = 0; let up = 0; let headerDown = 0; let headerUp = 0;
    const onScroll = () => {
      const delta = node.scrollTop - last;
      last = node.scrollTop;
      if (node.scrollTop < 24) toolbarManualOpenRef.current = false;
      const toolbarHasFocus = Boolean(toolbarRef.current?.contains(document.activeElement)
        && (document.activeElement as HTMLElement | null)?.matches(":focus-visible"));
      const headerHasFocus = Boolean(headerRef.current?.contains(document.activeElement)
        && (document.activeElement as HTMLElement | null)?.matches(":focus-visible"));
      const blockers = [
        drawerOpenRef.current && "drawer",
        annotationCardOpenRef.current && cardRef.current && "annotation-card",
        selectingTextRef.current && "text-selection",
        selectionComposerOpenRef.current && "annotation-composer",
        toolbarHasFocus && "toolbar-focus",
        headerHasFocus && "header-focus",
        headerOperatingRef.current && "header-operation",
      ].filter(Boolean) as string[];
      if (workspaceRef.current) {
        workspaceRef.current.dataset.scrollBlocker = blockers.join(",");
        workspaceRef.current.dataset.scrollDelta = String(delta);
      }
      if (blockers.length > 0) { down = 0; up = 0; headerDown = 0; headerUp = 0; return; }
      if (delta > 0) { down += delta; up = 0; headerDown += delta; headerUp = 0; }
      else if (delta < 0) { up -= delta; down = 0; headerUp -= delta; headerDown = 0; }
      if (workspaceRef.current) {
        workspaceRef.current.dataset.scrollDown = String(down);
        workspaceRef.current.dataset.scrollUp = String(up);
        workspaceRef.current.dataset.headerScrollDown = String(headerDown);
        workspaceRef.current.dataset.headerScrollUp = String(headerUp);
      }
      if ((down > 96 || node.scrollTop > 96) && !toolbarManualOpenRef.current) setToolbarSlim(true);
      else if (up > 56 || node.scrollTop < 24) setToolbarSlim(false);
      if (compact && node.scrollTop > 96 && !drawerOpenRef.current && !annotationCardOpenRef.current && !selectingTextRef.current) {
        window.requestAnimationFrame(() => {
          if (pageShellRef.current === node && node.scrollTop > 96 && !toolbarManualOpenRef.current && !drawerOpenRef.current && !annotationCardOpenRef.current && !selectingTextRef.current) setToolbarSlim(true);
        });
      }
      if (compact && headerDown > 88) setHeaderCompact(true);
      if (!compact || headerUp > 64 || node.scrollTop < 24) setHeaderCompact(false);
    };
    node.addEventListener("scroll", onScroll, { passive: true });
    if (compact && node.scrollTop > 96) setToolbarSlim(true);
    return () => node.removeEventListener("scroll", onScroll);
  }, [compact]);
  useEffect(() => { const close = (e: KeyboardEvent) => { if (e.key === "Escape") { updateDrawerOpen(false); updatePapersPanelOpen(false); updateAnnotationsPanelOpen(false); updateAnnotationCardOpen(false); } }; window.addEventListener("keydown", close); return () => window.removeEventListener("keydown", close); }, [updateAnnotationCardOpen, updateAnnotationsPanelOpen, updateDrawerOpen, updatePapersPanelOpen]);

  useEffect(() => {
    const releaseSelectionGuard = () => {
      if (selectionReleaseTimerRef.current !== null) window.clearTimeout(selectionReleaseTimerRef.current);
      selectionReleaseTimerRef.current = window.setTimeout(() => { selectingTextRef.current = false; setIsSelectingText(false); }, 350);
    };
    const paintSelectionPreview = () => {
      selectionPreviewFrameRef.current = null;
      const selection = document.getSelection();
      const layer = textLayerRef.current;
      if (!selection || selection.isCollapsed || !layer || selection.rangeCount === 0) {
        setSelectionPreviewQuads([]);
        return;
      }
      const range = selection.getRangeAt(0);
      if (!layer.contains(range.commonAncestorContainer)) {
        setSelectionPreviewQuads([]);
        return;
      }
      setSelectionPreviewQuads(buildNormalizedSelectionQuads(range, layer));
    };
    const scheduleSelectionPreview = () => {
      if (selectionPreviewFrameRef.current === null) {
        selectionPreviewFrameRef.current = window.requestAnimationFrame(paintSelectionPreview);
      }
    };
    const onSelectionChange = () => {
      const selection = document.getSelection();
      const layer = textLayerRef.current;
      if (!selection || selection.isCollapsed || !layer || selection.rangeCount === 0 || !layer.contains(selection.getRangeAt(0).commonAncestorContainer)) {
        if (selectionPromptOpenRef.current) return;
        clearSelectionPreview();
        return;
      }
      selectingTextRef.current = true; setIsSelectingText(true);
      scheduleSelectionPreview();
      if (!selectionPointerDownRef.current) releaseSelectionGuard();
    };
    const onPointerEnd = () => { selectionPointerDownRef.current = false; releaseSelectionGuard(); };
    document.addEventListener("selectionchange", onSelectionChange);
    window.addEventListener("pointerup", onPointerEnd);
    window.addEventListener("pointercancel", onPointerEnd);
    return () => {
      document.removeEventListener("selectionchange", onSelectionChange);
      window.removeEventListener("pointerup", onPointerEnd);
      window.removeEventListener("pointercancel", onPointerEnd);
      if (selectionReleaseTimerRef.current !== null) window.clearTimeout(selectionReleaseTimerRef.current);
      clearSelectionPreview();
    };
  }, [clearSelectionPreview]);

  useEffect(() => {
    clearSelectionPreview();
  }, [clearSelectionPreview, pageNumber, paper, pdfZoom, viewerWidth]);

  const beginToolbarOperation = useCallback(() => {
    if (toolbarReleaseTimerRef.current !== null) window.clearTimeout(toolbarReleaseTimerRef.current);
    toolbarOperatingRef.current = true;
    toolbarReleaseTimerRef.current = window.setTimeout(() => { toolbarOperatingRef.current = false; }, 750);
  }, []);
  const endToolbarOperation = useCallback((event: React.PointerEvent<HTMLElement>) => {
    if (event.target instanceof HTMLElement && event.target.closest("button")) (event.target.closest("button") as HTMLElement).blur();
    if (toolbarReleaseTimerRef.current !== null) window.clearTimeout(toolbarReleaseTimerRef.current);
    toolbarReleaseTimerRef.current = null;
    toolbarOperatingRef.current = false;
  }, []);

  const beginHeaderOperation = useCallback(() => {
    if (headerReleaseTimerRef.current !== null) window.clearTimeout(headerReleaseTimerRef.current);
    headerOperatingRef.current = true;
    headerReleaseTimerRef.current = window.setTimeout(() => { headerOperatingRef.current = false; }, 750);
  }, []);
  const endHeaderOperation = useCallback((event: React.PointerEvent<HTMLElement>) => {
    if (event.target instanceof HTMLElement && event.target.closest("button, label")) (event.target.closest("button, label") as HTMLElement).blur();
    if (headerReleaseTimerRef.current !== null) window.clearTimeout(headerReleaseTimerRef.current);
    headerReleaseTimerRef.current = null;
    headerOperatingRef.current = false;
  }, []);

  const refreshPapers = useCallback(async () => {
    const next = await listPapers();
    setPapers(next);
    return next;
  }, []);

  const openReadingSession = useCallback(async () => {
    if (!READING_TASK_ID) return;
    const detail = await openReadingTaskApi(READING_TASK_ID);
    let restoredPage = 1;
    let restoredRevision = 0;
    if (detail.session_id) {
      try {
        const context = await getContext(detail.session_id);
        if (context.paper.paper_id === detail.paper.paper_id) {
          restoredPage = context.checkpoint.page_number;
          restoredRevision = context.checkpoint.revision;
        }
      } catch { /* a new task session has no checkpoint yet */ }
    }
    revisionRef.current = restoredRevision;
    setReadingTask(detail);
    setReadingPanelOpen(true);
    setReadingPanelKind("questions");
    setPageNumber(restoredPage);
    setSessionId(detail.session_id ?? null);
    setPaper(detail.paper);
    setMessage(detail.task.title);
  }, []);

  const scheduleState = useCallback((
    targetPage: number,
    text: string,
    selection: SelectionDraft | null,
  ) => {
    if (!paper || !sessionId) return;
    if (saveTimerRef.current !== null) window.clearTimeout(saveTimerRef.current);
    saveTimerRef.current = window.setTimeout(() => {
      const selectedPaper = paper;
      const selectedSession = sessionId;
      writeChainRef.current = writeChainRef.current.catch(() => undefined).then(async () => {
        const revision = revisionRef.current + 1;
        const eventId = crypto.randomUUID();
        const payload = {
          paper_id: selectedPaper.paper_id,
          session_id: selectedSession,
          client_event_id: eventId,
          page_number: targetPage,
          page_text: text.slice(0, MAX_PAGE_TEXT),
          revision,
          selection: selection ? { ...selection, revision } : null,
        };
        setSyncState("saving");
        try {
          const saved = await saveState(selectedSession, payload);
          revisionRef.current = saved.revision;
          setSyncState("saved");
        } catch (error) {
          if (error instanceof ApiError && error.status === 409) {
            const current = error.problem?.detail?.current_revision;
            if (typeof current === "number") revisionRef.current = current;
          }
          setSyncState("error");
          throw error;
        }
      });
    }, 450);
  }, [paper, sessionId]);

  useEffect(() => {
    const shell = pageShellRef.current;
    if (!shell) return;
    const observer = new ResizeObserver(([entry]) => {
      setViewerWidth(entry.contentRect.width);
      setViewerHeight(entry.contentRect.height);
    });
    observer.observe(shell);
    return () => observer.disconnect();
  }, [paper]);

  useLayoutEffect(() => {
    const shell = pageShellRef.current;
    if (!shell) return;
    const rect = shell.getBoundingClientRect();
    setViewerWidth((current) => Math.abs(current - rect.width) > 0.5 ? rect.width : current);
    setViewerHeight((current) => Math.abs(current - rect.height) > 0.5 ? rect.height : current);
  }, [paper, papersPanelOpen, annotationsPanelOpen, compact]);

  useEffect(() => {
    let active = true;
    if (READING_MODE) {
      void openReadingSession().catch((error) => {
        if (active) setMessage(error instanceof Error ? error.message : "无法打开 Reading 任务");
      });
      return () => { active = false; };
    }
    void refreshPapers().then(async (available) => {
      const paperId = localStorage.getItem(ACTIVE_PAPER) ?? localStorage.getItem("coread.activePaperId");
      const storedSession = localStorage.getItem(ACTIVE_SESSION) ?? localStorage.getItem("coread.activeSessionId");
      const selected = available.find((item) => item.paper_id === paperId);
      if (!active || !selected || !storedSession) return;
      try {
        const context = await getContext(storedSession);
        if (!active) return;
        if (context.paper.paper_id !== selected.paper_id) return;
        localStorage.setItem(ACTIVE_PAPER, selected.paper_id);
        localStorage.setItem(ACTIVE_SESSION, storedSession);
        revisionRef.current = context.checkpoint.revision;
        // Restore identity and checkpoint together, before a page-1 render can save.
        setPaper(selected);
        setSessionId(storedSession);
        setPageNumber(context.checkpoint.page_number);
        setMessage(`已恢复到第 ${context.checkpoint.page_number} 页`);
      } catch {
        revisionRef.current = 0;
      }
    }).catch(() => setMessage("无法连接 Caeliae Read"));
    return () => { active = false; };
  }, [openReadingSession, refreshPapers]);

  useEffect(() => {
    if (!paper) {
      setDocumentProxy(null);
      return;
    }
    let cancelled = false;
    const task = getDocument(paperFileUrl(paper.paper_id));
    void task.promise.then((pdf) => {
      if (cancelled) return;
      setDocumentProxy(pdf);
      setPageCount(pdf.numPages);
      setPageNumber((current) => Math.min(Math.max(current, 1), pdf.numPages));
      setMessage(paper.original_filename);
    }).catch(() => setMessage("PDF 无法打开"));
    return () => {
      cancelled = true;
      void task.destroy();
    };
  }, [paper]);

  useEffect(() => {
    if (!documentProxy || !canvasRef.current || !textLayerRef.current || viewerWidth < 1) return;
    let cancelled = false;
    let activePage: PDFPageProxy | null = null;
    let renderTask: ReturnType<PDFPageProxy["render"]> | null = null;
    void documentProxy.getPage(pageNumber).then(async (page) => {
      if (cancelled) return;
      activePage = page;
      const original = page.getViewport({ scale: 1 });
      const available = Math.max(280, Math.min(viewerWidth - 24, 1100));
      const fitScale = Math.min(2, available / original.width);
      const scale = pdfZoom ?? fitScale;
      const viewport = page.getViewport({ scale });
      viewportOriginalRef.current = {
        width: original.width,
        height: original.height,
        rotation: page.rotate as 0 | 90 | 180 | 270,
      };

      const canvas = canvasRef.current!;
      const outputScale = Math.min(window.devicePixelRatio || 1, 2);
      const pixelWidth = Math.floor(viewport.width * outputScale);
      const pixelHeight = Math.floor(viewport.height * outputScale);
      const staging = document.createElement("canvas");
      staging.width = pixelWidth; staging.height = pixelHeight;
      const context = staging.getContext("2d", { alpha: false });
      if (!context) throw new Error("Canvas 2D is unavailable");
      renderTask = page.render({
        canvas: staging,
        canvasContext: context,
        viewport,
        transform: outputScale === 1 ? undefined : [outputScale, 0, 0, outputScale, 0, 0],
      });
      try { await renderTask.promise; } catch (error) { if (!cancelled) throw error; return; }
      if (cancelled) return;
      canvas.width = pixelWidth; canvas.height = pixelHeight;
      canvas.style.width = `${viewport.width}px`; canvas.style.height = `${viewport.height}px`;
      const visible = canvas.getContext("2d", { alpha: false });
      if (!visible) throw new Error("Canvas 2D is unavailable");
      visible.drawImage(staging, 0, 0);

      const content = await page.getTextContent();
      if (cancelled) return;
      const pageText = normalizeText(content.items.map((item) => ("str" in item ? item.str : "")).join(" "))
        .slice(0, MAX_PAGE_TEXT);
      pageTextRef.current = pageText;

      const layer = textLayerRef.current!;
      layer.replaceChildren();
      layer.style.width = `${viewport.width}px`;
      layer.style.height = `${viewport.height}px`;
      for (const item of content.items) {
        if (!("str" in item) || !item.str) continue;
        const transform = Util.transform(viewport.transform, item.transform);
        const fontHeight = Math.hypot(transform[2], transform[3]);
        const angle = Math.atan2(transform[1], transform[0]);
        const span = document.createElement("span");
        span.textContent = item.str;
        span.dataset.hasEol = "hasEOL" in item && item.hasEOL ? "true" : "false";
        span.style.left = `${transform[4]}px`;
        span.style.top = `${transform[5] - fontHeight}px`;
        span.style.fontSize = `${fontHeight}px`;
        span.style.fontFamily = content.styles[item.fontName]?.fontFamily || "sans-serif";
        layer.appendChild(span);

        const naturalWidth = span.getBoundingClientRect().width;
        const itemBasis = Math.hypot(item.transform[0], item.transform[1]);
        const viewportBasis = Math.hypot(transform[0], transform[1]);
        const viewportScale = itemBasis > 0.0001 ? viewportBasis / itemBasis : 0;
        const targetWidth = typeof item.width === "number" && viewportScale > 0
          ? Math.abs(item.width) * viewportScale
          : 0;
        const scaleX = naturalWidth > 0.0001 && targetWidth > 0
          ? targetWidth / naturalWidth
          : 1;
        span.style.transform = `rotate(${angle}rad) scaleX(${scaleX})`;
      }
      scheduleState(pageNumber, pageText, null);
      setRenderReadyPage(pageNumber);
    }).catch(() => setMessage("页面渲染失败"));
    return () => {
      cancelled = true;
      renderTask?.cancel();
      activePage?.cleanup();
    };
  }, [documentProxy, pageNumber, scheduleState, viewerWidth, pdfZoom]);

  useEffect(() => {
    if (!paper) { setAnnotations([]); return; }
    let active = true;
    const refreshAnnotations = () => void listAnnotations(paper.paper_id).then((items) => { if (active) setAnnotations(items); }).catch(() => { if (active) setAnnotations([]); });
    refreshAnnotations();
    const timer = window.setInterval(refreshAnnotations, 3000);
    return () => { active = false; window.clearInterval(timer); };
  }, [paper]);

  useEffect(() => {
    if (!paper) { setStickyNotes([]); return; }
    let active = true;
    const refreshStickyNotes = () => void listStickyNotes(paper.paper_id).then((items) => { if (active) setStickyNotes(items); }).catch(() => { if (active) setStickyNotes([]); });
    refreshStickyNotes();
    const timer = window.setInterval(refreshStickyNotes, 3000);
    return () => { active = false; window.clearInterval(timer); };
  }, [paper]);

  useEffect(() => {
    if (!paper) { setSummaryNotes([]); return; }
    let active = true;
    const refreshSummaryNotes = () => void listSummaryNotes(paper.paper_id).then((items) => { if (active) setSummaryNotes(items); }).catch(() => { if (active) setSummaryNotes([]); });
    refreshSummaryNotes();
    const timer = window.setInterval(refreshSummaryNotes, 3000);
    return () => { active = false; window.clearInterval(timer); };
  }, [paper]);

  const pageAnnotations = annotations.filter((item) => item.page_number === pageNumber);
  const activeAnnotation = activeAnnotationId ? annotations.find((item) => item.annotation_id === activeAnnotationId) ?? null : null;
  const annotationCardDetached = Boolean(annotationCardOpen && activeAnnotation && activeAnnotation.page_number !== pageNumber);
  const overlayBlankPointerRef = useRef<number | null>(null);
  const overlayIsOpen = Boolean(selectionComposerDraft || annotationCardOpen || stickyComposerDraft || summaryComposerDraft);

  const isPdfBlankTarget = useCallback((target: EventTarget | null) => {
    const element = eventTargetElement(target);
    if (!element) return false;
    if (textLayerSpanFromTarget(target) || element.closest(".annotation-mark, .sticky-note-marker, .sticky-marker, .summary-note-marker, button, a, input, textarea, select, .selection-composer, .annotation-card, .sticky-composer, .summary-note-composer")) return false;
    const inTextLayer = element.closest(".text-layer") === textLayerRef.current;
    const inBlankOverlay = Boolean(element.closest(".selection-preview-layer, .annotation-layer, .sticky-layer, .summary-note-layer"));
    return target === pdfPageRef.current || target === canvasRef.current || target === textLayerRef.current || inTextLayer || inBlankOverlay;
  }, []);

  const dismissOpenOverlay = useCallback(() => {
    if (selectionComposerDraft && selectionComposerNote.trim()) {
      setMessage("请先保存或取消当前批注");
      return false;
    }
    if (stickyComposerDraft && (stickyComposerText !== stickyComposerDraft.text || stickyComposerStyle !== stickyComposerDraft.style_key)) {
      setMessage("请先保存或取消便签编辑");
      return false;
    }
    if (summaryComposerDraft && summaryComposerText !== summaryComposerDraft.text) {
      setMessage("请先保存或取消当前大意");
      return false;
    }
    if (annotationCardOpen && activeAnnotation?.author === "user" && annotationEditNote !== (activeAnnotation.note ?? "")) {
      setMessage("请先保存或取消当前批注");
      return false;
    }
    if (selectionComposerDraft) {
      selectionComposerOpenRef.current = false;
      selectionPromptOpenRef.current = false;
      setSelectionComposerDraft(null);
      setSelectionComposerNote("");
      clearSelectionPreview();
    }
    if (stickyComposerDraft) {
      setStickyComposerDraft(null);
      setStickyComposerText("");
    }
    if (summaryComposerDraft) {
      setSummaryComposerDraft(null);
      setSummaryComposerText("");
    }
    if (annotationCardOpen) {
      pendingAnnotationFocusRef.current = null;
      updateAnnotationCardOpen(false);
    }
    return true;
  }, [activeAnnotation, annotationCardOpen, annotationEditNote, clearSelectionPreview, selectionComposerDraft, selectionComposerNote, stickyComposerDraft, stickyComposerStyle, stickyComposerText, summaryComposerDraft, summaryComposerText, updateAnnotationCardOpen]);

  const handleAnnotationCardDoubleClick = useCallback((event: React.MouseEvent<HTMLDivElement>) => {
    if ((!annotationCardOpen && !selectionComposerDraft && !summaryComposerDraft) || stickyPlacementMode || summaryPlacementMode || isGrabScrolling || grabRef.current) return;
    if (selectingTextRef.current) return;
    if (document.getSelection()?.isCollapsed === false) return;
    if (!isPdfBlankTarget(event.target)) return;
    event.stopPropagation();
    dismissOpenOverlay();
  }, [annotationCardOpen, dismissOpenOverlay, isGrabScrolling, isPdfBlankTarget, selectionComposerDraft, stickyPlacementMode, summaryComposerDraft, summaryPlacementMode]);

  useEffect(() => {
    setAnnotationEditNote(activeAnnotation?.author === "user" ? activeAnnotation.note ?? "" : "");
    setAnnotationExcerptExpanded(false);
  }, [activeAnnotationId, activeAnnotation?.author, activeAnnotation?.note]);

  useLayoutEffect(() => {
    const editor = annotationNoteEditorRef.current;
    if (!editor || !annotationCardOpen || activeAnnotation?.author !== "user") return;
    editor.style.height = "auto";
    editor.style.height = `${editor.scrollHeight}px`;
  }, [annotationCardOpen, activeAnnotationId, activeAnnotation?.author, annotationEditNote]);

  useLayoutEffect(() => {
    const editor = stickyNoteEditorRef.current;
    if (!editor || !stickyComposerDraft) return;
    editor.style.height = "auto";
    editor.style.height = `${editor.scrollHeight}px`;
  }, [stickyComposerDraft, stickyComposerText]);

  useLayoutEffect(() => {
    const editor = summaryNoteEditorRef.current;
    if (!editor || !summaryComposerDraft) return;
    editor.style.height = "auto";
    editor.style.height = `${editor.scrollHeight}px`;
  }, [summaryComposerDraft, summaryComposerText]);

  const updateActiveUserAnnotation = useCallback(async (changes: Record<string, unknown>) => {
    const target = activeAnnotationId ? annotations.find((item) => item.annotation_id === activeAnnotationId) : null;
    if (!target || target.author !== "user") return;
    try {
      const result = await updateUserAnnotation(target.annotation_id, { session_id: target.session_id, ...changes });
      setAnnotations((all) => all.map((item) => item.annotation_id === result.annotation.annotation_id ? result.annotation : item));
      setMessage("批注已更新");
    } catch {
      setMessage("批注更新失败");
    }
  }, [activeAnnotationId, annotations]);

 const overlaySizeStyle = useCallback((kind: OverlayKind): React.CSSProperties | undefined => {
    const preferred = overlaySizes[kind];
    if (!preferred) return undefined;
    const rendered = clampOverlaySize(kind, preferred);
    return { width: rendered.width, height: rendered.height };
  }, [clampOverlaySize, overlaySizes]);
  const renderOverlayResizeHandle = (kind: OverlayKind) => (
    <span
      className="overlay-resize-handle"
      data-overlay-resize-handle={kind}
      role="separator"
      aria-label="调整浮层大小"
      onPointerDownCapture={(event) => beginOverlayResize(kind, event)}
      onPointerMove={(event) => moveOverlayResize(kind, event)}
      onPointerUp={(event) => endOverlayResize(kind, event)}
      onPointerCancel={(event) => endOverlayResize(kind, event)}
      onLostPointerCapture={(event) => endOverlayResize(kind, event)}
    />
  );

  const renderAnnotationCard = (detached: boolean) => {
    if (!annotationCardOpen || !activeAnnotation || detached !== annotationCardDetached) return null;
    const related = annotations.filter((candidate) => candidate.paper_id === activeAnnotation.paper_id && annotationAnchorKey(candidate) === annotationAnchorKey(activeAnnotation));
    return (
      <aside ref={cardRef} className={`annotation-card card-${activeAnnotation.author}${detached ? " annotation-card-detached" : cardPosition.docked ? " annotation-card-docked" : ""}${overlayDragging === "annotation" ? " is-overlay-dragging" : ""}${overlayResizing === "annotation" ? " is-overlay-resizing" : ""}`} style={{ ...(overlayPosition ? { left: overlayPosition.left, top: overlayPosition.top, right: "auto", bottom: "auto" } : detached ? {} : { left: cardPosition.left, top: cardPosition.top }), ...overlaySizeStyle("annotation") }} data-detached={detached} onClick={(event) => event.stopPropagation()}>
        <button className="annotation-card-close" type="button" aria-label="关闭批注" onClick={() => { pendingAnnotationFocusRef.current = null; updateAnnotationCardOpen(false); }}>×</button>
        <div className="annotation-card-heading overlay-drag-handle" onMouseDown={(event) => { if (event.button === 0 && !(event.target instanceof Element && event.target.closest("button"))) event.preventDefault(); }} onPointerDown={(event) => beginOverlayDrag("annotation", event)} onPointerMove={(event) => moveOverlayDrag("annotation", event)} onPointerUp={(event) => endOverlayDrag("annotation", event)} onPointerCancel={(event) => endOverlayDrag("annotation", event)}>
          <strong>{activeAnnotation.author === "assistant" ? "AI 批注" : "用户批注"}</strong>
        </div>
        <span className={`annotation-card-excerpt${annotationExcerptExpanded ? " is-expanded" : ""}`}>{activeAnnotation.exact_text}</span>
        {activeAnnotation.exact_text.length > 220 && <button type="button" className="annotation-card-expand" onClick={() => setAnnotationExcerptExpanded((expanded) => !expanded)}>{annotationExcerptExpanded ? "收起原文" : "展开原文"}</button>}
        <span className="annotation-card-page">原文第 {activeAnnotation.page_number} 页{detached ? " · 当前已离开原文页" : ""}</span>
        {activeAnnotation.author === "assistant" && activeAnnotation.note && <p className="annotation-card-note">{activeAnnotation.note}</p>}
        <button type="button" className="annotation-card-source" onClick={() => focusAnnotation(activeAnnotation.annotation_id)}>回到原文</button>
        {related.length > 1 && <div className="annotation-switcher" aria-label="同一原文的其他标注">{related.map((candidate, index) => <button type="button" className={candidate.annotation_id === activeAnnotation.annotation_id ? "active" : ""} key={candidate.annotation_id} onClick={() => focusAnnotation(candidate.annotation_id)}>{candidate.author === "assistant" ? "AI" : "我"} {index + 1}</button>)}</div>}
        {activeAnnotation.author === "user" && <>
          <div className="annotation-style-picker" role="group" aria-label="修改批注颜色">
            {(["primary", "secondary", "tertiary"] as const).map((style) => <button
              type="button"
              key={style}
              className={`annotation-style-dot annotation-style-${style}${(activeAnnotation.style_key ?? "primary") === style ? " active" : ""}`}
              aria-label={`修改批注颜色 ${style}`}
              aria-pressed={(activeAnnotation.style_key ?? "primary") === style}
              onPointerDown={(event) => event.stopPropagation()}
              onClick={(event) => { event.stopPropagation(); if ((activeAnnotation.style_key ?? "primary") !== style) void updateActiveUserAnnotation({ style_key: style }); }}
            />)}
          </div>
          <div className="annotation-mark-type-picker" role="group" aria-label="修改批注标记类型">
            {(["underline", "highlight"] as const).map((markType) => <button type="button" key={markType} className={activeAnnotation.mark_type === markType ? "active" : ""} aria-pressed={activeAnnotation.mark_type === markType} onClick={() => void updateActiveUserAnnotation({ mark_type: markType })}>{markType === "underline" ? "划线" : "荧光笔"}</button>)}
          </div>
          <textarea
            ref={annotationNoteEditorRef}
            className="annotation-card-note-editor"
            aria-label="编辑用户批注"
            placeholder="为这条批注补充笔记"
            value={annotationEditNote}
            onChange={(event) => setAnnotationEditNote(event.target.value)}
          />
          <button type="button" onClick={() => void updateActiveUserAnnotation({ note: annotationEditNote })}>保存批注</button>
          <button type="button" onClick={() => resetOverlaySize("annotation")}>恢复默认大小</button>
        </>}
        {activeAnnotation.author === "assistant" && activeAnnotation.remember && <button type="button" onClick={() => void revokeAssistantRemember(activeAnnotation.annotation_id).then(({ annotation }) => setAnnotations((all) => all.map((entry) => entry.annotation_id === annotation.annotation_id ? annotation : entry))).catch(() => setMessage("取消记住失败"))}>取消记住</button>}
        {activeAnnotation.author === "user" && <button type="button" onClick={() => void deleteUserAnnotation(activeAnnotation.annotation_id, activeAnnotation.session_id).then(() => { setAnnotations((all) => all.filter((a) => a.annotation_id !== activeAnnotation.annotation_id)); setActiveAnnotationId(null); updateAnnotationCardOpen(false); })}>删除我的标注</button>}
        {activeAnnotation.author === "assistant" && <button type="button" onClick={() => resetOverlaySize("annotation")}>恢复默认大小</button>}
        {renderOverlayResizeHandle("annotation")}
      </aside>
    );
  };

  useEffect(() => {
    if (annotationCardOpen && activeAnnotationId && !annotations.some((item) => item.annotation_id === activeAnnotationId)) {
      setActiveAnnotationId(null);
      updateAnnotationCardOpen(false);
    }
  }, [activeAnnotationId, annotationCardOpen, annotations, updateAnnotationCardOpen]);

  const annotationAnchorKey = useCallback((item: Annotation) => `${item.page_number}|${item.exact_text}|${item.normalized_quads.map((quad) => `${quad.x.toFixed(4)},${quad.y.toFixed(4)},${quad.width.toFixed(4)},${quad.height.toFixed(4)}`).join(";")}`, []);
  const focusAnnotation = useCallback((annotationId: string) => {
    const target = annotations.find((item) => item.annotation_id === annotationId);
    if (!target) return;
    pendingAnnotationFocusRef.current = annotationId;
    setActiveAnnotationId(annotationId);
    updateAnnotationCardOpen(true);
    if (target.page_number !== pageNumber) setPageNumber(target.page_number);
  }, [annotations, pageNumber, updateAnnotationCardOpen]);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => { if (event.key === "Escape") updateAnnotationCardOpen(false); };
    window.addEventListener("keydown", onKey); return () => window.removeEventListener("keydown", onKey);
  }, [updateAnnotationCardOpen]);

  useEffect(() => {
    if (!annotationCardOpen || !activeAnnotationId) return;
    const onPointerDown = (event: PointerEvent) => {
      const target = event.target as Node;
      if (cardRef.current?.contains(target) || markRefs.current.get(activeAnnotationId)?.contains(target)) return;
      if (toolbarRef.current?.contains(target)) return;
      // Keep the card open on a single click in the page. The page-level
      // dblclick handler owns blank-area dismissal; text selection/pan guards
      // decide separately whether a new interaction may replace the card.
      if (pdfPageRef.current?.contains(target)) return;
      if (activeAnnotation?.author === "user" && annotationEditNote !== (activeAnnotation.note ?? "")) {
        event.preventDefault();
        setMessage("请先保存批注");
        return;
      }
      updateAnnotationCardOpen(false);
    };
    document.addEventListener("pointerdown", onPointerDown);
    return () => document.removeEventListener("pointerdown", onPointerDown);
  }, [activeAnnotation, activeAnnotationId, annotationCardOpen, annotationEditNote, updateAnnotationCardOpen]);

  useEffect(() => {
    const viewport = pageShellRef.current;
    if (!viewport || !annotationCardOpen) return;
    const onScroll = () => setCardLayoutVersion((value) => value + 1);
    viewport.addEventListener("scroll", onScroll, { passive: true });
    return () => viewport.removeEventListener("scroll", onScroll);
  }, [annotationCardOpen]);

  useLayoutEffect(() => {
    const node = selectionComposerDraft ? selectionComposerRef.current : annotationCardOpen ? cardRef.current : stickyComposerDraft ? stickyComposerRef.current : summaryComposerRef.current;
    if (!node) return;
    if (overlayPosition) {
      const next = clampOverlayPosition(overlayPosition.left, overlayPosition.top, node);
      if (next.left !== overlayPosition.left || next.top !== overlayPosition.top) setOverlayPosition(next);
      return;
    }
    if (annotationCardOpen && !annotationCardDetached) {
      setCardPosition((position) => {
        const next = clampOverlayPosition(position.left, position.top, node);
        if (next.left === position.left && next.top === position.top) return position;
        return { ...position, ...next };
      });
    }
  }, [annotationCardDetached, annotationCardOpen, clampOverlayPosition, compact, overlayPosition, overlaySizes, selectionComposerDraft, stickyComposerDraft, summaryComposerDraft, viewerHeight, viewerWidth]);


  useEffect(() => {
    setOverlayPosition(null);
    overlayDragRef.current = null;
    setOverlayDragging(null);
    setSummaryPlacementMode(false);
    setSummaryComposerDraft(null);
    setSummaryComposerText("");
  }, [paper?.paper_id, sessionId]);

  useEffect(() => { setRenderReadyPage(null); }, [pageNumber, paper]);

  useLayoutEffect(() => {
    if (!annotationCardOpen || !activeAnnotationId || !pageShellRef.current || !pdfPageRef.current) return;
    const target = annotations.find((item) => item.annotation_id === activeAnnotationId);
    if (!target) return;
    const mark = markRefs.current.get(activeAnnotationId);
    const card = cardRef.current;
    const viewport = pageShellRef.current;
    if (!card) return;
    if (target.page_number !== pageNumber) {
      setCardPosition({ left: 0, top: 0, docked: false });
      return;
    }
    if (renderReadyPage !== pageNumber || !mark) return;
    if (pendingAnnotationFocusRef.current === activeAnnotationId) {
      pendingAnnotationFocusRef.current = null;
      mark.scrollIntoView({ behavior: "smooth", block: "center", inline: "center" });
      const mr = mark.getBoundingClientRect();
      const scrollTarget = viewport.scrollTop + mr.top - viewport.getBoundingClientRect().top - (viewport.clientHeight - mr.height) / 2;
      viewport.scrollTo({ top: Math.max(0, scrollTarget), behavior: "smooth" });
    }
    const vr = viewport.getBoundingClientRect(); const pr = pdfPageRef.current.getBoundingClientRect(); const rr = readerRef.current?.getBoundingClientRect() ?? vr; const ar = mark.getBoundingClientRect();
    const narrow = vr.width < 560;
    if (narrow) { setCardPosition({ left: 8, top: Math.max(8, viewport.clientHeight - card.offsetHeight - 8), docked: true }); return; }
    const gap = 12; const width = card.offsetWidth; const height = card.offsetHeight;
    const toolbarBottom = (toolbarRef.current?.getBoundingClientRect().bottom ?? vr.top) - pr.top;
    const safeTop = Math.max(8, toolbarBottom + 8); const safeBottom = Math.max(safeTop + 40, vr.bottom - pr.top - 8);
    const anchorX = ar.right - pr.left; const anchorY = ar.top - pr.top + ar.height / 2;
    const candidates = [anchorX + gap, ar.left - pr.left - width - gap, anchorX - width / 2, anchorX - width / 2];
    let left = candidates[0]; let top = anchorY - height / 2;
    if (left + width > pr.width - 8) left = candidates[1];
    if (left < 8) { left = Math.max(8, Math.min(pr.width - width - 8, candidates[2])); top = anchorY < height + gap ? ar.bottom - pr.top + gap : ar.top - pr.top - height - gap; }
    const maxTop = Math.max(safeTop, safeBottom - height);
    setCardPosition({ left: Math.max(8, pr.left - rr.left + Math.min(pr.width - width - 8, left)), top: Math.max(8, pr.top - rr.top + Math.max(safeTop, Math.min(maxTop, top))), docked: false });
  }, [activeAnnotationId, annotationCardOpen, pageNumber, renderReadyPage, viewerWidth, annotations, toolbarSlim, cardLayoutVersion]);

  const closeSelectionComposer = useCallback(() => {
    selectionComposerOpenRef.current = false;
    selectionPromptOpenRef.current = false;
    setSelectionComposerDraft(null);
    setSelectionComposerNote("");
    setSelectionVocabularyKey("");
  }, []);

  const discardSelectionComposer = useCallback(() => {
    closeSelectionComposer();
    clearSelectionPreview();
  }, [clearSelectionPreview, closeSelectionComposer]);

  const saveSelectionComposerNote = useCallback(async () => {
    const draft = selectionComposerDraft;
    const note = selectionComposerNote.trim();
    if (!draft || !note) return;
    try {
      const result = await createUserAnnotation({ ...draft, note, style_key: selectionComposerStyle, mark_type: selectionComposerMarkType, idempotency_key: crypto.randomUUID() });
      setAnnotations((items) => [...items, result.annotation]);
      closeSelectionComposer();
      clearSelectionPreview();
    } catch {
      setMessage("标注保存失败");
    }
  }, [clearSelectionPreview, closeSelectionComposer, selectionComposerDraft, selectionComposerNote, selectionComposerStyle, selectionComposerMarkType]);

  const addSelectionToVocabulary = useCallback(async () => {
    const draft = selectionComposerDraft;
    const term = draft?.exact_text.trim();
    if (!draft || !term || vocabularySaving) return;
    setVocabularySaving(true);
    try {
      const result = await createVocabularyFromSelection({
        term,
        selection: { ...draft, revision: Math.max(1, revisionRef.current + 1) },
        idempotency_key: selectionVocabularyKey || crypto.randomUUID(),
      });
      setMessage(result.replayed ? "该选区已在词汇中" : "已加入词汇");
    } catch (error) {
      setMessage(error instanceof Error ? `加入词汇失败：${error.message}` : "加入词汇失败");
    } finally {
      setVocabularySaving(false);
    }
  }, [selectionComposerDraft, selectionVocabularyKey, vocabularySaving]);

  const closeStickyComposer = useCallback(() => {
    setStickyComposerDraft(null);
    setStickyComposerText("");
  }, []);

  const saveStickyComposer = useCallback(async () => {
    const draft = stickyComposerDraft;
    const text = stickyComposerText.trim();
    if (!draft || !text || !paper) return;
    try {
      if (draft.id) {
        const result = await updateStickyNote(draft.id, { text, style_key: stickyComposerStyle });
        setStickyNotes((items) => items.map((item) => item.id === draft.id ? result.sticky_note : item));
      } else {
        const result = await createStickyNote({ paper_id: paper.paper_id, page: pageNumber, x: draft.x, y: draft.y, text, style_key: stickyComposerStyle });
        setStickyNotes((items) => [...items, result.sticky_note]);
      }
      closeStickyComposer();
    } catch {
      setMessage("便签保存失败");
    }
  }, [closeStickyComposer, pageNumber, paper, stickyComposerDraft, stickyComposerStyle, stickyComposerText]);

  const editStickyNote = useCallback((note: StickyNote) => {
    setStickyComposerDraft({ id: note.id, x: note.x, y: note.y, text: note.text, style_key: note.style_key });
    setStickyComposerText(note.text);
    setStickyComposerStyle(note.style_key);
  }, []);

  const closeSummaryComposer = useCallback(() => {
    setSummaryComposerDraft(null);
    setSummaryComposerText("");
  }, []);

  const saveSummaryComposer = useCallback(async () => {
    const draft = summaryComposerDraft;
    const text = summaryComposerText.trim();
    if (!draft || !text || !paper) return;
    try {
      if (draft.id) {
        const result = await updateSummaryNote(draft.id, { text });
        setSummaryNotes((items) => items.map((item) => item.summary_note_id === draft.id ? result.summary_note : item));
      } else {
        const result = await createSummaryNote({ paper_id: paper.paper_id, page_number: pageNumber, normalized_y: draft.normalized_y, text });
        setSummaryNotes((items) => [...items, result.summary_note]);
      }
      closeSummaryComposer();
      setMessage("大意已保存");
    } catch {
      setMessage("大意保存失败");
    }
  }, [closeSummaryComposer, pageNumber, paper, summaryComposerDraft, summaryComposerText]);

  const editSummaryNote = useCallback((note: SummaryNote) => {
    setSummaryComposerDraft({ id: note.summary_note_id, normalized_y: note.normalized_y, text: note.text });
    setSummaryComposerText(note.text);
  }, []);

  const annotationIndexItems = [
    ...annotations.map((annotation) => ({
      key: annotation.annotation_id,
      kind: "annotation" as const,
      page: annotation.page_number,
      x: annotation.normalized_quads[0]?.x ?? 0,
      y: annotation.normalized_quads[0]?.y ?? 0,
      type: annotation.mark_type === "highlight" ? "荧光" : "划线",
      filter: annotation.mark_type === "highlight" ? "highlight" as const : "underline" as const,
      excerpt: annotation.exact_text,
      note: annotation.note,
      annotation,
    })),
     ...stickyNotes.map((note) => ({
      key: note.id,
      kind: "sticky" as const,
      page: note.page,
      x: note.x,
      y: note.y,
      type: "便签",
      filter: "sticky" as const,
      excerpt: note.text,
      note: null,
       sticky: note,
     })),
     ...summaryNotes.map((note) => ({
       key: note.summary_note_id,
       kind: "summary" as const,
       page: note.page_number,
       x: 0,
       y: note.normalized_y,
       type: "大意",
       filter: "summary" as const,
       excerpt: note.text,
       note: null,
       summary: note,
     })),
   ]
    .filter((item) => annotationIndexFilter === "all" || item.filter === annotationIndexFilter || (annotationIndexFilter === "note" && Boolean(item.note?.trim())))
    .sort((a, b) => a.page - b.page || a.y - b.y || a.x - b.x);

  const focusAnnotationIndexItem = useCallback((item: (typeof annotationIndexItems)[number]) => {
    setAnnotationIndexFocus(item.key);
    window.setTimeout(() => setAnnotationIndexFocus((current) => current === item.key ? null : current), 1200);
    if (item.kind === "annotation") focusAnnotation(item.annotation.annotation_id);
    else if (item.kind === "sticky") {
      if (item.sticky.page !== pageNumber) setPageNumber(item.sticky.page);
      editStickyNote(item.sticky);
    } else {
      pendingSummaryFocusRef.current = item.summary.summary_note_id;
      if (item.summary.page_number !== pageNumber) setPageNumber(item.summary.page_number);
      else {
        const marker = summaryMarkerRefs.current.get(item.summary.summary_note_id);
        marker?.scrollIntoView({ behavior: "smooth", block: "center", inline: "nearest" });
      }
    }
  }, [editStickyNote, focusAnnotation, pageNumber]);

  useLayoutEffect(() => {
    const pendingId = pendingSummaryFocusRef.current;
    if (!pendingId || renderReadyPage !== pageNumber) return;
    const marker = summaryMarkerRefs.current.get(pendingId);
    if (!marker) return;
    pendingSummaryFocusRef.current = null;
    marker.scrollIntoView({ behavior: "smooth", block: "center", inline: "nearest" });
  }, [pageNumber, renderReadyPage, summaryNotes]);

  const removeStickyNote = useCallback(async (noteId: string) => {
    try {
      await deleteStickyNote(noteId);
      setStickyNotes((items) => items.filter((item) => item.id !== noteId));
      closeStickyComposer();
    } catch {
      setMessage("便签删除失败");
    }
  }, [closeStickyComposer]);

  const removeSummaryNote = useCallback(async (summaryNoteId: string) => {
    if (!window.confirm("删除这条大意？")) return;
    try {
      await deleteSummaryNote(summaryNoteId);
      setSummaryNotes((items) => items.filter((item) => item.summary_note_id !== summaryNoteId));
      closeSummaryComposer();
      setMessage("大意已删除");
    } catch {
      setMessage("大意删除失败");
    }
  }, [closeSummaryComposer]);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape" && selectionComposerOpenRef.current) discardSelectionComposer();
      if (event.key === "Escape") {
        if (summaryPlacementMode) setMessage("大意放置已取消");
        else if (summaryComposerDraft) setMessage("大意编辑已取消");
        else if (stickyPlacementMode) setMessage("便签放置已取消");
        else if (stickyComposerDraft) setMessage("便签编辑已取消");
        setSummaryPlacementMode(false);
        closeSummaryComposer();
        setStickyPlacementMode(false);
        closeStickyComposer();
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [closeStickyComposer, closeSummaryComposer, discardSelectionComposer, summaryComposerDraft, summaryPlacementMode, stickyComposerDraft, stickyPlacementMode]);

  const captureSelection = useCallback(() => {
    const selection = document.getSelection();
    const layer = textLayerRef.current;
    if (!selection || selection.isCollapsed || !layer || !paper || !sessionId) return;
    const range = selection.getRangeAt(0);
    if (!layer.contains(range.commonAncestorContainer)) return;
    if (annotationCardOpenRef.current) {
      const current = activeAnnotationId ? annotations.find((item) => item.annotation_id === activeAnnotationId) : null;
      if (current?.author === "user" && annotationEditNote !== (current.note ?? "")) {
        setMessage("请先保存批注");
        return;
      }
      pendingAnnotationFocusRef.current = null;
      updateAnnotationCardOpen(false);
    }
    const exactText = (selectionTextFromTextLayer(range, layer) || normalizeText(selection.toString())).slice(0, 2_000);
    if (!exactText) return;
    const quads = buildNormalizedSelectionQuads(range, layer);
    if (quads.length === 0) return;
    const text = pageTextRef.current;
    const index = text.indexOf(exactText);
    const prefix = index >= 0 ? text.slice(Math.max(0, index - 160), index) : "";
    const suffix = index >= 0 ? text.slice(index + exactText.length, index + exactText.length + 160) : "";
    const viewport = viewportOriginalRef.current;
    const draft: SelectionDraft = {
      paper_id: paper.paper_id,
      session_id: sessionId,
      page_number: pageNumber,
      exact_text: exactText,
      prefix,
      suffix,
      normalized_quads: quads,
      page_width: viewport.width,
      page_height: viewport.height,
      rotation: viewport.rotation,
    };
    const frozenDraft: SelectionDraft = {
      ...draft,
      normalized_quads: draft.normalized_quads.map((quad) => ({ ...quad })),
    };
    scheduleState(pageNumber, text, frozenDraft);
    selectionPromptOpenRef.current = true;
    selectionComposerOpenRef.current = true;
    setSelectionComposerDraft(frozenDraft);
    setSelectionComposerNote("");
    setSelectionVocabularyKey(crypto.randomUUID());
  }, [activeAnnotationId, annotationEditNote, annotations, pageNumber, paper, scheduleState, sessionId, updateAnnotationCardOpen]);

  async function openPaper(nextPaper: Paper) {
    const session = await createSession(nextPaper.paper_id);
    localStorage.setItem(ACTIVE_PAPER, nextPaper.paper_id);
    localStorage.setItem(ACTIVE_SESSION, session.session_id);
    revisionRef.current = 0;
    setPageNumber(1);
    setSessionId(session.session_id);
    setPaper(nextPaper);
  }

  async function handleUpload(file: File | undefined) {
    if (!file) return;
    setUploading(true);
    setMessage("正在校验并保存 PDF…");
    try {
      const result = await uploadPaper(file);
      await refreshPapers();
      await openPaper(result.paper);
      setMessage(result.deduplicated ? "已打开已有的相同 PDF" : "PDF 已安全保存");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "上传失败");
    } finally {
      setUploading(false);
    }
  }

  const syncLabel = syncState === "saving" ? "正在保存" : syncState === "saved" ? "已保存" : syncState === "error" ? "保存冲突" : "等待阅读";
  const showReadingQuestionPanel = READING_MODE && readingPanelOpen && readingPanelKind === "questions" && Boolean(readingTask);
  const showReadingAnnotationPanel = READING_MODE && readingPanelOpen && readingPanelKind === "annotations";
  const showAnnotationPanel = READING_MODE ? showReadingAnnotationPanel : annotationsPanelOpen;
  const showReaderSidePanel = showReadingQuestionPanel || showAnnotationPanel;
  const toggleReadingPanel = (kind: "questions" | "annotations") => {
    if (!READING_MODE) return;
    if (readingPanelOpen && readingPanelKind === kind) setReadingPanelOpen(false);
    else { setReadingPanelKind(kind); setReadingPanelOpen(true); }
  };

  return (
    <main className={`app-shell${headerCompact ? " header-compact" : ""}`} data-header-compact={headerCompact} onPointerMove={handleReaderPointerMove} onPointerDown={handleReaderPointerDown}>
      <header className="topbar" data-compact={headerCompact} ref={headerRef} onPointerDown={beginHeaderOperation} onPointerUp={endHeaderOperation} onPointerCancel={endHeaderOperation}>
        <div className="hero-copy">
          <p className="eyebrow">CAELIAE READ · LOCAL V0.1</p>
          <h1>和一篇论文待在一起</h1>
        </div>
        <span className="compact-brand">CAELIAE READ</span>
        <label className="upload-button">
          <input
            type="file"
            accept="application/pdf,.pdf"
            disabled={uploading}
            onChange={(event) => void handleUpload(event.target.files?.[0])}
          />
          {uploading ? "上传中…" : "上传 PDF"}
        </label>
      </header>

      <section className={`workspace${compact ? " compact" : ""}`} data-layout={compact ? "compact" : "desktop"} data-drawer-open={drawerOpen} data-papers-open={papersPanelOpen} data-annotations-open={showAnnotationPanel} data-annotation-card-open={annotationCardOpen} data-active-annotation-id={activeAnnotationId ?? ""} data-selecting-text={isSelectingText} ref={workspaceRef}>
        <aside className="library side-panel" aria-label="论文列表">
          <div className="section-heading side-panel-heading">
            <span>本地论文 <small>{papers.length}</small></span>
            <button type="button" className="side-panel-close" aria-label="关闭论文面板" onClick={() => updatePapersPanelOpen(false)}>×</button>
          </div>
          <div className="paper-list">
            {papers.map((item) => (
              <button
                className={item.paper_id === paper?.paper_id ? "paper-card active" : "paper-card"}
                key={item.paper_id}
                type="button"
                onClick={() => void openPaper(item)}
              >
                <strong>{item.original_filename}</strong>
                <span>{item.page_count} 页 · {(item.size_bytes / 1024).toFixed(0)} KB</span>
              </button>
            ))}
            {papers.length === 0 && <p className="empty">PDF 只保存在这台 Caeliae Read 的数据目录中。</p>}
          </div>
        </aside>

        <section className={`reader${showAnnotationPanel ? " has-annotation-index" : ""}${showReaderSidePanel ? " has-reading-panel" : ""}${READING_MODE ? " has-reading-task" : ""}`} ref={readerRef} aria-label="PDF 阅读器">
           {READING_MODE && readingTask && <header className="reading-task-header"><div><span className="reading-task-kicker">READING TASK</span><strong>{readingTask.task.title}</strong>{readingTask.task.instructions && <p>{readingTask.task.instructions}</p>}</div><div className="reading-task-header-actions"><span>{readingTask.task.due_at ? `截止 ${readingTask.task.due_at}` : "任务阅读"}</span><a href="/reader/reading.html">返回任务</a></div></header>}
           {!compact && <nav className={`reader-controls desktop-toolbar${toolbarSlim ? " toolbar-slim" : ""}`} data-slim={toolbarSlim} ref={toolbarRef} onPointerLeave={scheduleToolbarCollapse} onBlur={scheduleToolbarCollapse} onPointerDown={beginToolbarOperation} onPointerUp={endToolbarOperation} onPointerCancel={endToolbarOperation}>
             {toolbarSlim ? <button className="toolbar-reveal-handle" type="button" aria-label="展开阅读工具栏" onClick={revealToolbar}>⌄</button> : <>
              <span className="toolbar-group toolbar-navigation">
                <button type="button" className={papersPanelOpen ? "panel-toggle active" : "panel-toggle"} aria-pressed={papersPanelOpen} onClick={() => updatePapersPanelOpen(!papersPanelOpen)}>论文</button>
                <button type="button" disabled={!documentProxy || pageNumber <= 1} onClick={() => setPageNumber((value) => value - 1)}>上一页</button>
             <span>{documentProxy ? `${pageNumber} / ${pageCount}` : "尚未打开"}</span>
             <button type="button" disabled={!documentProxy || pageNumber >= pageCount} onClick={() => setPageNumber((value) => value + 1)}>下一页</button>
              </span>
              <span className="toolbar-group toolbar-zoom">
                <span className="pdf-zoom-controls"><button type="button" onClick={() => setPdfZoom((z) => Math.max(.75, (z ?? 1) - .25))}>−</button><span>{pdfZoom === null ? "适宽" : `${Math.round(pdfZoom * 100)}%`}</span><button type="button" onClick={() => setPdfZoom((z) => Math.min(2, (z ?? 1) + .25))}>＋</button><button type="button" onClick={() => setPdfZoom(null)}>适合宽度</button></span>
              </span>
              <span className="toolbar-group toolbar-annotation-actions">
                <button type="button" className={showAnnotationPanel ? "panel-toggle active" : "panel-toggle"} aria-pressed={showAnnotationPanel} onClick={() => READING_MODE ? toggleReadingPanel("annotations") : updateAnnotationsPanelOpen(!annotationsPanelOpen)}>批注</button>
                 <button type="button" className={stickyPlacementMode ? "sticky-tool active" : "sticky-tool"} aria-pressed={stickyPlacementMode} onClick={() => { const next = !stickyPlacementMode; setStickyPlacementMode(next); setSummaryPlacementMode(false); closeStickyComposer(); closeSummaryComposer(); setMessage(next ? STICKY_PLACEMENT_MESSAGE : "便签放置已取消"); }}>便签</button>
                 <button type="button" className={summaryPlacementMode ? "summary-tool active" : "summary-tool"} aria-pressed={summaryPlacementMode} onClick={() => { const next = !summaryPlacementMode; setSummaryPlacementMode(next); setStickyPlacementMode(false); closeSummaryComposer(); closeStickyComposer(); setMessage(next ? SUMMARY_PLACEMENT_MESSAGE : "大意放置已取消"); }}>+ 大意</button>
                {READING_MODE && <button type="button" className={showReadingQuestionPanel ? "panel-toggle active" : "panel-toggle"} aria-pressed={showReadingQuestionPanel} onClick={() => toggleReadingPanel("questions")}>题目</button>}
                <button type="button" onClick={() => { window.location.href = "/reader/reading.html"; }}>阅读</button>
                <button type="button" onClick={() => { window.location.href = "/reader/vocabulary.html"; }}>词汇</button>
              </span>
           </>}
          </nav>}
           {compact && <nav className={`reader-controls compact-toolbar${toolbarSlim ? " slim" : ""}`} data-slim={toolbarSlim} ref={toolbarRef} onPointerLeave={scheduleToolbarCollapse} onBlur={scheduleToolbarCollapse} onPointerDown={beginToolbarOperation} onPointerUp={endToolbarOperation} onPointerCancel={endToolbarOperation}>
             {toolbarSlim ? <button className="toolbar-reveal-handle slim-expand" type="button" aria-label="展开阅读工具栏" onClick={revealToolbar}>⌄</button> : <>
              <button className="compact-paper-trigger" type="button" title={paper?.original_filename ?? "选择论文"} aria-pressed={drawerOpen} onClick={() => updatePapersPanelOpen(compact ? !drawerOpen : !papersPanelOpen)}>论文<span>{paper?.original_filename ?? "选择论文"}</span></button>
              <button type="button" disabled={!documentProxy || pageNumber <= 1} onClick={() => setPageNumber((value) => value - 1)}>上一页</button>
              <span>{documentProxy ? `${pageNumber} / ${pageCount}` : "尚未打开"}</span>
              <button type="button" disabled={!documentProxy || pageNumber >= pageCount} onClick={() => setPageNumber((value) => value + 1)}>下一页</button>
              <span className="pdf-zoom-controls"><button type="button" onClick={() => setPdfZoom((z) => Math.max(.75, (z ?? 1) - .25))}>−</button><span>{pdfZoom === null ? "适宽" : `${Math.round(pdfZoom * 100)}%`}</span><button type="button" onClick={() => setPdfZoom((z) => Math.min(2, (z ?? 1) + .25))}>＋</button><button type="button" onClick={() => setPdfZoom(null)}>适合宽度</button></span>
               <button type="button" className={showAnnotationPanel ? "panel-toggle active" : "panel-toggle"} aria-pressed={showAnnotationPanel} onClick={() => READING_MODE ? toggleReadingPanel("annotations") : updateAnnotationsPanelOpen(!annotationsPanelOpen)}>批注</button>
                <button type="button" className={stickyPlacementMode ? "sticky-tool active" : "sticky-tool"} aria-pressed={stickyPlacementMode} onClick={() => { const next = !stickyPlacementMode; setStickyPlacementMode(next); setSummaryPlacementMode(false); closeStickyComposer(); closeSummaryComposer(); setMessage(next ? STICKY_PLACEMENT_MESSAGE : "便签放置已取消"); }}>便签</button>
                <button type="button" className={summaryPlacementMode ? "summary-tool active" : "summary-tool"} aria-pressed={summaryPlacementMode} onClick={() => { const next = !summaryPlacementMode; setSummaryPlacementMode(next); setStickyPlacementMode(false); closeSummaryComposer(); closeStickyComposer(); setMessage(next ? SUMMARY_PLACEMENT_MESSAGE : "大意放置已取消"); }}>+ 大意</button>
               {READING_MODE && <button type="button" className={showReadingQuestionPanel ? "panel-toggle active" : "panel-toggle"} aria-pressed={showReadingQuestionPanel} onClick={() => toggleReadingPanel("questions")}>题目</button>}
               <button type="button" onClick={() => { window.location.href = "/reader/reading.html"; }}>阅读</button>
               <button type="button" onClick={() => { window.location.href = "/reader/vocabulary.html"; }}>词汇</button>
            </>}
          </nav>}
           {stickyPlacementMode && <div className="sticky-placement-hint" role="status">{STICKY_PLACEMENT_MESSAGE}</div>}
           {summaryPlacementMode && <div className="summary-placement-hint" role="status">{SUMMARY_PLACEMENT_MESSAGE}</div>}
          {compact && drawerOpen && <div className="paper-drawer" role="dialog" aria-label="选择论文" onClick={() => updateDrawerOpen(false)}><div className="paper-drawer-panel" onClick={(e) => e.stopPropagation()}><header><strong>本地论文（{papers.length}）</strong><button type="button" onClick={() => updatePapersPanelOpen(false)} aria-label="关闭论文列表">×</button></header><div className="paper-list">{papers.map((item) => <button className={item.paper_id === paper?.paper_id ? "paper-card active" : "paper-card"} key={item.paper_id} type="button" title={item.original_filename} onClick={() => { void openPaper(item); updateDrawerOpen(false); }}><strong>{item.original_filename}</strong><span>{item.page_count} 页</span></button>)}</div></div></div>}
          {selectionComposerDraft && <aside
            ref={selectionComposerRef}
            className={`selection-composer${overlayDragging === "composer" ? " is-overlay-dragging" : ""}${overlayResizing === "composer" ? " is-overlay-resizing" : ""}`}
            aria-label="为选区添加批注"
            style={{ ...(overlayPosition ? { left: overlayPosition.left, top: overlayPosition.top, right: "auto", bottom: "auto" } : {}), ...overlaySizeStyle("composer") }}
            onPointerDown={(event) => event.stopPropagation()}
            onClick={(event) => event.stopPropagation()}
          >
            <div className="selection-composer-heading overlay-drag-handle" onMouseDown={(event) => { if (event.button === 0 && !(event.target instanceof Element && event.target.closest("button"))) event.preventDefault(); }} onPointerDown={(event) => beginOverlayDrag("composer", event)} onPointerMove={(event) => moveOverlayDrag("composer", event)} onPointerUp={(event) => endOverlayDrag("composer", event)} onPointerCancel={(event) => endOverlayDrag("composer", event)}>
              <strong>添加批注</strong>
              <button type="button" className="selection-composer-close" aria-label="关闭批注编辑器" onClick={discardSelectionComposer}>×</button>
            </div>
            <span className="selection-composer-source">原文第 {selectionComposerDraft.page_number} 页 · 选区已冻结</span>
            <div className="annotation-style-picker" role="group" aria-label="批注颜色层级">
              {(["primary", "secondary", "tertiary"] as const).map((style) => <button
                type="button"
                key={style}
                className={`annotation-style-dot annotation-style-${style}${selectionComposerStyle === style ? " active" : ""}`}
                aria-label={`批注颜色 ${style}`}
                aria-pressed={selectionComposerStyle === style}
                onClick={() => { setSelectionComposerStyle(style); localStorage.setItem(LAST_USER_ANNOTATION_STYLE, style); }}
              />)}
            </div>
            <div className="annotation-mark-type-picker" role="group" aria-label="批注标记类型">
              {(["underline", "highlight"] as const).map((markType) => <button type="button" key={markType} className={selectionComposerMarkType === markType ? "active" : ""} aria-pressed={selectionComposerMarkType === markType} onClick={() => setSelectionComposerMarkType(markType)}>{markType === "underline" ? "划线" : "荧光笔"}</button>)}
            </div>
            <textarea
              aria-label="为这段原文添加问题或笔记"
              placeholder="为这段原文添加问题或笔记"
              value={selectionComposerNote}
              onChange={(event) => setSelectionComposerNote(event.target.value)}
            />
            <div className="selection-composer-actions">
              <button type="button" disabled={!selectionComposerNote.trim()} onClick={() => void saveSelectionComposerNote()}>保存笔记</button>
              <button type="button" onClick={discardSelectionComposer}>仅保存选区</button>
              <button type="button" className="selection-vocabulary-action" disabled={vocabularySaving} data-testid="selection-add-vocabulary" onClick={() => void addSelectionToVocabulary()}>{vocabularySaving ? "加入中…" : "加入词汇"}</button>
              <button type="button" onClick={() => resetOverlaySize("composer")}>恢复默认大小</button>
            </div>
            {renderOverlayResizeHandle("composer")}
          </aside>}
           {stickyComposerDraft && <aside ref={stickyComposerRef} className={`sticky-composer${overlayDragging === "sticky" ? " is-overlay-dragging" : ""}${overlayResizing === "sticky" ? " is-overlay-resizing" : ""}`} aria-label={stickyComposerDraft.id ? "编辑便签" : "添加便签"} style={{ ...(overlayPosition ? { left: overlayPosition.left, top: overlayPosition.top, right: "auto", bottom: "auto" } : {}), ...overlaySizeStyle("sticky") }} onPointerDown={(event) => event.stopPropagation()} onClick={(event) => event.stopPropagation()}>
            <div className="selection-composer-heading overlay-drag-handle" onMouseDown={(event) => { if (event.button === 0 && !(event.target instanceof Element && event.target.closest("button"))) event.preventDefault(); }} onPointerDown={(event) => beginOverlayDrag("sticky", event)} onPointerMove={(event) => moveOverlayDrag("sticky", event)} onPointerUp={(event) => endOverlayDrag("sticky", event)} onPointerCancel={(event) => endOverlayDrag("sticky", event)}>
              <strong>{stickyComposerDraft.id ? "编辑便签" : "添加便签"}</strong>
              <div className="annotation-style-picker" role="group" aria-label="便签颜色层级">
                {(["primary", "secondary", "tertiary"] as const).map((style) => <button type="button" key={style} className={"annotation-style-dot annotation-style-" + style + (stickyComposerStyle === style ? " active" : "")} aria-label={"便签颜色层级 " + style} aria-pressed={stickyComposerStyle === style} onClick={() => { setStickyComposerStyle(style); localStorage.setItem(LAST_USER_ANNOTATION_STYLE, style); }} />)}
              </div>
              <button type="button" className="selection-composer-close" aria-label="关闭便签编辑器" onClick={closeStickyComposer}>×</button>
            </div>
            <textarea ref={stickyNoteEditorRef} autoFocus aria-label="便签内容" placeholder="写下便签" value={stickyComposerText} onChange={(event) => setStickyComposerText(event.target.value)} />
            <div className="selection-composer-actions">
              <button type="button" disabled={!stickyComposerText.trim()} onClick={() => void saveStickyComposer()}>保存便签</button>
              {stickyComposerDraft.id && <button type="button" onClick={() => void removeStickyNote(stickyComposerDraft.id!)}>删除</button>}
              <button type="button" onClick={() => resetOverlaySize("sticky")}>恢复默认大小</button>
            </div>
             {renderOverlayResizeHandle("sticky")}
           </aside>}
           {summaryComposerDraft && <aside ref={summaryComposerRef} className={`summary-note-composer${overlayDragging === "summary" ? " is-overlay-dragging" : ""}${overlayResizing === "summary" ? " is-overlay-resizing" : ""}`} aria-label={summaryComposerDraft.id ? "编辑大意" : "添加大意"} style={{ ...(overlayPosition ? { left: overlayPosition.left, top: overlayPosition.top, right: "auto", bottom: "auto" } : {}), ...overlaySizeStyle("summary") }} onPointerDown={(event) => event.stopPropagation()} onClick={(event) => event.stopPropagation()}>
             <div className="selection-composer-heading overlay-drag-handle" onMouseDown={(event) => { if (event.button === 0 && !(event.target instanceof Element && event.target.closest("button"))) event.preventDefault(); }} onPointerDown={(event) => beginOverlayDrag("summary", event)} onPointerMove={(event) => moveOverlayDrag("summary", event)} onPointerUp={(event) => endOverlayDrag("summary", event)} onPointerCancel={(event) => endOverlayDrag("summary", event)}>
               <strong>{summaryComposerDraft.id ? "编辑大意" : "添加大意"}</strong>
               <button type="button" className="selection-composer-close" aria-label="关闭大意编辑器" onClick={closeSummaryComposer}>×</button>
             </div>
             <span className="selection-composer-source">第 {summaryComposerDraft.id ? (summaryNotes.find((note) => note.summary_note_id === summaryComposerDraft.id)?.page_number ?? pageNumber) : pageNumber} 页 · 大意</span>
             <textarea ref={summaryNoteEditorRef} autoFocus aria-label="大意内容" placeholder="用一句话概括这一段" value={summaryComposerText} onChange={(event) => setSummaryComposerText(event.target.value)} />
             <div className="selection-composer-actions">
               <button type="button" disabled={!summaryComposerText.trim()} onClick={() => void saveSummaryComposer()}>保存大意</button>
               {summaryComposerDraft.id && <button type="button" onClick={() => void removeSummaryNote(summaryComposerDraft.id!)}>删除</button>}
               <button type="button" onClick={() => resetOverlaySize("summary")}>恢复默认大小</button>
             </div>
             {renderOverlayResizeHandle("summary")}
           </aside>}
          <div
             className={`page-stage${isGrabScrolling ? " is-grab-scrolling" : ""}${stickyPlacementMode ? " sticky-placement-mode" : ""}${summaryPlacementMode ? " summary-placement-mode" : ""}`}
             ref={pageShellRef}
             onPointerDown={(event) => {
               if (overlayIsOpen && isPdfBlankTarget(event.target)) {
                 overlayBlankPointerRef.current = event.pointerId;
                 event.stopPropagation();
                 return;
               }
               if (selectionComposerOpenRef.current) return;
               const textTarget = isTextLayerContentTarget(event.target) && !spaceHeldRef.current;
               if (textTarget && annotationCardOpenRef.current) {
                 if (activeAnnotation?.author === "user" && annotationEditNote !== (activeAnnotation.note ?? "")) {
                   setMessage("请先保存批注");
                   event.preventDefault();
                   return;
                 }
                 pendingAnnotationFocusRef.current = null;
                 updateAnnotationCardOpen(false);
               }
               if (textTarget) {
                selectionPointerDownRef.current = true;
                selectingTextRef.current = true;
                setIsSelectingText(true);
                return;
               }
                if (stickyPlacementMode && event.button === 0) {
                  const canStartPlacement = isDragSurfaceTarget(event.target);
                  if (canStartPlacement) stickyPointerRef.current = { pointerId: event.pointerId };
                }
                if (summaryPlacementMode && event.button === 0 && isSummaryPlacementTarget(event.target, event.clientX)) {
                  summaryPointerRef.current = { pointerId: event.pointerId };
                }
                handleStagePointerDown(event);
             }}
            onPointerMove={handleStagePointerMove}
            onPointerUp={(event) => {
              if (overlayBlankPointerRef.current === event.pointerId) {
                overlayBlankPointerRef.current = null;
                event.stopPropagation();
                return;
              }
              const panned = handleStagePointerUp(event);
               const stickyPointer = stickyPointerRef.current;
               stickyPointerRef.current = null;
               const summaryPointer = summaryPointerRef.current;
               summaryPointerRef.current = null;
              if (!panned && stickyPointer?.pointerId === event.pointerId && stickyPlacementMode && !drawerOpenRef.current && !annotationCardOpenRef.current && openStickyComposerAt(event.clientX, event.clientY)) {
                suppressStageClickRef.current = true;
                window.setTimeout(() => { suppressStageClickRef.current = false; }, 120);
                 return;
               }
               if (!panned && summaryPointer?.pointerId === event.pointerId && summaryPlacementMode && !drawerOpenRef.current && !annotationCardOpenRef.current && openSummaryComposerAt(event.clientX, event.clientY)) {
                 suppressStageClickRef.current = true;
                 window.setTimeout(() => { suppressStageClickRef.current = false; }, 120);
                 return;
               }
              if (!panned) {
                if (selectionPointerDownRef.current && textLayerRef.current) {
                  correctSelectionEndpointAtPointer(event, textLayerRef.current);
                }
                captureSelection();
              }
            }}
            onPointerCancel={handleStagePointerCancel}
            onClick={handleStageClick}
            onKeyUp={captureSelection}
          >
            {paper ? (
              <div className="pdf-page" ref={pdfPageRef} onDoubleClick={handleAnnotationCardDoubleClick} onClick={(event) => {
                if (overlayIsOpen && isPdfBlankTarget(event.target)) {
                  event.preventDefault();
                  event.stopPropagation();
                  if (event.detail >= 2) dismissOpenOverlay();
                  return;
                }
                if (!stickyPlacementMode || isTextLayerContentTarget(event.target) || (event.target instanceof HTMLElement && event.target.closest(".annotation-mark, .sticky-note-marker"))) return;
                if (openStickyComposerAt(event.clientX, event.clientY)) event.stopPropagation();
              }}>
                <canvas ref={canvasRef} aria-label={`论文第 ${pageNumber} 页`} />
                <div className="selection-preview-layer" aria-hidden="true">
                  {selectionPreviewQuads.map((quad, index) => (
                    <span
                      className="selection-preview-quad"
                      key={`selection-preview-${index}`}
                      style={{ left: `${quad.x * 100}%`, top: `${quad.y * 100}%`, width: `${quad.width * 100}%`, height: `${quad.height * 100}%` }}
                    />
                  ))}
                </div>
                <div className="annotation-layer" aria-label="页面标注">
                  {pageAnnotations.flatMap((annotation) => {
                    const visualQuads = annotation.author === "user" && annotation.mark_type === "highlight"
                      ? mergeHighlightQuads(annotation.normalized_quads)
                      : annotation.normalized_quads;
                    return visualQuads.map((quad, index) => (
                    <button
                      className={`annotation-mark annotation-${annotation.author}${annotation.author === "user" ? ` annotation-user-style-${annotation.style_key ?? "primary"} annotation-user-mark-${annotation.mark_type ?? "underline"}` : ""}${annotation.remember ? " annotation-remember" : ""}${quad.width < 0.03 ? " annotation-short" : ""}${activeAnnotationId === annotation.annotation_id ? " is-focused" : ""}`}
                      key={`${annotation.annotation_id}-${index}`}
                      type="button"
                      data-annotation-id={annotation.annotation_id}
                      style={{ left: quad.width < 0.03 ? `calc(${quad.x * 100}% - 10px)` : `${quad.x * 100}%`, top: `${quad.y * 100}%`, width: quad.width < 0.03 ? `calc(${quad.width * 100}% + 20px)` : `${quad.width * 100}%`, height: `${quad.height * 100}%`, transform: "none" }}
                      aria-label={`${annotation.author === "assistant" ? "AI" : "用户"}标注：${annotation.exact_text}`}
                      ref={(node) => { if (node) markRefs.current.set(annotation.annotation_id, node); else markRefs.current.delete(annotation.annotation_id); }}
                      onClick={() => focusAnnotation(annotation.annotation_id)}
                    >
                      {index === visualQuads.length - 1 && <span className="annotation-entry-dot" aria-hidden="true" />}
                    </button>
                    ));
                  })}
                </div>
                <div className="sticky-layer" aria-label="页面便签">
                  {stickyNotes.filter((note) => note.page === pageNumber).map((note) => <button
                    className={`sticky-note-marker sticky-style-${note.style_key}${draggingStickyId === note.id ? " is-dragging" : ""}${annotationIndexFocus === note.id ? " is-index-focused" : ""}`}
                    key={note.id}
                    type="button"
                    aria-label={`便签：${note.text}`}
                    title={note.text}
                    style={{ left: `${note.x * 100}%`, top: `${note.y * 100}%` }}
                    onPointerDown={(event) => beginStickyDrag(event, note)}
                    onPointerMove={(event) => moveStickyDrag(event, note.id)}
                    onPointerUp={(event) => { void finishStickyDrag(event, note.id, false); }}
                    onPointerCancel={(event) => { void finishStickyDrag(event, note.id, true); }}
                    onClick={(event) => {
                      event.stopPropagation();
                      if (stickyClickSuppressedRef.current === note.id) {
                        stickyClickSuppressedRef.current = null;
                        event.preventDefault();
                        return;
                      }
                      editStickyNote(note);
                    }}
                  ><span aria-hidden="true" /></button>)}
                </div>
                 <div className="summary-note-layer" aria-label="页面大意">
                   {summaryNotes.filter((note) => note.page_number === pageNumber).map((note) => <button
                     className={`summary-note-marker${annotationIndexFocus === note.summary_note_id ? " is-index-focused" : ""}`}
                     key={note.summary_note_id}
                     type="button"
                     aria-label={`大意：${note.text}`}
                     title={note.text}
                     style={{ top: `${note.normalized_y * 100}%` }}
                     ref={(node) => { if (node) summaryMarkerRefs.current.set(note.summary_note_id, node); else summaryMarkerRefs.current.delete(note.summary_note_id); }}
                     onClick={(event) => { event.stopPropagation(); editSummaryNote(note); }}
                   >
                     <span className="summary-note-label">大意</span>
                     <span className="summary-note-preview">{note.text}</span>
                   </button>)}
                 </div>
                 <div className="text-layer" ref={textLayerRef} aria-label="可选择文字层" />
              </div>
            ) : (
              <div className="reader-empty">
                <span>PDF</span>
                <p>上传一篇可选择文字的论文，阅读状态会在本地保存。</p>
              </div>
            )}
          </div>
          {renderAnnotationCard(false)}
          {renderAnnotationCard(true)}
          {showReadingQuestionPanel && readingTask && <ReadingTaskPanel detail={readingTask} onClose={() => setReadingPanelOpen(false)} onMessage={setMessage} />}
          {showAnnotationPanel && <aside className="annotation-index side-panel" aria-label="批注索引">
            <div className="annotation-index-heading side-panel-heading">
              <strong>批注索引</strong>
               <button type="button" className="side-panel-close" aria-label="关闭批注面板" onClick={() => READING_MODE ? setReadingPanelOpen(false) : updateAnnotationsPanelOpen(false)}>×</button>
            </div>
            <div className="annotation-index-filters" role="group" aria-label="批注筛选">
               {([ ["all", "全部"], ["underline", "划线"], ["highlight", "荧光"], ["sticky", "便签"], ["summary", "大意"], ["note", "有笔记"] ] as const).map(([value, label]) => <button type="button" key={value} className={annotationIndexFilter === value ? "active" : ""} aria-pressed={annotationIndexFilter === value} onClick={() => setAnnotationIndexFilter(value)}>{label}</button>)}
            </div>
            <div className="annotation-index-list">
              {annotationIndexItems.map((item) => <button type="button" key={item.key} className={`annotation-index-item${annotationIndexFocus === item.key ? " is-focused" : ""}`} onClick={() => focusAnnotationIndexItem(item)}>
                <span className="annotation-index-meta">第 {item.page} 页 · {item.type}</span>
                <strong>{item.excerpt || "（无摘录）"}</strong>
                {item.note?.trim() && <span className="annotation-index-note">{item.note}</span>}
              </button>)}
              {annotationIndexItems.length === 0 && <p className="annotation-index-empty">当前筛选下没有批注。</p>}
            </div>
          </aside>}
          <footer className="reader-status" aria-live="polite">
            <span>{message}</span>
            <span className={`sync sync-${syncState}`}>{syncLabel}</span>
          </footer>
       </section>
     </section>
    </main>
  );
}
