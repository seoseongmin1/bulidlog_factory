import { useState } from "react";
import { getJson } from "../api.js";
import { useAsync } from "../useAsync.js";
import AlarmTimeline from "./AlarmTimeline.jsx";
import AsyncPanel from "./AsyncPanel.jsx";
import ResponseButtons from "./ResponseButtons.jsx";
import { MAX_DATE, MIN_DATE } from "./Report.jsx";
import { useAlarmResponses } from "../responses.js";

// 채점 기간(9~12월) 안이고 사용량 '위험'이 나오는 시각이라 처음 보여 주기에 알맞다.
export const DEFAULT_FORECAST_DATE = "2018-10-10";
export const DEFAULT_FORECAST_TIME = "13:00";
// 마지막 1시간은 채점할 실제 값이 없어 고를 수 없다.
const LAST_TIME = "22:45";

const TIMES = Array.from({ length: 96 }, (_, i) => {
  const minutes = i * 15;
  return `${String(Math.floor(minutes / 60)).padStart(2, "0")}:${String(minutes % 60).padStart(2, "0")}`;
});
// 한눈에 보기와 같은 색을 쓴다. 위험은 '이상'과 같은 빨강이다.
const LEVEL_CLASS = { good: "level-good", warn: "level-warn", danger: "level-bad" };

function ScoreNote({ name, score }) {
  return (
    <li>
      {name}: 기준을 넘은 {score.초과_구간}번 중 {score.미리_잡은_비율_pct}%를 평균 {Math.round(score.평균_몇분_전)}분 전에 알렸고,
      헛경보는 하루 {score.하루_헛경보.toFixed(1)}번이었어요.
    </li>
  );
}

function Reasons({ reasons }) {
  if (!reasons?.length) return null;
  return (
    <div className="reasons">
      <p className="muted">왜 이렇게 봤나요? 그 정보를 평소 같은 값으로 바꾸면 확률이 이만큼 내려가요.</p>
      <ul>
        {reasons.map((reason) => (
          <li key={reason.name}>
            <strong>{reason.name}</strong> <span className="reason-points">+{reason.points}%p</span>
            <span className="muted"> {reason.detail}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

function AlarmCard({ title, level, label, children, action, reasons, response }) {
  return (
    <div className={`alarm-card ${LEVEL_CLASS[level]}`}>
      <div className="card-head">
        <h3>{title}</h3>
        <strong className="status-badge" aria-label={`${title}: ${label}`}>
          {label}
        </strong>
      </div>
      {children}
      <Reasons reasons={reasons} />
      <p className="briefing-action">
        <strong>할 일</strong> {action}
      </p>
      {response}
    </div>
  );
}

/** 고른 시각을 '지금'으로 보고 앞으로 1시간의 사용량·역률 경보를 보여 준다. 숫자는 모두 서버의 예측 모델이 계산한다. */
export default function Forecast({ onRecorded }) {
  const [date, setDate] = useState(DEFAULT_FORECAST_DATE);
  const [time, setTime] = useState(DEFAULT_FORECAST_TIME);
  const dateInRange = Boolean(date) && date >= MIN_DATE && date <= MAX_DATE;
  const inRange = dateInRange && !(date === MAX_DATE && time > LAST_TIME);
  const { saved, save } = useAlarmResponses(date, onRecorded);
  // 주의·위험이 켜진 경보에만 응답을 받는다.
  const responseFor = (target, name, item) =>
    item.level !== "good" && item.watched !== false ? (
      <ResponseButtons
        alarm={{ time, target, name, level: item.level, probability_pct: item.probability_pct }}
        saved={saved[`${time}|${target}`]}
        onSave={save}
      />
    ) : null;
  const state = useAsync(
    () => (inRange ? getJson(`/api/forecast?time=${encodeURIComponent(`${date} ${time}`)}`) : Promise.resolve(null)),
    [date, time, inRange],
  );

  return (
    <section className="card" aria-label="경보 예측">
      <div className="card-head">
        <h2>경보 예측 · 앞으로 1시간</h2>
        <div className="forecast-pickers">
          <input
            type="date"
            aria-label="예측 날짜"
            min={MIN_DATE}
            max={MAX_DATE}
            value={date}
            onChange={(event) => setDate(event.target.value)}
          />
          <select aria-label="예측 시각" value={time} onChange={(event) => setTime(event.target.value)}>
            {TIMES.map((t) => (
              <option key={t} value={t}>
                {t}
              </option>
            ))}
          </select>
        </div>
      </div>

      {!inRange && (
        <p className="range-notice" role="alert">
          고를 수 있는 시각은 2018년 1월 1일 00:00부터 12월 31일 {LAST_TIME}까지입니다.
        </p>
      )}

      {inRange && (
        <AsyncPanel state={state} title="경보 예측을 불러오지 못했습니다.">
          {(data) => (
            <div className="forecast-body">
              <p className="muted">
                {data.time}을 "지금"으로 보고 {data.until.slice(11)}까지를 예측했어요.
              </p>
              {data.learned_period && (
                <p className="range-notice" role="note">
                  학습에 쓴 기간이라 실제보다 잘 맞아 보일 수 있어요.
                </p>
              )}
              <div className="alarm-grid">
                <AlarmCard title="사용량" {...data.usage} response={responseFor("usage", "사용량", data.usage)}>
                  <p>
                    기준을 넘을 가능성 <strong>{data.usage.probability_pct}%</strong>
                  </p>
                  <p className="muted">
                    지금 {data.usage.now_kw.toLocaleString("ko-KR")}kW · 기준 {data.usage.limit_kw.toLocaleString("ko-KR")}kW
                  </p>
                </AlarmCard>
                <AlarmCard title="전기 효율(역률)" {...data.power_factor} response={responseFor("power_factor", "역률", data.power_factor)}>
                  {data.power_factor.watched ? (
                    <p>
                      {data.power_factor.limit_pct}% 아래로 떨어질 가능성 <strong>{data.power_factor.probability_pct}%</strong>
                    </p>
                  ) : (
                    <p>이 시간에는 보지 않아요.</p>
                  )}
                  <p className="muted">
                    지금 {data.power_factor.now_pct}% · 기준 {data.power_factor.limit_pct}% (09~22시)
                  </p>
                </AlarmCard>
              </div>
              <div className="muted forecast-scores">
                <p>지난 채점 (9~12월, 이 모델):</p>
                <ul>
                  <ScoreNote name="사용량" score={data.scores.usage} />
                  <ScoreNote name="역률" score={data.scores.power_factor} />
                </ul>
                <p>예측은 확률이라 틀릴 수 있어요. 경보는 알리기만 하고 기계를 제어하지 않아요.</p>
              </div>
            </div>
          )}
        </AsyncPanel>
      )}

      {dateInRange && <AlarmTimeline date={date} time={time} onSelect={setTime} />}
    </section>
  );
}
