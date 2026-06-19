import { useEffect, useRef } from "react";
import A from "aladin-lite";
import type { Survey, Target } from "../api";
import { fovCorners, fovTopTriangle } from "./fov";

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

interface Props {
  survey?: Survey;
  targets: Target[];
  focus: SkyFocus | null;
  fov: FovBox | null;
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
export default function AladinView({
  survey,
  targets,
  focus,
  fov,
  onTargetClick,
}: Props) {
  const divRef = useRef<HTMLDivElement>(null);
  const aladinRef = useRef<any>(null);
  const catalogRef = useRef<any>(null);
  const fovOverlayRef = useRef<any>(null);

  // Latest props, readable from inside the async init closure.
  const onClickRef = useRef(onTargetClick);
  onClickRef.current = onTargetClick;
  const surveyRef = useRef(survey);
  surveyRef.current = survey;
  const targetsRef = useRef(targets);
  targetsRef.current = targets;
  const fovRef = useRef(fov);
  fovRef.current = fov;

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

  // Imperative focus (click-to-center).
  useEffect(() => {
    if (aladinRef.current && focus) {
      if (focus.fov) aladinRef.current.setFov(focus.fov);
      aladinRef.current.gotoRaDec(focus.ra, focus.dec);
    }
  }, [focus]);

  return <div ref={divRef} className="aladin-host" />;
}
