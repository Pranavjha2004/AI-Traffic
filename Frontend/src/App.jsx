import { useEffect, useState } from "react";
import {
  Area,
  AreaChart,
  CartesianGrid,
  Cell,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import "./App.css";

const API_BASE = import.meta.env.VITE_API_URL || "http://127.0.0.1:8000";
const POLL_INTERVAL_MS = 2500;
const DEFAULT_STREAM_URL = import.meta.env.VITE_STREAM_URL || "http://192.168.1.50/stream";
const DIRECTIONS = ["auto", "north", "east", "south", "west"];

const CATEGORY_COLORS = {
  bicycle: "#facc15",
  motorcycle: "#22c55e",
  car: "#38bdf8",
  truck: "#f97316",
  bus: "#8b5cf6",
  AMBULANCE: "#ef4444",
};

const DENSITY_COLORS = {
  low: "#22c55e",
  medium: "#f59e0b",
  high: "#f97316",
  severe: "#ef4444",
  emergency: "#dc2626",
  manual_focus: "#06b6d4",
};

function StatCard({ label, value, helper, tone = "#38bdf8" }) {
  return (
    <div className="stat-card" style={{ "--tone": tone }}>
      <div className="stat-label">{label}</div>
      <div className="stat-value">{value}</div>
      <div className="stat-helper">{helper}</div>
    </div>
  );
}

function StatusPill({ label, value, color }) {
  return (
    <div className="status-pill" style={{ "--pill": color }}>
      <span className="status-pill-label">{label}</span>
      <span className="status-pill-value">{value}</span>
    </div>
  );
}

function formatTimestamp(value) {
  if (!value) return "No timestamp";
  try {
    return new Date(value).toLocaleString("en-IN");
  } catch {
    return value;
  }
}

function FrameModal({ frame, onClose }) {
  if (!frame) return null;

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal" onClick={(event) => event.stopPropagation()}>
        <div className="modal-header">
          <div>
            <h3>Frame #{frame.frame_id}</h3>
            <p>{formatTimestamp(frame.timestamp)}</p>
          </div>
          <button className="modal-close" onClick={onClose}>
            Close
          </button>
        </div>

        {frame.annotated_image ? (
          <img
            className="modal-image"
            src={`data:image/jpeg;base64,${frame.annotated_image}`}
            alt={`Frame ${frame.frame_id}`}
          />
        ) : null}

        <div className="modal-grid">
          <StatusPill
            label="Density"
            value={frame.management?.density_level || "unknown"}
            color={DENSITY_COLORS[frame.management?.density_level] || "#64748b"}
          />
          <StatusPill label="Signal" value={frame.management?.signal_state || "unknown"} color="#0ea5e9" />
          <StatusPill label="Observed" value={frame.management?.active_direction || "auto"} color="#22c55e" />
          <StatusPill
            label="Green Time"
            value={`${frame.management?.recommended_green_sec ?? 0}s`}
            color="#f97316"
          />
        </div>
      </div>
    </div>
  );
}

export default function App() {
  const [frames, setFrames] = useState([]);
  const [stats, setStats] = useState(null);
  const [signalPlan, setSignalPlan] = useState(null);
  const [streamStatus, setStreamStatus] = useState(null);
  const [directionStatus, setDirectionStatus] = useState(null);
  const [cameraStatus, setCameraStatus] = useState(null);
  const [connected, setConnected] = useState(false);
  const [error, setError] = useState("");
  const [history, setHistory] = useState([]);
  const [selectedFrame, setSelectedFrame] = useState(null);
  const [streamUrl, setStreamUrl] = useState(() => localStorage.getItem("traffic-stream-url") || DEFAULT_STREAM_URL);
  const [analyzeFps, setAnalyzeFps] = useState(2);
  const [actionBusy, setActionBusy] = useState(false);

  useEffect(() => {
    localStorage.setItem("traffic-stream-url", streamUrl);
  }, [streamUrl]);

  useEffect(() => {
    let cancelled = false;

    const load = async () => {
      try {
        const [framesResponse, statsResponse, signalResponse, streamResponse, directionResponse, cameraResponse] = await Promise.all([
          fetch(`${API_BASE}/frames?limit=20`),
          fetch(`${API_BASE}/stats`),
          fetch(`${API_BASE}/signal-plan`),
          fetch(`${API_BASE}/stream/status`),
          fetch(`${API_BASE}/direction/status`),
          fetch(`${API_BASE}/camera/flash`),
        ]);

        if (!framesResponse.ok || !statsResponse.ok || !signalResponse.ok || !streamResponse.ok || !directionResponse.ok || !cameraResponse.ok) {
          throw new Error("Dashboard API request failed.");
        }

        const [framesPayload, statsPayload, signalPayload, streamPayload, directionPayload, cameraPayload] = await Promise.all([
          framesResponse.json(),
          statsResponse.json(),
          signalResponse.json(),
          streamResponse.json(),
          directionResponse.json(),
          cameraResponse.json(),
        ]);

        if (cancelled) return;

        setFrames(framesPayload);
        setStats(statsPayload);
        setSignalPlan(signalPayload);
        setStreamStatus(streamPayload);
        setDirectionStatus(directionPayload);
        setCameraStatus(cameraPayload);
        setConnected(true);
        setError("");

        if (framesPayload.length > 0) {
          const latest = framesPayload[0];
          setHistory((previous) => {
            const point = {
              time: new Date(latest.timestamp).toLocaleTimeString("en-IN", {
                hour: "2-digit",
                minute: "2-digit",
                second: "2-digit",
              }),
              vehicles: latest.vehicle_count ?? 0,
            };
            const last = previous[previous.length - 1];
            if (last && last.time === point.time && last.vehicles === point.vehicles) {
              return previous;
            }
            return [...previous, point].slice(-25);
          });
        }
      } catch (loadError) {
        if (cancelled) return;
        setConnected(false);
        setError(loadError.message || "Could not connect to backend.");
      }
    };

    load();
    const intervalId = window.setInterval(load, POLL_INTERVAL_MS);
    return () => {
      cancelled = true;
      window.clearInterval(intervalId);
    };
  }, []);

  const startStream = async () => {
    try {
      setActionBusy(true);
      const response = await fetch(`${API_BASE}/stream/start`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ stream_url: streamUrl, analyze_fps: Number(analyzeFps) }),
      });
      if (!response.ok) {
        const payload = await response.json().catch(() => ({}));
        throw new Error(payload.detail || payload.message || "Failed to start stream analysis.");
      }
      setStreamStatus(await response.json());
      setError("");
    } catch (streamError) {
      setError(streamError.message || "Could not start stream.");
    } finally {
      setActionBusy(false);
    }
  };

  const stopStream = async () => {
    try {
      setActionBusy(true);
      const response = await fetch(`${API_BASE}/stream/stop`, { method: "POST" });
      if (!response.ok) {
        throw new Error("Failed to stop stream analysis.");
      }
      setStreamStatus(await response.json());
      setError("");
    } catch (streamError) {
      setError(streamError.message || "Could not stop stream.");
    } finally {
      setActionBusy(false);
    }
  };

  const selectDirection = async (direction) => {
    try {
      setActionBusy(true);
      const response = await fetch(`${API_BASE}/direction/select`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ direction }),
      });
      if (!response.ok) {
        const payload = await response.json().catch(() => ({}));
        throw new Error(payload.detail || "Failed to change direction.");
      }
      setDirectionStatus(await response.json());
      setError("");
    } catch (directionError) {
      setError(directionError.message || "Could not change direction.");
    } finally {
      setActionBusy(false);
    }
  };

  const toggleFlash = async () => {
    try {
      setActionBusy(true);
      const response = await fetch(`${API_BASE}/camera/flash`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ enabled: !cameraStatus?.flash_enabled }),
      });
      if (!response.ok) {
        const payload = await response.json().catch(() => ({}));
        throw new Error(payload.detail || "Failed to change flashlight state.");
      }
      setCameraStatus(await response.json());
      setError("");
    } catch (flashError) {
      setError(flashError.message || "Could not change flashlight state.");
    } finally {
      setActionBusy(false);
    }
  };

  const latestFrame = frames[0] || null;
  const categoryEntries = Object.entries(latestFrame?.category_counts || {});
  const distributionData = Object.entries(stats?.category_totals || {}).map(([name, value]) => ({
    name,
    value,
    color: CATEGORY_COLORS[name] || "#94a3b8",
  }));
  const densityColor = DENSITY_COLORS[signalPlan?.density_level] || "#64748b";
  const activeDirection = directionStatus?.active_direction || null;

  return (
    <div className="app-shell">
      <FrameModal frame={selectedFrame} onClose={() => setSelectedFrame(null)} />

      <header className="topbar">
        <div>
          <p className="eyebrow">AI Traffic Detection and Management</p>
          <h1>Live video stream, density analysis, and signal control</h1>
        </div>
        <div className={`connection ${connected ? "online" : "offline"}`}>
          <span className="connection-dot" />
          {connected ? "Backend connected" : "Backend offline"}
        </div>
      </header>

      {signalPlan?.emergency_detected ? (
        <section className="alert-banner">
          Emergency vehicle detected. Signal override is active and priority green is recommended.
        </section>
      ) : null}

      <main className="layout">
        <section className="stream-control-panel">
          <div className="panel-title-row">
            <div>
              <p className="eyebrow">Stream control</p>
              <h2>Connect backend to live camera</h2>
            </div>
          </div>

          <div className="form-grid">
            <label className="field">
              <span>ESP32 stream URL</span>
              <input
                value={streamUrl}
                onChange={(event) => setStreamUrl(event.target.value)}
                placeholder="http://192.168.1.50/stream"
              />
            </label>
            <label className="field">
              <span>Analyze FPS</span>
              <input
                type="number"
                min="0.2"
                max="10"
                step="0.1"
                value={analyzeFps}
                onChange={(event) => setAnalyzeFps(event.target.value)}
              />
            </label>
          </div>

          <div className="button-row">
            <button className="primary-button" onClick={startStream} disabled={actionBusy}>
              Start live analysis
            </button>
            <button className="secondary-button" onClick={stopStream} disabled={actionBusy}>
              Stop analysis
            </button>
            <button className="secondary-button" onClick={toggleFlash} disabled={actionBusy}>
              {cameraStatus?.flash_enabled ? "Disable flash" : "Enable flash"}
            </button>
          </div>

          <div className="decision-grid">
            <StatusPill label="Stream active" value={streamStatus?.active ? "yes" : "no"} color="#22c55e" />
            <StatusPill label="Frames read" value={streamStatus?.frames_read ?? 0} color="#38bdf8" />
            <StatusPill label="Frames analyzed" value={streamStatus?.frames_analyzed ?? 0} color="#f97316" />
            <StatusPill
              label="Observed direction"
              value={activeDirection || "auto"}
              color="#06b6d4"
            />
          </div>

          {streamStatus?.last_error ? <p className="warning-text">{streamStatus.last_error}</p> : null}
          {cameraStatus?.last_flash_error ? <p className="warning-text">{cameraStatus.last_flash_error}</p> : null}
        </section>

        <section className="direction-panel">
          <div className="panel-title-row">
            <div>
              <p className="eyebrow">Manual direction</p>
              <h2>Tell the system which side the camera is observing</h2>
            </div>
          </div>

          <div className="direction-buttons">
            {DIRECTIONS.map((direction) => {
              const isActive =
                (direction === "auto" && !activeDirection) || activeDirection === direction;
              return (
                <button
                  key={direction}
                  type="button"
                  className={`direction-button ${isActive ? "active" : ""}`}
                  onClick={() => selectDirection(direction)}
                  disabled={actionBusy}
                >
                  {direction}
                </button>
              );
            })}
          </div>

          <p className="decision-copy">
            {activeDirection
              ? `Camera is manually focused on ${activeDirection}. The selected road stays red while the perpendicular road gets green based on observed density.`
              : "Auto mode is active. No manual road direction is locked right now."}
          </p>
        </section>

        <section className="hero-panel">
          <div className="panel-title-row">
            <div>
              <p className="eyebrow">Analyzed output</p>
              <h2>Annotated traffic feed</h2>
            </div>
            <div className="tiny-meta">
              {latestFrame ? `Updated ${formatTimestamp(latestFrame.timestamp)}` : "Waiting for analyzed frames"}
            </div>
          </div>

          <div className="feed-frame">
            {latestFrame?.annotated_image ? (
              <img
                src={`data:image/jpeg;base64,${latestFrame.annotated_image}`}
                alt="Annotated live traffic feed"
              />
            ) : (
              <div className="empty-state">
                <strong>No analyzed frames yet.</strong>
                <span>Start live analysis so the backend can ingest and process the stream.</span>
              </div>
            )}
          </div>

          <div className="chip-row">
            {categoryEntries.length > 0 ? (
              categoryEntries.map(([name, count]) => (
                <span key={name} className="chip" style={{ "--chip": CATEGORY_COLORS[name] || "#94a3b8" }}>
                  {name}: {count}
                </span>
              ))
            ) : (
              <span className="muted-note">No detections to summarize yet.</span>
            )}
          </div>
        </section>

        <section className="decision-panel">
          <p className="eyebrow">Adaptive decision engine</p>
          <h2>Current signal recommendation</h2>

          <div className="decision-highlight" style={{ "--density": densityColor }}>
            <div>
              <span className="decision-label">Density</span>
              <strong>{signalPlan?.density_level || "idle"}</strong>
            </div>
            <div>
              <span className="decision-label">Signal Mode</span>
              <strong>{signalPlan?.signal_mode || "idle"}</strong>
            </div>
            <div>
              <span className="decision-label">State</span>
              <strong>{signalPlan?.signal_state || "unknown"}</strong>
            </div>
          </div>

          <div className="decision-grid">
            <StatusPill label="Vehicles" value={latestFrame?.vehicle_count ?? 0} color="#0ea5e9" />
            <StatusPill label="Congestion Score" value={signalPlan?.congestion_score ?? 0} color="#f97316" />
            <StatusPill
              label="Recommended Green"
              value={`${signalPlan?.recommended_green_sec ?? 0}s`}
              color="#22c55e"
            />
            <StatusPill
              label="Preferred phase"
              value={signalPlan?.preferred_phase || "cycle"}
              color="#8b5cf6"
            />
          </div>

          <p className="decision-copy">
            {signalPlan?.action || "No management action available until the first frame is analyzed."}
          </p>
        </section>

        <section className="stats-grid">
          <StatCard label="Frames analyzed" value={stats?.total_frames_analyzed ?? 0} helper="YOLO processed frames" tone="#38bdf8" />
          <StatCard label="Average vehicles per frame" value={stats?.avg_vehicles_per_frame ?? 0} helper="Rolling average" tone="#14b8a6" />
          <StatCard label="Emergency frames" value={stats?.emergency_frames ?? 0} helper="Ambulance detections" tone="#ef4444" />
          <StatCard label="Average congestion score" value={stats?.avg_congestion_score ?? 0} helper="Density-based traffic load" tone="#f97316" />
        </section>

        <section className="chart-panel">
          <div className="panel-title-row">
            <div>
              <p className="eyebrow">Traffic trend</p>
              <h2>Vehicle count over time</h2>
            </div>
          </div>
          <ResponsiveContainer width="100%" height={260}>
            <AreaChart data={history}>
              <defs>
                <linearGradient id="vehicleGradient" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="5%" stopColor="#38bdf8" stopOpacity={0.45} />
                  <stop offset="95%" stopColor="#38bdf8" stopOpacity={0.03} />
                </linearGradient>
              </defs>
              <CartesianGrid stroke="#243145" strokeDasharray="4 4" />
              <XAxis dataKey="time" tick={{ fill: "#94a3b8", fontSize: 11 }} />
              <YAxis tick={{ fill: "#94a3b8", fontSize: 11 }} allowDecimals={false} />
              <Tooltip contentStyle={{ background: "#0f172a", border: "1px solid #22304a", borderRadius: "12px" }} />
              <Area type="monotone" dataKey="vehicles" stroke="#38bdf8" strokeWidth={3} fill="url(#vehicleGradient)" />
            </AreaChart>
          </ResponsiveContainer>
        </section>

        <section className="distribution-panel">
          <div className="panel-title-row">
            <div>
              <p className="eyebrow">Vehicle mix</p>
              <h2>Category distribution</h2>
            </div>
          </div>
          {distributionData.length > 0 ? (
            <div className="distribution-layout">
              <ResponsiveContainer width="50%" height={220}>
                <PieChart>
                  <Pie data={distributionData} dataKey="value" innerRadius={45} outerRadius={82}>
                    {distributionData.map((entry) => (
                      <Cell key={entry.name} fill={entry.color} />
                    ))}
                  </Pie>
                </PieChart>
              </ResponsiveContainer>
              <div className="distribution-list">
                {distributionData.map((entry) => (
                  <div key={entry.name} className="distribution-item">
                    <span className="swatch" style={{ background: entry.color }} />
                    <span>{entry.name}</span>
                    <strong>{entry.value}</strong>
                  </div>
                ))}
              </div>
            </div>
          ) : (
            <div className="empty-inline">Distribution will appear after the first analyzed frame.</div>
          )}
        </section>

        <section className="frames-panel">
          <div className="panel-title-row">
            <div>
              <p className="eyebrow">Recent evidence</p>
              <h2>Analyzed frames</h2>
            </div>
          </div>

          {frames.length > 0 ? (
            <div className="frame-list">
              {frames.map((frame) => (
                <button key={frame.frame_id} className="frame-card" onClick={() => setSelectedFrame(frame)} type="button">
                  {frame.annotated_image ? (
                    <img src={`data:image/jpeg;base64,${frame.annotated_image}`} alt={`Frame ${frame.frame_id}`} />
                  ) : (
                    <div className="frame-fallback">No image</div>
                  )}
                  <div className="frame-card-body">
                    <div className="frame-card-top">
                      <strong>Frame #{frame.frame_id}</strong>
                      <span className="mini-density" style={{ "--mini-density": DENSITY_COLORS[frame.management?.density_level] || "#64748b" }}>
                        {frame.management?.density_level || "unknown"}
                      </span>
                    </div>
                    <span>{frame.vehicle_count} vehicles</span>
                    <span>{formatTimestamp(frame.timestamp)}</span>
                  </div>
                </button>
              ))}
            </div>
          ) : (
            <div className="empty-inline">Frames will appear here when the backend processes the live stream.</div>
          )}
        </section>

        {!connected && error ? <section className="error-box">{error}</section> : null}
      </main>
    </div>
  );
}
