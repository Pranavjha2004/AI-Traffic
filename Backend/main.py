"""
AI Traffic Management System backend with live stream ingestion.

Features:
- YOLOv8 vehicle detection
- ambulance heuristic detection
- congestion scoring
- adaptive signal recommendations
- optional manual observed-direction override for single-camera prototypes
- ESP32 MJPEG stream ingestion and background analysis
"""

from __future__ import annotations

import base64
import json
import logging
import threading
import time
from collections import Counter, deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.parse import urlparse, urlunparse
from urllib.request import urlopen

import cv2
import numpy as np
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field
from ultralytics import YOLO

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent
MODEL_PATH = BASE_DIR / "yolov8s.pt"
DIRECTIONS = {"north", "east", "south", "west"}

app = FastAPI(title="AI Traffic Management", version="4.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"https?://(localhost|127\.0\.0\.1)(:\d+)?",
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

model: YOLO | None = None
frame_counter = 0
recent_frames: deque[dict[str, Any]] = deque(maxlen=50)

VEHICLE_CLASSES = {
    1: "bicycle",
    2: "car",
    3: "motorcycle",
    5: "bus",
    7: "truck",
}
AMBULANCE_CANDIDATE_CLASSES = {5, 7}
VEHICLE_WEIGHTS = {
    "bicycle": 0.5,
    "motorcycle": 0.8,
    "car": 1.0,
    "truck": 2.2,
    "bus": 2.6,
    "AMBULANCE": 2.4,
}


class StreamConfig(BaseModel):
    stream_url: str = Field(..., min_length=1)
    analyze_fps: float = Field(default=2.0, ge=0.2, le=10.0)


class DirectionSelection(BaseModel):
    direction: str = Field(default="auto")


class FlashControl(BaseModel):
    enabled: bool


stream_state: dict[str, Any] = {
    "active": False,
    "stream_url": "",
    "analyze_fps": 2.0,
    "worker_started_at": None,
    "last_frame_at": None,
    "last_error": "",
    "frames_read": 0,
    "frames_analyzed": 0,
}
direction_state: dict[str, Any] = {
    "active_direction": None,
    "mode": "auto",
    "updated_at": None,
}
camera_state: dict[str, Any] = {
    "flash_enabled": False,
    "last_flash_update_at": None,
    "last_flash_error": "",
}

stream_thread: threading.Thread | None = None
stream_stop_event = threading.Event()
state_lock = threading.Lock()
latest_raw_frame_jpeg: bytes | None = None
latest_raw_frame_at: str | None = None


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def update_stream_state(**kwargs: Any) -> None:
    with state_lock:
        stream_state.update(kwargs)


def get_stream_state() -> dict[str, Any]:
    with state_lock:
        return dict(stream_state)


def set_direction(direction: str | None) -> dict[str, Any]:
    with state_lock:
        direction_state["active_direction"] = direction
        direction_state["mode"] = "manual" if direction else "auto"
        direction_state["updated_at"] = utc_now_iso()
        return dict(direction_state)


def get_direction_state() -> dict[str, Any]:
    with state_lock:
        return dict(direction_state)


def update_camera_state(**kwargs: Any) -> None:
    with state_lock:
        camera_state.update(kwargs)


def get_camera_state() -> dict[str, Any]:
    with state_lock:
        return dict(camera_state)


def set_latest_raw_frame(frame: np.ndarray) -> None:
    global latest_raw_frame_jpeg, latest_raw_frame_at
    success, buffer = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
    if success:
        latest_raw_frame_jpeg = buffer.tobytes()
        latest_raw_frame_at = utc_now_iso()


def camera_base_url() -> str | None:
    stream_url = get_stream_state().get("stream_url", "")
    if not stream_url:
        return None

    parsed = urlparse(stream_url)
    if not parsed.scheme or not parsed.netloc:
        return None

    return urlunparse((parsed.scheme, parsed.netloc, "", "", "", ""))


def call_camera_flash(enabled: bool) -> dict[str, Any]:
    base_url = camera_base_url()
    if not base_url:
        raise HTTPException(status_code=400, detail="No active camera stream URL available for flash control.")

    target = f"{base_url}/flash?state={'on' if enabled else 'off'}"
    try:
        with urlopen(target, timeout=3) as response:
            payload = response.read().decode("utf-8", errors="replace")
    except URLError as exc:
        update_camera_state(last_flash_error=str(exc), last_flash_update_at=utc_now_iso())
        raise HTTPException(status_code=502, detail=f"Failed to reach ESP32-CAM flash endpoint: {exc}") from exc

    flash_enabled = enabled
    try:
        if payload:
            data = json.loads(payload)
            flash_enabled = bool(data.get("flash_enabled", enabled))
    except Exception:
        flash_enabled = enabled

    update_camera_state(
        flash_enabled=flash_enabled,
        last_flash_update_at=utc_now_iso(),
        last_flash_error="",
    )
    return get_camera_state()


def calculate_white_ratio(roi: np.ndarray) -> float:
    if roi is None or roi.size == 0:
        return 0.0
    hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(hsv, (0, 0, 160), (180, 60, 255))
    return cv2.countNonZero(mask) / max(roi.shape[0] * roi.shape[1], 1)


def has_red_cross_hint(roi: np.ndarray) -> bool:
    if roi is None or roi.size == 0:
        return False
    hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
    red_mask1 = cv2.inRange(hsv, (0, 100, 100), (10, 255, 255))
    red_mask2 = cv2.inRange(hsv, (160, 100, 100), (180, 255, 255))
    red_pixels = cv2.countNonZero(cv2.bitwise_or(red_mask1, red_mask2))
    return (red_pixels / max(roi.shape[0] * roi.shape[1], 1)) > 0.01


def is_ambulance_candidate(cls_id: int, box: list[float], image: np.ndarray) -> dict[str, Any]:
    if cls_id not in AMBULANCE_CANDIDATE_CLASSES:
        return {"is_ambulance": False, "ambulance_confidence": 0.0}

    x1, y1, x2, y2 = [int(v) for v in box]
    h, w = image.shape[:2]
    x1, y1 = max(0, x1), max(0, y1)
    x2, y2 = min(w, x2), min(h, y2)
    roi = image[y1:y2, x1:x2]
    if roi.size == 0:
        return {"is_ambulance": False, "ambulance_confidence": 0.0}

    white_ratio = calculate_white_ratio(roi)
    red_cross = has_red_cross_hint(roi)
    score = 0.0
    if white_ratio > 0.55:
        score += 0.6
    elif white_ratio > 0.40:
        score += 0.35
    if red_cross:
        score += 0.3

    aspect = (x2 - x1) / max((y2 - y1), 1)
    if 1.2 < aspect < 3.0:
        score += 0.1

    return {
        "is_ambulance": score >= 0.6,
        "ambulance_confidence": round(min(score, 1.0), 3),
        "white_ratio": round(white_ratio, 3),
        "red_cross_detected": red_cross,
    }


def phase_for_direction(direction: str | None) -> str | None:
    if direction in {"north", "south"}:
        return "ew_green"
    if direction in {"east", "west"}:
        return "ns_green"
    return None


def signal_state_for_phase(phase: str) -> str:
    return "east_west_green" if phase == "ew_green" else "north_south_green"


def build_management_decision(
    vehicle_count: int,
    counts: dict[str, int],
    coverage_ratio: float,
    emergency_detected: bool,
) -> dict[str, Any]:
    weighted_count = sum(VEHICLE_WEIGHTS.get(name, 1.0) * count for name, count in counts.items())
    normalized_count = min(vehicle_count / 18.0, 1.0)
    normalized_weight = min(weighted_count / 24.0, 1.0)
    normalized_coverage = min(coverage_ratio / 0.45, 1.0)
    congestion_score = round(
        ((normalized_count * 0.45) + (normalized_weight * 0.30) + (normalized_coverage * 0.25)) * 100,
        1,
    )

    direction_info = get_direction_state()
    active_direction = direction_info["active_direction"]
    preferred_phase = phase_for_direction(active_direction)

    if emergency_detected:
        density_level = "emergency"
        signal_mode = "emergency_override"
        signal_state = "priority_green"
        preferred_phase = "ns_green"
        recommended_green_sec = 45
        action = "Clear the lane and hold green for the emergency vehicle."
    elif active_direction:
        density_level = "manual_focus"
        signal_mode = "manual_direction_override"
        signal_state = signal_state_for_phase(preferred_phase or "ns_green")
        recommended_green_sec = max(18, min(50, 12 + int(congestion_score * 0.35)))
        action = (
            f"Observed direction is {active_direction}. Keep {active_direction} red and "
            f"give green to the perpendicular road based on observed density."
        )
    elif congestion_score >= 75:
        density_level = "severe"
        signal_mode = "adaptive"
        signal_state = "extended_green"
        preferred_phase = None
        recommended_green_sec = 40
        action = "Extend green time and slow cross traffic release."
    elif congestion_score >= 50:
        density_level = "high"
        signal_mode = "adaptive"
        signal_state = "green_extension"
        preferred_phase = None
        recommended_green_sec = 30
        action = "Give this approach more green time to reduce queue growth."
    elif congestion_score >= 25:
        density_level = "medium"
        signal_mode = "balanced"
        signal_state = "normal_cycle"
        preferred_phase = None
        recommended_green_sec = 22
        action = "Use a balanced cycle with a mild green extension."
    else:
        density_level = "low"
        signal_mode = "balanced"
        signal_state = "normal_cycle"
        preferred_phase = None
        recommended_green_sec = 15
        action = "Traffic is light, keep the standard cycle."

    return {
        "density_level": density_level,
        "coverage_ratio": round(coverage_ratio, 3),
        "weighted_vehicle_load": round(weighted_count, 2),
        "congestion_score": congestion_score,
        "signal_mode": signal_mode,
        "signal_state": signal_state,
        "preferred_phase": preferred_phase,
        "active_direction": active_direction,
        "recommended_green_sec": recommended_green_sec,
        "recommended_red_sec": max(12, 60 - recommended_green_sec),
        "action": action,
    }


def annotate_image(image: np.ndarray, detections: list[dict[str, Any]], management: dict[str, Any]) -> np.ndarray:
    annotated = image.copy()
    _, width = annotated.shape[:2]

    for detection in detections:
        x1, y1, x2, y2 = detection["box"]
        is_emergency = detection.get("is_emergency", False)
        label = detection["class_name"]
        confidence = detection["confidence"]

        color = (0, 0, 230) if is_emergency else (50, 210, 90)
        thickness = 3 if is_emergency else 2
        cv2.rectangle(annotated, (x1, y1), (x2, y2), color, thickness)

        text = f"{label} {confidence:.2f}"
        (text_width, text_height), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, 0.55, 2)
        top = max(y1 - text_height - 8, 0)
        cv2.rectangle(annotated, (x1, top), (x1 + text_width + 6, y1), color, -1)
        cv2.putText(
            annotated,
            text,
            (x1 + 3, max(y1 - 4, text_height + 2)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.55,
            (255, 255, 255),
            2,
        )

    overlay = annotated.copy()
    banner_color = (0, 0, 180) if management["density_level"] == "emergency" else (15, 70, 140)
    cv2.rectangle(overlay, (0, 0), (width, 72), banner_color, -1)
    cv2.addWeighted(overlay, 0.42, annotated, 0.58, 0, annotated)

    line1 = f"Density: {management['density_level'].upper()}  Score: {management['congestion_score']}"
    observed = management.get("active_direction") or "auto"
    line2 = f"Observed: {observed.upper()}  Signal: {management['signal_state']}"
    line3 = f"Green: {management['recommended_green_sec']}s  Red: {management['recommended_red_sec']}s"

    cv2.putText(annotated, line1, (12, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)
    cv2.putText(annotated, line2, (12, 46), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 2)
    cv2.putText(annotated, line3, (12, 68), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 2)
    return annotated


def analyze_image(image: np.ndarray) -> dict[str, Any]:
    global frame_counter

    if model is None:
        raise RuntimeError("Model not loaded.")

    frame_counter += 1
    results = model(image, conf=0.35, iou=0.45, verbose=False)[0]
    boxes = results.boxes
    image_area = max(image.shape[0] * image.shape[1], 1)

    detections: list[dict[str, Any]] = []
    counts: Counter[str] = Counter()
    ambulance_details: list[dict[str, Any]] = []
    emergency_detected = False
    covered_area = 0.0

    for index in range(len(boxes)):
        cls_id = int(boxes.cls[index].item())
        confidence = float(boxes.conf[index].item())
        xyxy = [round(v) for v in boxes.xyxy[index].tolist()]
        if cls_id not in VEHICLE_CLASSES:
            continue

        base_label = VEHICLE_CLASSES[cls_id]
        ambulance_result = is_ambulance_candidate(cls_id, xyxy, image)
        is_emergency = ambulance_result.get("is_ambulance", False)
        label = "AMBULANCE" if is_emergency else base_label

        if is_emergency:
            emergency_detected = True
            ambulance_details.append({"box": xyxy, **ambulance_result})

        x1, y1, x2, y2 = xyxy
        covered_area += max((x2 - x1), 0) * max((y2 - y1), 0)
        counts[label] += 1
        detections.append(
            {
                "class_id": cls_id,
                "class_name": label,
                "confidence": round(confidence, 3),
                "box": xyxy,
                "is_emergency": is_emergency,
            }
        )

    management = build_management_decision(
        vehicle_count=len(detections),
        counts=dict(counts),
        coverage_ratio=covered_area / image_area,
        emergency_detected=emergency_detected,
    )

    annotated = annotate_image(image, detections, management)
    success, buffer = cv2.imencode(".jpg", annotated, [cv2.IMWRITE_JPEG_QUALITY, 85])
    if not success:
        raise RuntimeError("Failed to encode annotated image.")

    return {
        "frame_id": frame_counter,
        "timestamp": utc_now_iso(),
        "vehicle_count": len(detections),
        "category_counts": dict(counts),
        "detections": detections,
        "emergency_detected": emergency_detected,
        "ambulance_details": ambulance_details,
        "management": management,
        "annotated_image": base64.b64encode(buffer.tobytes()).decode("utf-8"),
    }


def analyze_image_bytes(image_bytes: bytes) -> dict[str, Any]:
    image = cv2.imdecode(np.frombuffer(image_bytes, np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError("Cannot decode image.")
    return analyze_image(image)


def persist_result(result: dict[str, Any], inference_ms: float) -> dict[str, Any]:
    result["inference_ms"] = round(inference_ms, 1)
    recent_frames.appendleft(result)
    log.info(
        "Frame %s | vehicles=%s | density=%s | mode=%s | direction=%s | %.1fms",
        result["frame_id"],
        result["vehicle_count"],
        result["management"]["density_level"],
        result["management"]["signal_mode"],
        result["management"].get("active_direction"),
        result["inference_ms"],
    )
    return result


def stream_worker(stream_url: str, analyze_fps: float) -> None:
    update_stream_state(
        active=True,
        stream_url=stream_url,
        analyze_fps=analyze_fps,
        worker_started_at=utc_now_iso(),
        last_error="",
    )
    capture = cv2.VideoCapture(stream_url)
    if not capture.isOpened():
        update_stream_state(active=False, last_error=f"Could not open stream: {stream_url}")
        return

    min_interval = 1.0 / max(analyze_fps, 0.2)
    last_analyzed_at = 0.0

    try:
        while not stream_stop_event.is_set():
            ok, frame = capture.read()
            if not ok or frame is None:
                update_stream_state(last_error="Failed to read a frame from the live stream.")
                time.sleep(0.25)
                continue

            set_latest_raw_frame(frame)
            state = get_stream_state()
            update_stream_state(
                frames_read=state["frames_read"] + 1,
                last_frame_at=utc_now_iso(),
            )

            now = time.perf_counter()
            if now - last_analyzed_at < min_interval:
                continue

            started_at = time.perf_counter()
            result = analyze_image(frame)
            persist_result(result, (time.perf_counter() - started_at) * 1000)
            state = get_stream_state()
            update_stream_state(frames_analyzed=state["frames_analyzed"] + 1, last_error="")
            last_analyzed_at = now
    except Exception as exc:
        log.exception("Stream ingestion failed")
        update_stream_state(last_error=str(exc))
    finally:
        capture.release()
        update_stream_state(active=False)


def stop_stream_worker() -> None:
    global stream_thread
    stream_stop_event.set()
    if stream_thread and stream_thread.is_alive():
        stream_thread.join(timeout=3)
    stream_thread = None
    update_stream_state(active=False)


@app.on_event("startup")
async def startup_event() -> None:
    global model
    if not MODEL_PATH.exists():
        raise RuntimeError(f"Model file not found: {MODEL_PATH}")
    log.info("Loading YOLO model from %s", MODEL_PATH)
    model = YOLO(str(MODEL_PATH))
    log.info("Model loaded successfully")


@app.on_event("shutdown")
async def shutdown_event() -> None:
    stop_stream_worker()


@app.post("/analyze")
@app.post("/upload")
async def analyze(request: Request) -> JSONResponse:
    if model is None:
        raise HTTPException(status_code=503, detail="Model not loaded yet.")

    image_bytes = await request.body()
    if not image_bytes:
        raise HTTPException(status_code=400, detail="Empty request body.")

    try:
        started_at = time.perf_counter()
        result = analyze_image_bytes(image_bytes)
        return JSONResponse(persist_result(result, (time.perf_counter() - started_at) * 1000))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        log.exception("Detection error")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/stream/start")
async def start_stream(config: StreamConfig) -> JSONResponse:
    global stream_thread

    if model is None:
        raise HTTPException(status_code=503, detail="Model not loaded yet.")

    if stream_thread and stream_thread.is_alive():
        stop_stream_worker()

    stream_stop_event.clear()
    update_stream_state(
        active=False,
        stream_url=config.stream_url,
        analyze_fps=config.analyze_fps,
        worker_started_at=None,
        last_frame_at=None,
        last_error="",
        frames_read=0,
        frames_analyzed=0,
    )

    stream_thread = threading.Thread(
        target=stream_worker,
        args=(config.stream_url, config.analyze_fps),
        daemon=True,
        name="esp32-stream-worker",
    )
    stream_thread.start()
    return JSONResponse({"message": "Live stream analysis started.", **get_stream_state()})


@app.post("/stream/stop")
async def stop_stream() -> JSONResponse:
    stop_stream_worker()
    return JSONResponse({"message": "Live stream analysis stopped.", **get_stream_state()})


@app.get("/stream/status")
async def stream_status() -> JSONResponse:
    return JSONResponse(get_stream_state())


@app.get("/stream/frame")
async def stream_frame() -> Response:
    if latest_raw_frame_jpeg is None:
        raise HTTPException(status_code=404, detail="No raw stream frame available yet.")
    return Response(content=latest_raw_frame_jpeg, media_type="image/jpeg")


@app.post("/camera/flash")
async def set_camera_flash(control: FlashControl) -> JSONResponse:
    try:
        state = call_camera_flash(control.enabled)
        return JSONResponse(state)
    except HTTPException:
        raise
    except Exception as exc:
        log.exception("Unexpected flash control error")
        raise HTTPException(status_code=500, detail=f"Unexpected flash control error: {exc}") from exc


@app.get("/camera/flash")
async def get_camera_flash() -> JSONResponse:
    return JSONResponse(get_camera_state())


@app.post("/direction/select")
async def select_direction(selection: DirectionSelection) -> JSONResponse:
    direction = selection.direction.strip().lower()
    if direction == "auto":
        return JSONResponse(set_direction(None))
    if direction not in DIRECTIONS:
        raise HTTPException(status_code=400, detail="Direction must be one of auto, north, east, south, west.")
    return JSONResponse(set_direction(direction))


@app.get("/direction/status")
async def direction_status() -> JSONResponse:
    return JSONResponse(get_direction_state())


@app.get("/frames")
async def get_frames(limit: int = 20) -> JSONResponse:
    return JSONResponse(list(recent_frames)[: max(limit, 1)])


@app.get("/signal-plan")
async def signal_plan() -> JSONResponse:
    if not recent_frames:
        direction_info = get_direction_state()
        return JSONResponse(
            {
                "message": "No frames analyzed yet.",
                "signal_mode": "idle",
                "signal_state": "unknown",
                "preferred_phase": phase_for_direction(direction_info["active_direction"]),
                **direction_info,
            }
        )

    latest = recent_frames[0]
    return JSONResponse(
        {
            "frame_id": latest["frame_id"],
            "timestamp": latest["timestamp"],
            **latest["management"],
            "emergency_detected": latest["emergency_detected"],
        }
    )


@app.get("/stats")
async def get_stats() -> JSONResponse:
    if not recent_frames:
        return JSONResponse(
            {
                "total_frames_analyzed": frame_counter,
                "recent_frames_stored": 0,
                "total_vehicles_recent": 0,
                "avg_vehicles_per_frame": 0,
                "emergency_frames": 0,
                "category_totals": {},
                "density_distribution": {},
                "avg_congestion_score": 0,
                "current_signal_mode": "idle",
                "active_direction": get_direction_state()["active_direction"],
            }
        )

    total_vehicles = sum(frame.get("vehicle_count", 0) for frame in recent_frames)
    emergency_frames = sum(1 for frame in recent_frames if frame.get("emergency_detected"))
    category_totals: Counter[str] = Counter()
    density_distribution: Counter[str] = Counter()
    congestion_scores = []

    for frame in recent_frames:
        category_totals.update(frame.get("category_counts", {}))
        management = frame.get("management", {})
        density_distribution.update([management.get("density_level", "unknown")])
        congestion_scores.append(management.get("congestion_score", 0))

    latest_management = recent_frames[0].get("management", {})
    return JSONResponse(
        {
            "total_frames_analyzed": frame_counter,
            "recent_frames_stored": len(recent_frames),
            "total_vehicles_recent": total_vehicles,
            "avg_vehicles_per_frame": round(total_vehicles / max(len(recent_frames), 1), 2),
            "emergency_frames": emergency_frames,
            "category_totals": dict(category_totals),
            "density_distribution": dict(density_distribution),
            "avg_congestion_score": round(sum(congestion_scores) / max(len(congestion_scores), 1), 1),
            "current_signal_mode": latest_management.get("signal_mode", "idle"),
            "active_direction": latest_management.get("active_direction"),
        }
    )


@app.get("/health")
async def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "model_loaded": model is not None,
        "frames_analyzed": frame_counter,
        "model_path": str(MODEL_PATH),
        "stream": get_stream_state(),
        "direction": get_direction_state(),
        "camera": get_camera_state(),
    }


@app.get("/")
async def root() -> dict[str, str]:
    return {
        "service": "AI Traffic Management API",
        "docs": "/docs",
        "manual_analyze_endpoint": "/analyze",
        "stream_start_endpoint": "/stream/start",
        "stream_status_endpoint": "/stream/status",
        "direction_select_endpoint": "/direction/select",
    }
