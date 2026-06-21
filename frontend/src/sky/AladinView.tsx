/* eslint-disable @typescript-eslint/no-explicit-any -- aladin-lite ships no
   types, so the instance, catalog and overlay handles are typed as `any` at
   this wrapper boundary. */
import {
  forwardRef,
  useEffect,
  useImperativeHandle,
  useRef,
} from "react";
import A from "aladin-lite";
import type { ExposurePlan, Survey, Target } from "../api";
import { fovCorners, fovTopTriangle, type MosaicPanel } from "./fov";
import { NAMED_OBJECTS, objectLabel } from "./skyObjects";

// A named object is drawn only when its angular size is at least this fraction
// of the current field-of-view width — the zoom-aware declutter for the overlay.
const MIN_FOV_FRACTION = 0.02;

// A target-box name label is drawn only when the box is at least this fraction of
// the current field-of-view wide. The same zoom-aware declutter the named-object
// overlay uses, tuned so labels stay legible (boxes shrink to a few px at wide
// FOV, where stacked names would be unreadable) but appear as you zoom in.
const LABEL_MIN_FOV_FRACTION = 0.04;

// Frame-matched label colors: cyan for the per-target FOV boxes, amber for the
// project-draft boxes (mirroring the overlay line colors).
const FOV_LABEL_COLOR = "#00e5ff";
const DRAFT_LABEL_COLOR = "#ffb300";
const LABEL_FONT = "12px sans-serif";
// Gap (screen px) between the box's bottom edge and the text baseline, so the
// rotated name hugs the edge from just outside it rather than overlapping the box.
const LABEL_EDGE_GAP = 3;

/** Width of a box in degrees, from the spread of its corner RA/Decs (declutter). */
function boxWidthDeg(corners: [number, number][]): number {
  if (!corners.length) return 0;
  const ras = corners.map((c) => c[0]);
  const decs = corners.map((c) => c[1]);
  const dRa = Math.max(...ras) - Math.min(...ras);
  const dDec = Math.max(...decs) - Math.min(...decs);
  return Math.max(dRa, dDec);
}

/** Current view FOV width in degrees, defaulting to 60 before the view reports. */
function currentFovDeg(aladin: any): number {
  const f = aladin?.getFov?.();
  const fovDeg = Array.isArray(f) ? f[0] : f;
  return fovDeg && fovDeg > 0 ? fovDeg : 60;
}

/** Per-filter acquisition breakdown for a target's popup (snd): one line per
 * exposure plan — filter and desired/acquired/accepted, with frames still to go. */
function planBreakdownHtml(plans: ExposurePlan[]): string {
  if (!plans.length) return "";
  const lines = plans.map((p) => {
    const pending = Math.max(0, p.desired - p.accepted);
    return (
      `&nbsp;&nbsp;${p.filter_name ?? "?"}: ${p.desired}/${p.acquired}/${p.accepted}` +
      (pending ? ` · ${pending} to go` : " · done")
    );
  });
  return "<br/>filters (desired/acquired/accepted):<br/>" + lines.join("<br/>");
}

export interface SkyFocus {
  ra: number;
  dec: number;
  fov?: number;
  key: number; // bump to re-trigger a goto even for the same coords
}

/** FOV box size in degrees, or null to hide the boxes. */
export interface FovBox {
  widthDeg: number;
  heightDeg: number;
}

/** What AladinView draws for one draft target: panel boxes + an orientation marker. */
export interface TargetRender {
  panels: MosaicPanel[];
  triangle: [number, number][];
  /** Target name, drawn (frame-matched amber) on the bottom edge of the box. */
  name?: string;
}

/** Imperative handle so the controls can read the current view center/zoom. */
export interface AladinHandle {
  /** Current view center as [raDeg, decDeg], or null before init. */
  getCenter: () => [number, number] | null;
  /** Current field of view in degrees (width), or null before init. */
  getFov: () => number | null;
}

/** The four sky corners of a dragged Area-of-Interest rectangle ([ra,dec] each). */
export interface CoverageCorners {
  tl: [number, number];
  tr: [number, number];
  bl: [number, number];
  br: [number, number];
}

/** Sky-interaction mode for the capture layer: move a center, drag an area, or off. */
export type PlaceMode = "move" | "coverage" | null;

interface Props {
  survey?: Survey;
  targets: Target[];
  focus: SkyFocus | null;
  fov: FovBox | null;
  /** One render entry per project-draft target (amber overlay). */
  draft: TargetRender[] | null;
  /** Draw the bundled named-object overlay (extent circles + labels). */
  showNamedObjects?: boolean;
  /** Non-null shows a capture layer: 'move' = click/drag a center, 'coverage' = drag an area. */
  placeMode?: PlaceMode;
  onPlaceCenter?: (raDeg: number, decDeg: number) => void;
  onCoverageDrag?: (corners: CoverageCorners) => void;
  onTargetClick?: (id: number) => void;
}

/**
 * Mounts an Aladin Lite v3 instance and keeps it in sync with React props.
 *
 * Aladin needs a container with a concrete, non-zero size when A.aladin() runs —
 * with height:100% the box can still measure 0 at the microtask A.init resolves,
 * which leaves a blank canvas with the reticle pinned near the top. So we gate
 * initialization on a ResizeObserver until the host actually has a size. The
 * design is also React.StrictMode-safe (no double instance, per-effect dispose).
 */
function AladinView(
  {
    survey,
    targets,
    focus,
    fov,
    draft,
    showNamedObjects,
    placeMode,
    onPlaceCenter,
    onCoverageDrag,
    onTargetClick,
  }: Props,
  ref: React.Ref<AladinHandle>,
) {
  const divRef = useRef<HTMLDivElement>(null);
  const aladinRef = useRef<any>(null);
  const catalogRef = useRef<any>(null);
  const fovOverlayRef = useRef<any>(null);
  const draftOverlayRef = useRef<any>(null);
  // Sibling canvas, stacked over Aladin's own canvases, on which the target-frame
  // name labels are drawn rotated to match each box's bottom edge (see drawLabels).
  const labelCanvasRef = useRef<HTMLCanvasElement>(null);
  // rAF handle that coalesces label redraws triggered by pan/zoom/resize so we
  // repaint at most once per frame and the labels track the boxes without lag.
  const labelRafRef = useRef<number>(0);
  const coverageOverlayRef = useRef<any>(null);
  const namedCircleRef = useRef<any>(null);
  const namedLabelRef = useRef<any>(null);
  const darkCircleRef = useRef<any>(null);
  const darkLabelRef = useRef<any>(null);
  const namedZoomTimerRef = useRef<number>(0);
  const draggingRef = useRef(false);
  const dragStartRef = useRef<{ x: number; y: number } | null>(null);

  // Latest props, readable from inside the async init closure.
  const onClickRef = useRef(onTargetClick);
  onClickRef.current = onTargetClick;
  const surveyRef = useRef(survey);
  surveyRef.current = survey;
  const targetsRef = useRef(targets);
  targetsRef.current = targets;
  const fovRef = useRef(fov);
  fovRef.current = fov;
  const draftRef = useRef(draft);
  draftRef.current = draft;
  const showNamedRef = useRef(showNamedObjects);
  showNamedRef.current = showNamedObjects;
  const onPlaceRef = useRef(onPlaceCenter);
  onPlaceRef.current = onPlaceCenter;
  const onCoverageRef = useRef(onCoverageDrag);
  onCoverageRef.current = onCoverageDrag;
  const placeModeRef = useRef(placeMode);
  placeModeRef.current = placeMode;

  useImperativeHandle(ref, () => ({
    getCenter: () => {
      const c = aladinRef.current?.getRaDec?.();
      return c ? [c[0], c[1]] : null;
    },
    getFov: () => {
      const f = aladinRef.current?.getFov?.();
      return f ? (Array.isArray(f) ? f[0] : f) : null;
    },
  }));

  useEffect(() => {
    const host = divRef.current;
    if (!host) return;

    let disposed = false;
    let ro: ResizeObserver | null = null;

    const createAladin = () => {
      if (disposed || aladinRef.current) return;
      const aladin = A.aladin(host, {
        // CORS-friendly default (the registry id "CDS/P/DSS2/color" can resolve
        // to a non-CORS IRSA mirror). Replaced once /api/surveys resolves.
        survey:
          surveyRef.current?.url_or_id ??
          "https://alasky.cds.unistra.fr/DSS/DSSColor",
        fov: 60,
        projection: "SIN",
        cooFrame: "ICRS",
        showCooGrid: false,
        showSimbadPointerControl: true,
        showContextMenu: true,
      });
      aladinRef.current = aladin;
      // Dev convenience: expose the instance for debugging in the console.
      (window as unknown as { aladin?: unknown }).aladin = aladin;

      const cat = A.catalog({
        name: "Targets",
        sourceSize: 16,
        color: "#ffb000",
        shape: "circle",
        onClick: "showPopup",
      });
      aladin.addCatalog(cat);
      catalogRef.current = cat;

      const fovOverlay = A.graphicOverlay({ color: "#00e5ff", lineWidth: 1.5 });
      aladin.addOverlay(fovOverlay);
      fovOverlayRef.current = fovOverlay;

      // The cyan FOV-box and amber draft-box NAME labels are no longer Aladin
      // catalog source labels (those are always screen-upright and overlapped the
      // frames). They are drawn on the sibling label canvas instead — rotated to
      // match each box's bottom edge — by drawLabels()/scheduleLabelRedraw().

      // Project-draft overlay, amber so it reads distinctly from the cyan
      // per-target FOV boxes that can be shown at the same time.
      const draftOverlay = A.graphicOverlay({
        color: "#ffb300",
        lineWidth: 2,
      });
      aladin.addOverlay(draftOverlay);
      draftOverlayRef.current = draftOverlay;

      // Coverage Area-of-Interest preview (the raw dragged rectangle), drawn
      // white/dashed during a coverage drag so it reads apart from the amber grid.
      const coverageOverlay = A.graphicOverlay({
        color: "#ffffff",
        lineWidth: 1,
        lineDash: [5, 4],
      });
      aladin.addOverlay(coverageOverlay);
      coverageOverlayRef.current = coverageOverlay;

      // Named-object overlay: a green extent ring per well-known object, plus a
      // companion catalog that carries the labels (the catalog draws the text,
      // the overlay draws the circles). Hidden until the topbar toggle is on.
      const namedCircles = A.graphicOverlay({ color: "#7dffb0", lineWidth: 1 });
      aladin.addOverlay(namedCircles);
      namedCircleRef.current = namedCircles;

      // Purely decorative annotations: the named-object labels must not respond to
      // hover (no highlight) and must not be clickable/selectable. Aladin has no
      // per-catalog "disable interaction" flag (every catalog source is hit-tested
      // for hover/click), so we follow the same non-interactive label recipe the
      // FOV-box and draft-box label catalogs use: no `onClick` (so a click runs no
      // popup/select action) and a minimal source marker (no hover/selection
      // highlight, matching the extent-ring decorative layer). The text label and
      // its color are unchanged — only the marker's interactivity is removed.
      // NOTE: sourceSize must be >= 2. Aladin caches a circle marker as
      // ctx.arc(C/2, C/2, C/2 - 1, ...) where C = sourceSize, so sourceSize 1 gives
      // a negative radius and throws DOMException inside the draw loop (freezing the
      // whole view). sourceSize 2 -> radius 0 keeps the marker invisible safely.
      const namedLabels = A.catalog({
        name: "Named objects",
        sourceSize: 2,
        color: "#7dffb0",
        shape: "circle",
        displayLabel: true,
        labelColumn: "label",
        labelColor: "#9affc7",
        labelFont: "13px sans-serif",
      });
      aladin.addCatalog(namedLabels);
      namedLabelRef.current = namedLabels;

      // Dark nebulae (Barnard / LDN) get their own dusty-orange layer so the
      // absorption silhouettes read distinctly from the green emission/galaxy
      // rings — they sit along the disk and often overlap bright objects.
      const darkCircles = A.graphicOverlay({ color: "#d9974f", lineWidth: 1 });
      aladin.addOverlay(darkCircles);
      darkCircleRef.current = darkCircles;

      const darkLabels = A.catalog({
        name: "Dark nebulae",
        sourceSize: 5,
        color: "#d9974f",
        shape: "circle",
        displayLabel: true,
        labelColumn: "label",
        labelColor: "#e7b277",
        labelFont: "13px sans-serif",
        onClick: "showPopup",
      });
      aladin.addCatalog(darkLabels);
      darkLabelRef.current = darkLabels;

      // Aladin fires objectClicked(source) on a marker and objectClicked(null)
      // on empty sky. Recenter on a marker; close the popup on empty sky (so the
      // user can dismiss it by clicking anywhere, not just the small X).
      // The marker popup is the catalog's own popup, which aladin.hidePopup()
      // (the view popup) does not close. Its close "×" handler does work, so we
      // dismiss by triggering that button — covers clicking empty sky or a box.
      const closePopup = () =>
        document
          .querySelectorAll<HTMLElement>(".aladin-closeBtn")
          .forEach((b) => b.click());

      aladin.on("objectClicked", (obj: any) => {
        const id = obj?.data?.id;
        if (id != null) onClickRef.current?.(Number(id));
        else closePopup();
      });
      // Clicking a FOV box (a footprint) rather than empty sky also dismisses it.
      aladin.on("footprintClicked", () => closePopup());

      // Re-cull the named-object overlay when the zoom changes, so only objects
      // large enough on-screen are drawn (debounced past the zoom animation).
      // NOTE: aladin.on() keeps only ONE handler per event name, so the target-box
      // label redraw must live inside this single zoomChanged handler.
      aladin.on("zoomChanged", () => {
        // The target-frame name labels follow the view: their bottom-edge angle,
        // screen position and which boxes are big enough to label all change with
        // zoom, so repaint the label canvas (coalesced to one frame).
        scheduleLabelRedraw();
        if (!showNamedRef.current) return;
        window.clearTimeout(namedZoomTimerRef.current);
        namedZoomTimerRef.current = window.setTimeout(
          () => syncNamed(showNamedRef.current),
          120,
        );
      });
      // Labels must track panning and host resizes too. These run every frame of a
      // drag; scheduleLabelRedraw() coalesces them to one repaint per animation frame.
      aladin.on("positionChanged", () => scheduleLabelRedraw());
      aladin.on("resizeChanged", () => scheduleLabelRedraw());

      syncCatalog(targetsRef.current);
      syncFov(targetsRef.current, fovRef.current);
      syncDraft(draftRef.current);
      syncNamed(showNamedRef.current);
    };

    // Initialize only once the container has a concrete, non-zero size.
    const tryInit = () => {
      if (disposed || aladinRef.current) return;
      // Require a real height (not a transient 1px) before locking in the canvas.
      if (host.clientWidth > 0 && host.clientHeight > 2) {
        createAladin();
        ro?.disconnect();
        ro = null;
      }
    };

    A.init.then(() => {
      if (disposed) return;
      ro = new ResizeObserver(tryInit);
      ro.observe(host);
      tryInit(); // already sized? go now.
    });

    return () => {
      disposed = true;
      ro?.disconnect();
      if (labelRafRef.current) {
        window.cancelAnimationFrame(labelRafRef.current);
        labelRafRef.current = 0;
      }
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function syncCatalog(items: Target[]) {
    const cat = catalogRef.current;
    if (!cat) return;
    cat.removeAll();
    cat.addSources(
      items.map((t) =>
        A.marker(t.ra_deg, t.dec_deg, {
          popupTitle: t.name,
          popupDesc:
            `${t.project_name} · ${t.active ? "active" : "inactive"}<br/>` +
            `RA ${t.ra_deg.toFixed(4)}°, Dec ${t.dec_deg.toFixed(4)}°<br/>` +
            `rotation ${t.rotation.toFixed(1)}°` +
            planBreakdownHtml(t.exposure_plans),
          id: t.id,
        }),
      ),
    );
  }

  function syncFov(items: Target[], box: FovBox | null) {
    const ov = fovOverlayRef.current;
    if (!ov) return;
    ov.removeAll();
    if (box) {
      for (const t of items) {
        ov.add(
          A.polygon(
            fovCorners(
              t.ra_deg,
              t.dec_deg,
              box.widthDeg,
              box.heightDeg,
              t.rotation,
            ),
          ),
        );
        ov.add(
          A.polygon(
            fovTopTriangle(
              t.ra_deg,
              t.dec_deg,
              box.widthDeg,
              box.heightDeg,
              t.rotation,
            ),
          ),
        );
      }
    }
    // removeAll()/add() don't repaint until the view changes; force it so the
    // boxes appear/disappear immediately when toggled.
    aladinRef.current?.view?.requestRedraw?.();
    scheduleLabelRedraw();
  }

  function syncDraft(targets: TargetRender[] | null) {
    const ov = draftOverlayRef.current;
    if (!ov) return;
    ov.removeAll();
    if (targets) {
      for (const t of targets) {
        for (const p of t.panels) ov.add(A.polygon(p.corners));
        if (t.panels.length) ov.add(A.polygon(t.triangle)); // orientation marker
      }
    }
    aladinRef.current?.view?.requestRedraw?.();
    scheduleLabelRedraw();
  }

  // ----- Rotated target-frame name labels (sibling canvas) -------------------
  //
  // Aladin catalog source labels are always screen-upright and frequently
  // overlapped the FOV/draft frames. Instead we draw each target NAME on a
  // dedicated overlay canvas, rotated to lie along its box's bottom edge (just
  // outside it). The overlay is fully decoupled from Aladin's own draw loop, so
  // nothing here can stall or crash Aladin's repaint (the ex9 negative-radius
  // class of bug lived inside Aladin's catalog draw; this code never touches it).

  /** Project a sky point to host-relative CSS pixels, or null if off-screen. */
  function worldToHostXY(ra: number, dec: number): { x: number; y: number } | null {
    const p = aladinRef.current?.world2pix?.(ra, dec);
    if (!p || !Number.isFinite(p[0]) || !Number.isFinite(p[1])) return null;
    return { x: p[0], y: p[1] };
  }

  /**
   * Draw one frame name centered along the bottom edge defined by its two
   * bottom corners (sky [ra,dec]), rotated to match the edge and hugging it from
   * just outside. Returns silently if either corner projects off-screen.
   */
  function drawEdgeLabel(
    ctx: CanvasRenderingContext2D,
    name: string,
    leftCorner: [number, number], // bottom edge corner at -w (fovCorners idx 2)
    rightCorner: [number, number], // bottom edge corner at +w (fovCorners idx 3)
    color: string,
  ) {
    const a = worldToHostXY(leftCorner[0], leftCorner[1]);
    const b = worldToHostXY(rightCorner[0], rightCorner[1]);
    if (!a || !b) return;
    const midX = (a.x + b.x) / 2;
    const midY = (a.y + b.y) / 2;
    // Screen angle of the bottom edge, left corner -> right corner.
    let angle = Math.atan2(b.y - a.y, b.x - a.x);
    // The box is "below" the bottom edge in screen space. With +y pointing down,
    // the outward normal (pointing away from the box, where the text should sit)
    // is the edge direction rotated by -90°. We place the text on that side by
    // offsetting along that normal. Keep text upright-readable: if the edge angle
    // would render the baseline upside-down (|angle| > 90°), flip by π and put the
    // text on the opposite side so the baseline still hugs the outside of the box.
    let offset = -LABEL_EDGE_GAP; // textBaseline 'bottom': baseline above the edge line
    if (angle > Math.PI / 2 || angle < -Math.PI / 2) {
      angle += Math.PI;
      offset = LABEL_EDGE_GAP; // flipped: keep the text on the box's outside
    }
    ctx.save();
    ctx.translate(midX, midY);
    ctx.rotate(angle);
    ctx.font = LABEL_FONT;
    ctx.fillStyle = color;
    ctx.textAlign = "center";
    // 'bottom'/'top' chosen so positive `offset` sits the text on the outside of
    // the frame edge regardless of the flip above.
    ctx.textBaseline = offset < 0 ? "bottom" : "top";
    ctx.fillText(name, 0, offset);
    ctx.restore();
  }

  /**
   * Repaint the whole label canvas: size it to the host (devicePixelRatio-aware),
   * then draw each visible FOV-box and draft-box name along its bottom edge.
   * Declutter (LABEL_MIN_FOV_FRACTION) and frame colors are preserved.
   */
  function drawLabels() {
    const canvas = labelCanvasRef.current;
    const host = divRef.current;
    const aladin = aladinRef.current;
    if (!canvas || !host || !aladin) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    // Match the canvas backing store to the host's CSS size at the device pixel
    // ratio, so text is crisp and 1 canvas unit == 1 CSS px after the scale.
    const dpr = window.devicePixelRatio || 1;
    const cssW = host.clientWidth;
    const cssH = host.clientHeight;
    const pxW = Math.max(1, Math.round(cssW * dpr));
    const pxH = Math.max(1, Math.round(cssH * dpr));
    if (canvas.width !== pxW) canvas.width = pxW;
    if (canvas.height !== pxH) canvas.height = pxH;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, cssW, cssH);

    const minWidthDeg = currentFovDeg(aladin) * LABEL_MIN_FOV_FRACTION;

    // Per-target FOV boxes (cyan). Bottom edge = corners 2 (-w,-h) and 3 (+w,-h).
    const box = fovRef.current;
    if (box) {
      for (const t of targetsRef.current) {
        if (!t.name) continue;
        const corners = fovCorners(
          t.ra_deg,
          t.dec_deg,
          box.widthDeg,
          box.heightDeg,
          t.rotation,
        );
        if (corners.length < 4 || boxWidthDeg(corners) < minWidthDeg) continue;
        drawEdgeLabel(ctx, t.name, corners[2], corners[3], FOV_LABEL_COLOR);
      }
    }

    // Project-draft boxes (amber). A draft target can be a mosaic of panels, so
    // pick the overall bottom edge: the two lowest-Dec among every panel's bottom
    // corners (each panel's corners 2/3), matching the per-box bottom-edge convention.
    for (const t of draftRef.current ?? []) {
      if (!t.name || !t.panels.length) continue;
      const allCorners = t.panels.flatMap((p) => p.corners);
      if (boxWidthDeg(allCorners) < minWidthDeg) continue;
      const bottoms = t.panels
        .flatMap((p) => [p.corners[2], p.corners[3]])
        .sort((c1, c2) => c1[1] - c2[1]);
      const left = bottoms[0];
      const right = bottoms[1] ?? bottoms[0];
      // Order the pair left->right in screen space so the edge angle is consistent.
      drawEdgeLabel(ctx, t.name, left, right, DRAFT_LABEL_COLOR);
    }
  }

  /** Coalesce label repaints to one per animation frame (pan fires per-frame). */
  function scheduleLabelRedraw() {
    if (labelRafRef.current) return;
    labelRafRef.current = window.requestAnimationFrame(() => {
      labelRafRef.current = 0;
      try {
        drawLabels();
      } catch {
        // A projection/draw hiccup must never wedge the overlay loop; the next
        // pan/zoom will reschedule a clean repaint.
      }
    });
  }

  // Named-object overlay: a circle sized to each object's angular extent plus a
  // label catalog. Zoom-aware culling keeps the view readable across ~420
  // objects — an object is only drawn when its angular size is at least
  // MIN_FOV_FRACTION of the current field of view, so a wide field shows only
  // the giants (M31, the Veil, big Sharpless complexes) and zooming in reveals
  // progressively smaller ones. Both layers are cleared when `show` is off.
  function syncNamed(show: boolean | undefined) {
    const circles = namedCircleRef.current;
    const labels = namedLabelRef.current;
    const darkCircles = darkCircleRef.current;
    const darkLabels = darkLabelRef.current;
    if (!circles || !labels || !darkCircles || !darkLabels) return;
    circles.removeAll();
    labels.removeAll();
    darkCircles.removeAll();
    darkLabels.removeAll();
    if (show) {
      const f = aladinRef.current?.getFov?.();
      const fovDeg = Array.isArray(f) ? f[0] : f;
      const minSizeDeg = (fovDeg && fovDeg > 0 ? fovDeg : 60) * MIN_FOV_FRACTION;
      const visible = NAMED_OBJECTS.filter(
        (o) => o.sizeArcmin / 60 >= minSizeDeg,
      );
      // Dark nebulae render on their own dusty-orange layer; everything else on
      // the green layer.
      for (const o of visible) {
        const isDark = o.kind === "dark";
        const circ = isDark ? darkCircles : circles;
        circ.add(A.circle(o.ra, o.dec, o.sizeArcmin / 2 / 60));
        if (isDark) {
          darkLabels.addSources([
            A.source(o.ra, o.dec, {
              label: objectLabel(o),
              popupTitle: objectLabel(o),
              popupDesc:
                `${o.kind} · ${o.catalog} · size ${o.sizeArcmin}'<br/>` +
                `RA ${o.ra.toFixed(4)}°, Dec ${o.dec.toFixed(4)}°`,
            }),
          ]);
        } else {
          // Named objects are non-interactive labels: the source carries only the
          // label text (no popup data), so a click opens nothing — matching the
          // FOV-box / draft-box decorative label catalogs.
          labels.addSources([A.source(o.ra, o.dec, { label: objectLabel(o) })]);
        }
      }
    }
    aladinRef.current?.view?.requestRedraw?.();
  }

  // Host-relative pixel of a pointer event, or null if the host is gone.
  function hostXY(e: React.PointerEvent): { x: number; y: number } | null {
    const host = divRef.current;
    if (!host) return null;
    const rect = host.getBoundingClientRect();
    return { x: e.clientX - rect.left, y: e.clientY - rect.top };
  }

  function unproject(x: number, y: number): [number, number] | null {
    const w = aladinRef.current?.pix2world?.(x, y);
    return w && Number.isFinite(w[0]) && Number.isFinite(w[1])
      ? [w[0], w[1]]
      : null;
  }

  // 'move' mode: report the pointer's sky position as the new target center.
  function placeFromEvent(e: React.PointerEvent) {
    const p = hostXY(e);
    if (!p) return;
    const world = unproject(p.x, p.y);
    if (world) onPlaceRef.current?.(world[0], world[1]);
  }

  // 'coverage' mode: map the screen bounding box of the drag to four sky corners,
  // preview it as a dashed rectangle, and report it for the auto-divide.
  function coverageFromEvent(e: React.PointerEvent) {
    const p = hostXY(e);
    const start = dragStartRef.current;
    if (!p || !start) return;
    const minX = Math.min(start.x, p.x);
    const maxX = Math.max(start.x, p.x);
    const minY = Math.min(start.y, p.y);
    const maxY = Math.max(start.y, p.y);
    const tl = unproject(minX, minY);
    const tr = unproject(maxX, minY);
    const bl = unproject(minX, maxY);
    const br = unproject(maxX, maxY);
    if (!tl || !tr || !bl || !br) return;
    const ov = coverageOverlayRef.current;
    if (ov) {
      ov.removeAll();
      ov.add(A.polygon([tl, tr, br, bl]));
      aladinRef.current?.view?.requestRedraw?.();
    }
    onCoverageRef.current?.({ tl, tr, bl, br });
  }

  function clearCoveragePreview() {
    const ov = coverageOverlayRef.current;
    if (ov) {
      ov.removeAll();
      aladinRef.current?.view?.requestRedraw?.();
    }
  }

  function onCapDown(e: React.PointerEvent) {
    draggingRef.current = true;
    (e.target as HTMLElement).setPointerCapture?.(e.pointerId);
    if (placeModeRef.current === "coverage") {
      dragStartRef.current = hostXY(e);
    } else {
      placeFromEvent(e);
    }
  }
  function onCapMove(e: React.PointerEvent) {
    if (!draggingRef.current) return;
    if (placeModeRef.current === "coverage") coverageFromEvent(e);
    else placeFromEvent(e);
  }
  function onCapUp(e: React.PointerEvent) {
    draggingRef.current = false;
    (e.target as HTMLElement).releasePointerCapture?.(e.pointerId);
    if (placeModeRef.current === "coverage") {
      dragStartRef.current = null;
      clearCoveragePreview();
    }
  }

  // Survey changes.
  useEffect(() => {
    if (aladinRef.current && survey) {
      aladinRef.current.setImageSurvey(survey.url_or_id);
    }
  }, [survey]);

  // Target set changes.
  useEffect(() => {
    if (aladinRef.current) {
      syncCatalog(targets);
      syncFov(targets, fov);
    }
  }, [targets]);

  // FOV box changes.
  useEffect(() => {
    if (aladinRef.current) syncFov(targets, fov);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [fov]);

  // Project-draft changes.
  useEffect(() => {
    if (aladinRef.current) syncDraft(draft);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [draft]);

  // Named-object overlay toggled.
  useEffect(() => {
    if (aladinRef.current) syncNamed(showNamedObjects);
  }, [showNamedObjects]);

  // Imperative focus (click-to-center).
  useEffect(() => {
    if (aladinRef.current && focus) {
      if (focus.fov) aladinRef.current.setFov(focus.fov);
      aladinRef.current.gotoRaDec(focus.ra, focus.dec);
    }
  }, [focus]);

  return (
    <div className="aladin-wrap">
      <div ref={divRef} className="aladin-host" />
      {/* Rotated target-frame name labels, drawn over Aladin's canvases. Sits
          below the placement capture layer (z-index 5) and ignores pointers. */}
      <canvas ref={labelCanvasRef} className="aladin-label-overlay" />
      {placeMode && (
        <div
          className={
            placeMode === "coverage"
              ? "mosaic-capture coverage"
              : "mosaic-capture"
          }
          onPointerDown={onCapDown}
          onPointerMove={onCapMove}
          onPointerUp={onCapUp}
          onPointerCancel={onCapUp}
        />
      )}
    </div>
  );
}

export default forwardRef(AladinView);
