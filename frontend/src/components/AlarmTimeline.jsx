import { getJson } from "../api.js";
import { useAsync } from "../useAsync.js";
import AsyncPanel from "./AsyncPanel.jsx";

const LEVEL_LABEL = { good: "좋음", warn: "주의", danger: "위험" };
const HOURS = [0, 3, 6, 9, 12, 15, 18, 21];

export function summaryText(name, s) {
  if (s.초과_구간 === 0) {
    return s.경보_횟수 === 0
      ? `${name}: 기준을 넘은 때도, 경보도 없었어요.`
      : `${name}: 기준을 넘은 때는 없었어요. 경보 ${s.경보_횟수}번은 모두 헛경보예요.`;
  }
  return (
    `${name}: 기준을 넘은 ${s.초과_구간}번 중 ${s.미리_잡은_비율_pct}%를 평균 ${Math.round(s.평균_몇분_전)}분 전에 미리 알렸어요. ` +
    `경보 ${s.경보_횟수}번 중 헛경보는 ${s.헛경보}번이에요.`
  );
}

/** 경보 띠. 칸을 누르면 그 시각을 '지금'으로 고른다. */
function AlarmStrip({ name, rows, levelKey, pctKey, describe, time, onSelect }) {
  return (
    <div className="tl-row">
      <span className="tl-name">{name}</span>
      <div className="tl-strip">
        {rows.map((row) => {
          const level = row[levelKey];
          const label = level
            ? `${row.time} · ${name} ${LEVEL_LABEL[level]} ${row[pctKey]}% · ${describe(row)}`
            : `${row.time} · ${name} 없음 · ${describe(row)}`;
          return (
            <button
              key={row.time}
              type="button"
              className={`tl-cell tl-${level ?? "none"}${row.time === time ? " tl-selected" : ""}`}
              title={label}
              aria-label={label}
              aria-pressed={row.time === time}
              disabled={!row.usage}
              onClick={() => onSelect(row.time)}
            />
          );
        })}
      </div>
    </div>
  );
}

/** 실제로 기준을 넘은 칸을 표시하는 띠 */
function ActualStrip({ name, rows, flagKey, describe, time }) {
  return (
    <div className="tl-row">
      <span className="tl-name">{name}</span>
      <div className="tl-strip" role="list" aria-label={name}>
        {rows.map((row) => (
          <span
            key={row.time}
            role="listitem"
            className={`tl-cell${row[flagKey] ? " tl-actual" : ""}${row.time === time ? " tl-selected" : ""}`}
            title={`${row.time} · ${describe(row)}${row[flagKey] ? " · 기준 넘음" : ""}`}
            aria-label={row[flagKey] ? `${row.time} 기준 넘음` : undefined}
          />
        ))}
      </div>
    </div>
  );
}

/** 하루 96칸의 경보와 실제로 기준을 넘은 때를 위아래로 맞춰 보여 준다. 경보가 몇 칸 먼저 켜졌는지 바로 보인다. */
export default function AlarmTimeline({ date, time, onSelect }) {
  const state = useAsync(() => getJson(`/api/forecast/day/${date}`), [date]);
  const kw = (row) => `실제 ${row.kw}kW`;
  const pf = (row) => `실제 역률 ${row.pf}%`;

  return (
    <div className="timeline" aria-label="하루 경보 타임라인">
      <h3>하루 경보 타임라인 · {date}</h3>
      <AsyncPanel state={state} title="타임라인을 불러오지 못했습니다.">
        {(data) => (
          <>
            <p className="muted">칸을 누르면 그 시각의 예측을 위에서 보여 줘요. 경보 칸이 실제 칸보다 앞에 있으면 미리 알린 거예요.</p>
            <ul className="tl-legend muted">
              <li><span className="tl-swatch tl-warn" />주의</li>
              <li><span className="tl-swatch tl-danger" />위험</li>
              <li><span className="tl-swatch tl-actual" />실제로 기준을 넘음</li>
              <li><span className="tl-swatch tl-none" />역률을 보지 않는 시간</li>
            </ul>
            <div className="tl-chart">
              <AlarmStrip name="사용량 경보" rows={data.rows} levelKey="usage" pctKey="usage_pct" describe={kw} time={time} onSelect={onSelect} />
              <ActualStrip name={`${data.limit_kw}kW 넘음`} rows={data.rows} flagKey="over" describe={kw} time={time} />
              <AlarmStrip name="역률 경보" rows={data.rows} levelKey="power_factor" pctKey="power_factor_pct" describe={pf} time={time} onSelect={onSelect} />
              <ActualStrip name={`${data.limit_pct}% 미만`} rows={data.rows} flagKey="under" describe={pf} time={time} />
              <div className="tl-row" aria-hidden="true">
                <span className="tl-name" />
                <div className="tl-hours">
                  {HOURS.map((h) => (
                    <span key={h}>{String(h).padStart(2, "0")}시</span>
                  ))}
                </div>
              </div>
            </div>
            <ul className="tl-summary">
              <li>{summaryText("사용량", data.summary.usage)}</li>
              <li>{summaryText("역률", data.summary.power_factor)}</li>
            </ul>
          </>
        )}
      </AsyncPanel>
    </div>
  );
}
