import { useEffect, useState } from "react";
import { getJson } from "../api.js";
import { eventText, replayEvents } from "../replay.js";
import { useAsync } from "../useAsync.js";
import { summaryText } from "./AlarmTimeline.jsx";
import AsyncPanel from "./AsyncPanel.jsx";
import Chart from "./Chart.jsx";
import { DEFAULT_FORECAST_DATE } from "./Forecast.jsx";
import ResponseButtons from "./ResponseButtons.jsx";
import { MAX_DATE, MIN_DATE } from "./Report.jsx";
import { useAlarmResponses } from "../responses.js";

// 한 칸(15분)을 보여 주는 시간(밀리초)
const SPEEDS = { slow: { label: "느리게", ms: 1000 }, normal: { label: "보통", ms: 500 }, fast: { label: "빠르게", ms: 150 } };
const LEVEL_CLASS = { good: "level-good", warn: "level-warn", danger: "level-bad" };
const LEVEL_LABEL = { good: "좋음", warn: "주의", danger: "위험" };

function NowCard({ title, level, children, action }) {
  return (
    <div className={`alarm-card ${LEVEL_CLASS[level ?? "good"]}`}>
      <div className="card-head">
        <h3>{title}</h3>
        <strong className="status-badge" aria-label={`${title}: ${level ? LEVEL_LABEL[level] : "보지 않음"}`}>
          {level ? LEVEL_LABEL[level] : "좋음"}
        </strong>
      </div>
      {children}
      <p className="briefing-action">
        <strong>할 일</strong> {action}
      </p>
    </div>
  );
}

function Player({ data, onRecorded }) {
  const last = data.rows.length - 1;
  const [index, setIndex] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [speed, setSpeed] = useState("normal");
  const { saved, save } = useAlarmResponses(data.date, onRecorded);

  useEffect(() => {
    if (!playing) return undefined;
    const timer = setInterval(() => setIndex((i) => Math.min(i + 1, last)), SPEEDS[speed].ms);
    return () => clearInterval(timer);
  }, [playing, speed, last]);
  useEffect(() => {
    if (index >= last) setPlaying(false);
  }, [index, last]);

  const row = data.rows[index];
  const events = replayEvents(data.rows, index);
  const finished = index >= last;
  const play = () => {
    if (finished) setIndex(0);
    setPlaying((p) => !p);
  };
  const chartRows = data.rows.map((r, i) => ({ label: r.time, value: i <= index ? r.kw : null }));
  const pfAction = row.power_factor ? data.actions.power_factor[row.power_factor] : data.actions.power_factor.unwatched;

  return (
    <div className="forecast-body">
      <div className="replay-bar">
        <p className="replay-clock" aria-live="off">
          지금 <strong>{row.time}</strong>
        </p>
        <button type="button" className="primary" onClick={play}>
          {playing ? "멈춤" : finished ? "처음부터 다시" : "재생"}
        </button>
        <select aria-label="재생 속도" value={speed} onChange={(event) => setSpeed(event.target.value)}>
          {Object.entries(SPEEDS).map(([id, s]) => (
            <option key={id} value={id}>
              {s.label}
            </option>
          ))}
        </select>
        <input
          type="range"
          aria-label="시각 이동"
          min={0}
          max={last}
          value={index}
          onChange={(event) => {
            setPlaying(false);
            setIndex(Number(event.target.value));
          }}
        />
      </div>

      <div className="alarm-grid">
        <NowCard title="사용량" level={row.usage} action={data.actions.usage[row.usage ?? "good"]}>
          <p>
            기준을 넘을 가능성 <strong>{row.usage_pct ?? 0}%</strong>
          </p>
          <p className="muted">
            지금 {row.kw.toLocaleString("ko-KR")}kW · 기준 {data.limit_kw.toLocaleString("ko-KR")}kW
          </p>
        </NowCard>
        <NowCard title="전기 효율(역률)" level={row.power_factor} action={pfAction}>
          {row.power_factor ? (
            <p>
              {data.limit_pct}% 아래로 떨어질 가능성 <strong>{row.power_factor_pct}%</strong>
            </p>
          ) : (
            <p>이 시간에는 보지 않아요.</p>
          )}
          <p className="muted">
            지금 {row.pf}% · 기준 {data.limit_pct}% (09~22시)
          </p>
        </NowCard>
      </div>

      <figure className="card pf-chart">
        <figcaption>
          지금까지의 15분 사용량 <span>kW</span>
        </figcaption>
        <div className="chart">
          <Chart kind="line" unit="kW" label="재생 중인 15분 사용량 선 차트" rows={chartRows} suggestedMax={Math.ceil(data.limit_kw * 1.1)} />
        </div>
      </figure>

      <div className="replay-log">
        <h3>알림</h3>
        {events.length === 0 ? (
          <p className="muted">아직 알림이 없어요.</p>
        ) : (
          <ol aria-live="polite">
            {[...events].reverse().map((event) => (
              <li key={`${event.type}-${event.target}-${event.index}`} className={`replay-${event.type}`}>
                <time>{event.time}</time>
                {eventText(event)}
                {event.type === "alarm" && (
                  <ResponseButtons
                    alarm={{
                      time: event.time,
                      target: event.target,
                      name: event.name,
                      level: event.level,
                      probability_pct: data.rows[event.index][`${event.target}_pct`],
                    }}
                    saved={saved[`${event.time}|${event.target}`]}
                    onSave={save}
                  />
                )}
              </li>
            ))}
          </ol>
        )}
      </div>

      {finished && (
        <ul className="tl-summary">
          <li>{summaryText("사용량", data.summary.usage)}</li>
          <li>{summaryText("역률", data.summary.power_factor)}</li>
        </ul>
      )}
    </div>
  );
}

/** 하루를 15분씩 되감아 '그때 이 시스템이 있었다면' 어떻게 알렸을지 보여 준다. 실시간 데이터가 아니라 기록을 흘려보내는 재생이다. */
export default function Replay({ onRecorded }) {
  const [date, setDate] = useState(DEFAULT_FORECAST_DATE);
  const dateInRange = Boolean(date) && date >= MIN_DATE && date <= MAX_DATE;
  const state = useAsync(() => (dateInRange ? getJson(`/api/forecast/day/${date}`) : Promise.resolve(null)), [date, dateInRange]);

  return (
    <section className="card" aria-label="감시 재생">
      <div className="card-head">
        <h2>감시 재생 · 하루를 흘려보기</h2>
        <div className="forecast-pickers">
          <input type="date" aria-label="재생 날짜" min={MIN_DATE} max={MAX_DATE} value={date} onChange={(e) => setDate(e.target.value)} />
        </div>
      </div>
      <p className="muted">실시간 데이터가 아니라, 2018년 기록을 15분씩 흘려보내는 재생이에요. 경보는 알리기만 하고 기계를 제어하지 않아요.</p>

      {!dateInRange && (
        <p className="range-notice" role="alert">
          고를 수 있는 날짜는 2018년 1월 1일부터 12월 31일까지입니다.
        </p>
      )}

      {dateInRange && (
        <AsyncPanel state={state} title="재생할 하루를 불러오지 못했습니다.">
          {(data) => (
            <>
              {data.learned_period && (
                <p className="range-notice" role="note">
                  학습에 쓴 기간이라 실제보다 잘 맞아 보일 수 있어요.
                </p>
              )}
              <Player key={data.date} data={data} onRecorded={onRecorded} />
            </>
          )}
        </AsyncPanel>
      )}
    </section>
  );
}
