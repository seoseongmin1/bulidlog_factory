import { getJson } from "../api.js";
import { useAsync } from "../useAsync.js";
import AsyncPanel from "./AsyncPanel.jsx";
import { MAX_DATE, MIN_DATE } from "./Report.jsx";

const SOURCE_NOTE = {
  ai: "AI가 계산 결과를 쉬운 말로 정리했어요.",
  rule: "계산 결과로 자동 정리했어요.",
};

/**
 * 고른 날짜의 상태(좋음/주의/이상)와 세 줄 요약, 할 일을 질문 없이 자동으로 보여 준다.
 * AI 문장은 시간이 걸리므로 계산으로 만든 요약을 먼저 보여 주고, AI 문장이 오면 바꾼다.
 */
export default function Briefing({ date }) {
  const inRange = Boolean(date) && date >= MIN_DATE && date <= MAX_DATE;
  const quick = useAsync(
    () => (inRange ? getJson(`/api/briefing/${date}?ai=false`) : Promise.resolve(null)),
    [date, inRange],
  );
  const full = useAsync(() => (inRange ? getJson(`/api/briefing/${date}`) : Promise.resolve(null)), [date, inRange]);

  if (!inRange) return null;

  // AI 요약이 먼저 오면 그것을, 아니면 계산 요약을 보여 준다. AI 요청이 실패해도 계산 요약은 남는다.
  const state = full.status === "ready" ? full : quick;
  const polishing = full.status === "loading" && state.data?.source !== "ai";

  return (
    <section className="card briefing" aria-label="한눈에 보기">
      <AsyncPanel state={state} title="요약을 불러오지 못했습니다.">
        {(data) => (
          <div className={`briefing-body level-${data.status.level}`}>
            <div className="card-head">
              <h2>
                한눈에 보기 · {data.date} ({data.요일}
                {data.휴일 ? ` · ${data.휴일}` : ""})
              </h2>
              <strong className="status-badge" aria-label={`상태: ${data.status.label}`}>
                {data.status.label}
              </strong>
            </div>
            <ul className="briefing-lines">
              {data.lines.map((line) => (
                <li key={line}>{line}</li>
              ))}
            </ul>
            <p className="briefing-action">
              <strong>할 일</strong> {data.action}
            </p>
            <p className="muted" role="status">
              {polishing ? "AI가 쉬운 말로 다듬는 중이에요…" : (SOURCE_NOTE[data.source] ?? SOURCE_NOTE.rule)}
            </p>
          </div>
        )}
      </AsyncPanel>
    </section>
  );
}
