import { useState } from "react";
import { getJson } from "../api.js";
import { downloadMarkdown } from "../download.js";
import { useAsync } from "../useAsync.js";
import AsyncPanel from "./AsyncPanel.jsx";
import { MAX_DATE, MIN_DATE } from "./Report.jsx";

export const SHIFTS = [
  { id: "A", label: "A조", hours: "06~14시" },
  { id: "B", label: "B조", hours: "14~22시" },
  { id: "C", label: "C조", hours: "22~06시" },
];
export const DEFAULT_HANDOVER_DATE = "2018-10-10";
export const DEFAULT_SHIFT = "B";

const SOURCE_NOTE = {
  ai: "AI가 문장을 다듬었고, 숫자는 모두 계산 결과와 같은지 검사를 통과했어요.",
  rule: "계산 결과로 자동 작성했어요.",
};

/** 문장 속 [SOP-EN-01 2. 경보 단계별 조치] 표기를 매뉴얼 탭으로 가는 링크로 바꾼다. */
export function withManualLinks(text) {
  const parts = text.split(/(\[(?:SOP|GUIDE)-[A-Z]*-?\d+[^\]]*\])/g);
  return parts.map((part, i) => {
    const match = part.match(/^\[((?:SOP|GUIDE)-[A-Z]*-?\d+)/);
    return match ? (
      <a key={i} className="cite" href={`#manual:${match[1]}`}>
        {part}
      </a>
    ) : (
      part
    );
  });
}

function Facts({ facts }) {
  const alarms = (t) => facts.경보[t];
  const unanswered = ["usage", "power_factor"].reduce((n, t) => n + alarms(t).경보.filter((a) => !a.응답).length, 0);
  const rows = [
    ["사용량", `${facts.사용량_kwh.toLocaleString("ko-KR")} kWh`, facts.평소_kwh === null ? "평소 기록 없음" : `평소 ${facts.평소_kwh.toLocaleString("ko-KR")} kWh (${facts.평소_대비_pct > 0 ? "+" : ""}${facts.평소_대비_pct}%)`],
    ["최대 수요전력", `${facts.최대_수요전력_kw.toLocaleString("ko-KR")} kW`, facts.최대_시각],
    ["역률", facts.역률_pct === null ? "-" : `${facts.역률_pct}%`, "09~22시만 봐요"],
    ["경보", `사용량 ${alarms("usage").경보.length} · 역률 ${alarms("power_factor").경보.length}`, `실제 초과 ${alarms("usage").초과.length + alarms("power_factor").초과.length}번`],
    ["미응답 경보", `${unanswered}`, Object.entries(facts.응답).map(([k, v]) => `${k} ${v}`).join(" · ")],
  ];
  return (
    <div className="kpis handover-kpis">
      {rows.map(([label, value, note]) => (
        <div className="kpi" key={label}>
          <span>{label}</span>
          <strong>{value}</strong>
          <small>{note}</small>
        </div>
      ))}
    </div>
  );
}

/** 인수인계서 본문. 탭과 감시 재생의 알림에서 함께 쓴다. */
export function HandoverBody({ data, polishing, compact = false }) {
  const f = data.facts;
  const title = `${f.조} ${f.시간} · ${f.date} (${f.요일}${f.휴일 ? ` · ${f.휴일}` : ""})`;
  return (
    <div className={`handover-body level-${data.status.level}`}>
      <div className="card-head">
        <h3>{title}</h3>
        <strong className="status-badge" aria-label={`인수인계 상태: ${data.status.label}`}>
          {data.status.label}
        </strong>
      </div>
      {f.일부만 && <p className="range-notice">데이터가 2018-12-31 24:00에서 끝나 이 교대는 일부만 들어 있어요.</p>}
      <ul className="briefing-lines">
        {data.note.summary.map((line) => (
          <li key={line}>{line}</li>
        ))}
      </ul>
      {!compact && <Facts facts={f} />}
      <div className="handover-section">
        <h4>인계 사항</h4>
        <ul>
          {data.note.items.map((line) => (
            <li key={line}>{withManualLinks(line)}</li>
          ))}
        </ul>
      </div>
      <div className="handover-section next">
        <h4>다음 조가 먼저 확인할 것</h4>
        <ul>
          {data.note.next.map((line) => (
            <li key={line}>{withManualLinks(line)}</li>
          ))}
        </ul>
      </div>
      <div className="handover-foot">
        <p className="muted" role="status">
          {polishing ? "AI가 문장을 다듬는 중이에요…" : (SOURCE_NOTE[data.source] ?? SOURCE_NOTE.rule)}
        </p>
        <button
          type="button"
          className="save-button"
          onClick={() => downloadMarkdown(data.markdown, `handover-${f.date}-${data.shift}.md`)}
        >
          .md로 저장
        </button>
      </div>
    </div>
  );
}

/** 코드가 쓴 인수인계서를 먼저 보여 주고, AI가 다듬은 문장이 오면 바꾼다. AI가 실패해도 코드 문서는 남는다. */
export function HandoverView({ date, shift, compact }) {
  const quick = useAsync(() => getJson(`/api/handover/${date}/${shift}?ai=false`), [date, shift]);
  const full = useAsync(() => getJson(`/api/handover/${date}/${shift}`), [date, shift]);
  const state = full.status === "ready" ? full : quick;
  const polishing = full.status === "loading" && state.data?.source !== "ai";
  return (
    <AsyncPanel state={state} title="인수인계서를 만들지 못했습니다.">
      {(data) => <HandoverBody data={data} polishing={polishing} compact={compact} />}
    </AsyncPanel>
  );
}

export default function Handover() {
  const [date, setDate] = useState(DEFAULT_HANDOVER_DATE);
  const [shift, setShift] = useState(DEFAULT_SHIFT);
  const inRange = Boolean(date) && date >= MIN_DATE && date <= MAX_DATE;

  return (
    <section className="card" aria-label="교대 인수인계">
      <div className="card-head">
        <h2>교대 인수인계서</h2>
        <div className="forecast-pickers">
          <input type="date" aria-label="인수인계 날짜" min={MIN_DATE} max={MAX_DATE} value={date} onChange={(e) => setDate(e.target.value)} />
          <div className="segmented" role="radiogroup" aria-label="교대 조">
            {SHIFTS.map((s) => (
              <button key={s.id} type="button" role="radio" aria-checked={shift === s.id} onClick={() => setShift(s.id)}>
                {s.label} <small>{s.hours}</small>
              </button>
            ))}
          </div>
        </div>
      </div>
      <p className="muted handover-intro">
        교대가 끝나면 그 조의 사용량·경보·작업자 응답을 모아 자동으로 써 둡니다. 작업자는 확인하고 현장 사항만 덧붙이면 돼요. 감시
        재생에서 교대 시각이 지나면 알림으로 뜨고, <code>python -m app.autorun --now --send</code>를 작업 스케줄러에 걸면 메신저로
        받습니다.
      </p>
      {!inRange && (
        <p className="range-notice" role="alert">
          고를 수 있는 날짜는 2018년 1월 1일부터 12월 31일까지입니다.
        </p>
      )}
      {inRange && <HandoverView date={date} shift={shift} />}
    </section>
  );
}
