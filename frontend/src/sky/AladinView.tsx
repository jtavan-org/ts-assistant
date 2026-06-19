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
import type { Survey, Target } from "../api";
import { fovCorners, fovTopTriangle, type MosaicPanel } from "./fov";

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
  const coverageOverlayRef = useRef<any>(null);
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

      syncCatalog(targetsRef.current);
      syncFov(targetsRef.current, fovRef.current);
      syncDraft(draftRef.current);
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
            (t.exposure_plans.length
              ? `<br/>filters: ${t.exposure_plans
                  .map((p) => p.filter_name ?? "?")
                  .join(", ")}`
              : ""),
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
