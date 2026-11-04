import { useState } from "react";
import { getJson } from "../api.js";
import { useAsync } from "../useAsync.js";
import AsyncPanel from "./AsyncPanel.jsx";
import Chart from "./Chart.jsx";
import { DEFAULT_FORECAST_DATE } from "./Forecast.jsx";
import { MAX_DATE } from "./Report.jsx";

// 앞의 일주일은 비교할 지난 날이 모자라 예측하지 않는다.
export const DAY_AHEAD_MIN_DATE = "2018-01-08";
const LEVEL_CLASS = { good: "level-good", warn: "level-warn", danger: "level-bad" };

function Block({ block }) {
  return (
    <li className={`block-chip ${LEVEL_CLASS[block.level]}`}>
      <div className="block-time">
        {block.start}~{block.end}
      </div>
      <strong className="status-badge" aria-label={`${block.start}~${block.end}: ${block.label}`}>
        {block.label}
      </strong>
      <small className="muted">
        지난 같은 날 {block.compared_days}일 중 {block.exceeded_days}일 넘음
      </small>
      <small className={block.over ? "block-actual-over" : "muted"}>
        {block.over ? `실제로 넘음 (최고 ${block.actual_max_kw}kW)` : `실제로 안 넘음 (최고 ${block.actual_max_kw}kW)`}
      </small>
    </li>
  );
}

function ScoreNote({ scores }) {
  const warn = scores.results.freq_warn;
  return (
    <div className="muted forecast-scores">
      <p>지난 채점 (9~12월, 2시간 구간 단위):</p>
      <ul>
        <li>
          주의 이상으로 본 구간이 실제로 넘은 {warn.초과_구간}구간 중 {warn.미리_잡은_비율_pct}%를 포함했어요. 하루 평균 {warn.하루_켜진_구간}구간을 켰고,
          그중 헛경보는 {warn.하루_헛_구간}구간이었어요.
        </li>
        <li>
          켜진 구간 중 실제로 넘은 것은 {warn.켜진_구간_중_실제_pct}%예요. 아무 구간이나 고르면 {scores.아무_구간이나_초과_pct}%가 넘어요.
        </li>
      </ul>
      <p>확률이 아니라 지난 4주 같은 날 유형의 빈도로 본 것이라 틀릴 수 있어요. 경보는 알리기만 하고 기계를 제어하지 않아요.</p>
    </div>
  );
}

/** 하루 전까지의 데이터로 다음 날의 위험 시간대를 알리고, 미리 준비할 계획을 보여 준다. 숫자는 모두 서버가 계산한다. */
export default function DayAhead() {
  const [date, setDate] = useState(DEFAULT_FORECAST_DATE);
  const inRange = Boolean(date) && date >= DAY_AHEAD_MIN_DATE && date <= MAX_DATE;
  const state = useAsync(() => (inRange ? getJson(`/api/day-ahead/${date}`) : Promise.resolve(null)), [date, inRange]);

  return (
    <section className="card" aria-label="내일 예측">
      <div className="card-head">
        <h2>내일 예측 · 하루 전 준비</h2>
        <div className="forecast-pickers">
          <input
            type="date"
            aria-label="예측할 날"
            min={DAY_AHEAD_MIN_DATE}
            max={MAX_DATE}
            value={date}
            onChange={(event) => setDate(event.target.value)}
          />
        </div>
      </div>

      {!inRange && (
        <p className="range-notice" role="alert">
          고를 수 있는 날은 2018년 1월 8일부터 12월 31일까지입니다. 앞의 일주일은 비교할 지난 날이 모자라요.
        </p>
      )}

      {inRange && (
        <AsyncPanel state={state} title="내일 예측을 불러오지 못했습니다.">
          {(data) => (
            <div className="forecast-body">
              <p className="muted">
                {data.issued_at}까지의 데이터로 {data.date}({data.day_type})의 사용량 기준 {data.limit_kw}kW 초과 위험을 예측했어요.
              </p>
              {data.learned_period && (
                <p className="range-notice" role="note">
                  학습에 쓴 기간이라 실제보다 잘 맞아 보일 수 있어요.
                </p>
              )}

              <div className="briefing-action plan-box">
                <strong>준비할 일</strong>
                <ul>
                  {data.plan.map((line) => (
                    <li key={line}>{line}</li>
                  ))}
                </ul>
                <p className="muted">{data.same_day_note}</p>
              </div>

              <ul className="block-grid" aria-label="2시간 구간별 위험">
                {data.blocks.map((block) => (
                  <Block key={block.start} block={block} />
                ))}
              </ul>
              <p className="tl-summary-line">
                실제 결과: 위험으로 본 {data.result.위험_구간}구간 중 {data.result.맞힌_구간}구간이 실제로 넘었어요. 놓친 구간은{" "}
                {data.result.놓친_구간}개, 헛경보는 {data.result.헛_구간}개예요.
              </p>

              <figure className="card pf-chart">
                <figcaption>
                  평소 사용량 (지난 4주 같은 날 유형) <span>kW · 기준 {data.limit_kw}kW</span>
                </figcaption>
                <div className="chart">
                  <Chart
                    kind="line"
                    unit="kW"
                    label="평소 사용량 선 차트"
                    rows={data.rows.map((row) => ({ label: row.time, value: row.usual_kw }))}
                    suggestedMax={Math.ceil(data.limit_kw * 1.1)}
                  />
                </div>
              </figure>

              <ScoreNote scores={data.scores} />
            </div>
          )}
        </AsyncPanel>
      )}
    </section>
  );
}
