import { useState } from "react";
import { getJson } from "../api.js";
import { useAsync } from "../useAsync.js";
import AsyncPanel from "./AsyncPanel.jsx";
import Chart from "./Chart.jsx";
import { MAX_DATE, MIN_DATE } from "./Report.jsx";

const TARGETS = [85, 90, 95];
export const DEFAULT_TARGET = 90;

function headline(data) {
  const auto = data.자동_조정기_추천;
  if (!auto) return `목표 ${data.목표_역률_pct}%는 300kVar 안에서는 맞출 수 없어요.`;
  if (auto.용량_kvar === 0) return `이 기간은 이미 목표 ${data.목표_역률_pct}%를 충분히 넘어서, 콘덴서를 더 달 필요가 없어요.`;
  return `목표 ${data.목표_역률_pct}%를 맞추려면 자동 역률 조정기 ${auto.용량_kvar}kVar(${auto.단계_kvar}kVar 단위)가 필요해요.`;
}

function Tile({ title, value, lines, tone }) {
  return (
    <div className={`kpi${tone ? ` tile-${tone}` : ""}`}>
      <span>{title}</span>
      <strong>{value}</strong>
      {lines.map((line) => (
        <small key={line}>{line}</small>
      ))}
    </div>
  );
}

/** 09~22시 역률을 목표까지 올리는 콘덴서 용량을 계산한다. 같은 용량의 고정 콘덴서와 비교해 과보상 위험을 보여 준다. */
export default function PowerFactorPlan() {
  const [start, setStart] = useState(MIN_DATE);
  const [end, setEnd] = useState(MAX_DATE);
  const [target, setTarget] = useState(DEFAULT_TARGET);
  const valid = Boolean(start && end) && start >= MIN_DATE && end <= MAX_DATE && start <= end;
  const state = useAsync(
    () => (valid ? getJson(`/api/power-factor-plan?start=${start}&end=${end}&target=${target}`) : Promise.resolve(null)),
    [start, end, target, valid],
  );

  return (
    <section className="card" aria-label="역률 개선 계산기">
      <div className="card-head">
        <h2>역률 개선 계산기 · 09~22시</h2>
        <div className="forecast-pickers">
          <input type="date" aria-label="시작 날짜" min={MIN_DATE} max={MAX_DATE} value={start} onChange={(e) => setStart(e.target.value)} />
          <input type="date" aria-label="끝 날짜" min={MIN_DATE} max={MAX_DATE} value={end} onChange={(e) => setEnd(e.target.value)} />
          <select aria-label="목표 역률" value={target} onChange={(e) => setTarget(Number(e.target.value))}>
            {TARGETS.map((t) => (
              <option key={t} value={t}>
                목표 {t}%
              </option>
            ))}
          </select>
        </div>
      </div>

      {!valid && (
        <p className="range-notice" role="alert">
          기간은 2018년 1월 1일부터 12월 31일 사이에서, 시작 날짜가 끝 날짜보다 늦지 않게 골라 주세요.
        </p>
      )}

      {valid && (
        <AsyncPanel state={state} title="역률 개선 계산을 불러오지 못했습니다.">
          {(data) => {
            const auto = data.자동_조정기_추천;
            const fixed = data.고정_콘덴서_비교;
            const fixedWorse = fixed && fixed.목표_미달_칸_비율_pct > data.현재.목표_미달_칸_비율_pct;
            return (
              <div className="forecast-body">
                <p className="briefing-action">
                  <strong>결론</strong> {headline(data)}
                </p>
                <div className="kpis pf-tiles">
                  <Tile
                    title="지금"
                    value={`미달 ${data.현재.목표_미달_칸_비율_pct}%`}
                    lines={[`평균 역률 ${data.현재.가중_평균_역률}%`, "목표보다 낮은 15분 칸 비율"]}
                  />
                  {auto && auto.용량_kvar > 0 && (
                    <Tile
                      title={`자동 조정기 ${auto.용량_kvar}kVar`}
                      value={`미달 ${auto.목표_미달_칸_비율_pct}%`}
                      lines={[`과보상 ${auto.과보상_칸_비율_pct}%`, "필요한 만큼만 단계를 켬"]}
                      tone="good"
                    />
                  )}
                  {fixed && (
                    <Tile
                      title={`고정 콘덴서 ${fixed.용량_kvar}kVar`}
                      value={`미달 ${fixed.목표_미달_칸_비율_pct}%`}
                      lines={[`과보상 ${fixed.과보상_칸_비율_pct}%`, "09~22시 늘 켜 둠"]}
                      tone={fixedWorse ? "bad" : undefined}
                    />
                  )}
                </div>
                {fixedWorse && (
                  <p className="range-notice" role="note">
                    같은 용량을 고정으로 달면 가벼운 부하일 때 과보상되어 오히려 미달이 늘어요. 자동 조정기를 권해요.
                  </p>
                )}
                <figure className="card pf-chart">
                  <figcaption>
                    시간대별 평균 부족 무효전력 <span>kVar</span>
                  </figcaption>
                  <div className="chart">
                    <Chart kind="bar" unit="kVar" label="시간대별 평균 부족 무효전력 막대 차트" rows={data.rows} />
                  </div>
                </figure>
                <p className="muted">{data.주의}</p>
              </div>
            );
          }}
        </AsyncPanel>
      )}
    </section>
  );
}
