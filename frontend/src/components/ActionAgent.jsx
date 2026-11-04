import { useEffect, useState } from "react";
import { postJson } from "../api.js";
import { ErrorNotice } from "./AsyncPanel.jsx";
import { withManualLinks } from "./Handover.jsx";
import { MAX_DATE, MIN_DATE } from "./Report.jsx";

export const PRESETS = [
  { time: "2018-09-07 20:00", label: "미루기로 해결되는 사건" },
  { time: "2018-10-10 13:00", label: "한도를 넘어 사람에게 넘기는 사건" },
  { time: "2018-09-03 14:30", label: "역률 사건 (콘덴서)" },
];
const STATUS = {
  open: { label: "계획 중", level: "warn" },
  waiting_approval: { label: "승인 대기", level: "warn" },
  resolved: { label: "해결됨", level: "good" },
  unresolved: { label: "해결 안 됨 · 사람에게 넘김", level: "bad" },
  no_action: { label: "조치 필요 없음", level: "good" },
};
const KIND = {
  detect: "감지", think: "판단", simulate: "시뮬레이션", propose: "제안", execute: "실행", approve: "승인", reject: "거절",
  revert: "되돌림", verify: "검증", blocked: "차단", fallback: "대체", finish: "완료",
};
const WHO = { agent: "에이전트", system: "시스템", user: "작업자" };
const ACTION_STATUS = { pending: "승인 대기", executed: "실행됨", rejected: "거절됨", reverted: "되돌림" };
const PLANT = new Set(["defer_load", "shed_auxiliary", "capacitor_step"]);
const TIMES = Array.from({ length: 96 }, (_, i) => `${String(Math.floor(i / 4)).padStart(2, "0")}:${String((i % 4) * 15).padStart(2, "0")}`);

/** 조치 입력을 사람이 읽는 문장으로 */
export function describe(action) {
  const p = action.params;
  if (action.type === "defer_load") return `${p.start}부터 ${p.minutes}분 동안 ${p.kw}kW 미루기 (뒤 ${p.minutes}분으로 옮김)`;
  if (action.type === "shed_auxiliary") return `${p.start}부터 ${p.minutes}분 동안 보조 설비 ${p.kw}kW 정지`;
  if (action.type === "capacitor_step") return `${p.start}부터 ${p.minutes}분 동안 콘덴서 ${p.steps}단(${p.steps * 10}kVar) 추가`;
  if (action.type === "notify") return `→ ${p.to}: ${p.message}`;
  return `${p.assignee} · ${p.title} · ${p.due_minutes}분 안에 ${p.cite ?? ""}`;
}

/** 조치 전(실제 기록 그대로)과 조치 후를 같은 축에 그린다. 기준선은 점선이다. */
export function CompareChart({ before, after, field, limit, unit, title }) {
  const w = 600;
  const h = 170;
  const pad = { l: 44, r: 24, t: 12, b: 24 };
  const values = [...before, ...after].map((r) => r[field]).concat(limit);
  const min = field === "pf" ? Math.max(0, Math.floor(Math.min(...values) / 10) * 10 - 5) : 0;
  const max = Math.max(...values) * (field === "pf" ? 1.02 : 1.1);
  const x = (i) => pad.l + (i * (w - pad.l - pad.r)) / (before.length - 1);
  const y = (v) => pad.t + (1 - (v - min) / (max - min)) * (h - pad.t - pad.b);
  const line = (rows) => rows.map((r, i) => `${x(i).toFixed(1)},${y(r[field]).toFixed(1)}`).join(" ");
  return (
    <figure className="compare-chart">
      <figcaption>
        {title} <span>{unit}</span>
      </figcaption>
      <svg viewBox={`0 0 ${w} ${h}`} role="img" aria-label={`${title}: 조치 전과 후`}>
        {[min, (min + max) / 2, max].map((v) => (
          <g key={v}>
            <line x1={pad.l} x2={w - pad.r} y1={y(v)} y2={y(v)} className="grid" />
            <text x={pad.l - 6} y={y(v) + 4} textAnchor="end">
              {Math.round(v)}
            </text>
          </g>
        ))}
        <line x1={pad.l} x2={w - pad.r} y1={y(limit)} y2={y(limit)} className="limit" />
        <text x={w - pad.r} y={y(limit) - 5} textAnchor="end" className="limit-label">
          기준 {limit}
        </text>
        <polyline points={line(before)} className="before" />
        <polyline points={line(after)} className="after" />
        {before.map((r, i) => (
          <text key={r.time} x={x(i)} y={h - 6} textAnchor="middle">
            {r.time}
          </text>
        ))}
      </svg>
      <p className="compare-legend">
        <span className="swatch before" /> 조치 안 했을 때(실제 기록) <span className="swatch after" /> 조치 후
      </p>
    </figure>
  );
}

function ActionCard({ action, busy, onDecide }) {
  const plant = PLANT.has(action.type);
  const e = action.expected;
  return (
    <li className={`action-card status-${action.status}`}>
      <div className="card-head">
        <h4>
          <small>{action.id}</small> {action.name}
        </h4>
        <span className={`risk ${action.risk}`}>{action.risk === "high" ? "승인 필요" : "자동 실행"}</span>
      </div>
      <p className="action-what">{withManualLinks(describe(action))}</p>
      {action.reason && <p className="muted">{withManualLinks(action.reason)}</p>}
      {e && (
        <p className="muted">
          예상 효과: 최대 {e.peak_kw[0]}→{e.peak_kw[1]}kW
          {e.min_pf[0] !== null && ` · 최저 역률 ${e.min_pf[0]}→${e.min_pf[1]}%`}
          {e.lead_slots > 0 && ` · 과보상 ${e.lead_slots}칸`}
        </p>
      )}
      {action.result && (
        <p className="muted">
          결과: {action.result.작업지시서 ? `작업지시서 ${action.result.작업지시서} 발행` : action.result.전달}
        </p>
      )}
      <div className="action-foot">
        <span className={`action-status ${action.status}`}>
          {ACTION_STATUS[action.status]}
          {action.executed_by === "user" ? " · 작업자 승인" : action.executed_by === "system" ? " · 규칙상 자동" : ""}
        </span>
        {action.status === "pending" && (
          <span className="action-buttons">
            <button type="button" className="save-button" disabled={busy} onClick={() => onDecide(action.id, "approve")}>
              승인하고 실행
            </button>
            <button type="button" disabled={busy} onClick={() => onDecide(action.id, "reject")}>
              거절
            </button>
          </span>
        )}
        {action.status === "executed" && plant && (
          <button type="button" disabled={busy} onClick={() => onDecide(action.id, "revert")}>
            되돌리기
          </button>
        )}
      </div>
    </li>
  );
}

function Verification({ incident }) {
  const v = incident.verification;
  if (!v) return null;
  const needed = Object.values(v.needed).some(Boolean);
  return (
    <div className={`verify-box level-${v.resolved ? "good" : "bad"}`}>
      <div className="card-head">
        <h3>실제 기록으로 채점한 결과</h3>
        <strong className="status-badge">{v.resolved ? "해결됨" : "해결 안 됨"}</strong>
      </div>
      {!needed && <p className="muted">실제로는 조치하지 않았어도 기준을 넘지 않았어요(헛경보). 그래도 조치가 해를 끼치지 않았는지 봅니다.</p>}
      {"usage" in v.checks && (
        <CompareChart before={v.without.rows} after={v.with.rows} field="kw" limit={incident.limits.usage_kw} unit="kW" title="앞으로 2시간 · 15분 수요전력" />
      )}
      {"power_factor" in v.checks && (
        <CompareChart before={v.without.rows.slice(0, 4)} after={v.with.rows.slice(0, 4)} field="pf" limit={incident.limits.pf_pct} unit="%" title="앞으로 1시간 역률" />
      )}
    </div>
  );
}

/** 경보 하나를 맡아 조사·시뮬레이션·조치·검증까지 하는 에이전트. 설비 조치는 작업자 승인 뒤 가상 공장에만 실행한다. */
export default function ActionAgent({ request }) {
  const [date, setDate] = useState("2018-09-07");
  const [time, setTime] = useState("20:00");
  const [incident, setIncident] = useState(null);
  const [state, setState] = useState({ status: "idle" });

  const run = async (when) => {
    setState({ status: "loading", what: "run" });
    try {
      setIncident(await postJson("/api/incidents", { time: when }));
      setState({ status: "idle" });
    } catch (error) {
      setState({ status: "error", error, retry: () => run(when) });
    }
  };
  const pick = (when) => {
    setDate(when.slice(0, 10));
    setTime(when.slice(11));
    run(when);
  };
  useEffect(() => {
    if (request?.time) pick(request.time);
  }, [request?.nonce]);

  const decide = async (actionId, decision) => {
    setState({ status: "loading", what: decision });
    try {
      setIncident(await postJson(`/api/incidents/${incident.id}/actions/${actionId}/${decision}`, {}));
      setState({ status: "idle" });
    } catch (error) {
      setState({ status: "error", error, retry: null });
    }
  };

  const inRange = date >= MIN_DATE && date <= MAX_DATE;
  const busy = state.status === "loading";
  const status = incident ? STATUS[incident.status] ?? STATUS.open : null;

  return (
    <section className="card action-agent" aria-label="조치 에이전트">
      <div className="card-head">
        <h2>조치 에이전트</h2>
        <div className="forecast-pickers">
          <input type="date" aria-label="사건 날짜" min={MIN_DATE} max={MAX_DATE} value={date} onChange={(e) => setDate(e.target.value)} />
          <select aria-label="사건 시각" value={time} onChange={(e) => setTime(e.target.value)}>
            {TIMES.map((t) => (
              <option key={t}>{t}</option>
            ))}
          </select>
          <button type="button" className="save-button" disabled={busy || !inRange} onClick={() => run(`${date} ${time}`)}>
            에이전트 실행
          </button>
        </div>
      </div>
      <p className="muted">
        경보가 켜진 시각을 "지금"으로 보고 에이전트가 조사 → 시뮬레이션 → 조치를 합니다. 알림·작업지시서는 바로 실행하고, 설비에 닿는
        조치는 작업자가 승인해야 <strong>가상 공장</strong>(2018년 기록 위의 시뮬레이터)에 실행합니다. 결과는 실제 기록으로 채점해요.
      </p>
      <div className="chips">
        {PRESETS.map((p) => (
          <button key={p.time} type="button" disabled={busy} onClick={() => pick(p.time)}>
            {p.label} · {p.time.slice(5)}
          </button>
        ))}
      </div>

      {busy && (
        <p className="muted" role="status">
          {state.what === "run" ? "에이전트가 조사하고 조치를 계획하는 중이에요…" : "실행하고 실제 기록으로 확인하는 중이에요…"}
        </p>
      )}
      {state.status === "error" && <ErrorNotice title="조치 에이전트가 실패했습니다." message={state.error.message} onRetry={state.retry} />}

      {incident && (
        <div className={`incident level-${status.level}`}>
          <div className="card-head">
            <h3>
              {incident.time} 사건 <small className="muted">· 계획: {incident.planner?.startsWith("ai") ? "AI 에이전트" : "규칙 계획자"}</small>
            </h3>
            <strong className="status-badge" aria-label={`사건 상태: ${status.label}`}>
              {status.label}
            </strong>
          </div>
          {incident.targets.length > 0 && (
            <ul className="incident-alarms">
              {incident.targets.map((t) => (
                <li key={t}>
                  {t === "usage" ? "사용량" : "역률"} 경보 <strong>{incident.problem[t].label}</strong> ({incident.problem[t].probability_pct}%)
                </li>
              ))}
            </ul>
          )}
          {incident.summary && <p className="incident-summary">{withManualLinks(incident.summary)}</p>}

          {incident.actions.length > 0 && (
            <>
              <h4 className="section-title">조치</h4>
              <ol className="action-list">
                {incident.actions.map((a) => (
                  <ActionCard key={a.id} action={a} busy={busy} onDecide={decide} />
                ))}
              </ol>
            </>
          )}

          <Verification incident={incident} />

          <details className="agent-steps" open>
            <summary>에이전트 기록 ({incident.steps.length})</summary>
            <ol>
              {incident.steps.map((s, i) => (
                <li key={i} className={`step-${s.kind}`}>
                  <span className="step-who">{WHO[s.who] ?? s.who}</span>
                  <span className="step-kind">{KIND[s.kind] ?? s.kind}</span>
                  <span>{withManualLinks(s.text)}</span>
                </li>
              ))}
            </ol>
          </details>
        </div>
      )}
    </section>
  );
}
