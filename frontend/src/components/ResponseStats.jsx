import { getJson } from "../api.js";
import { useAsync } from "../useAsync.js";
import AsyncPanel from "./AsyncPanel.jsx";
import Chart from "./Chart.jsx";

function TargetStats({ item }) {
  if (item.건수 === 0) return null;
  const rows = item.문턱_시뮬레이션.filter((row) => row.문턱_pct > item.현재_주의_문턱_pct);
  return (
    <div className="response-target">
      <p>
        <strong>{item.이름}</strong> 기록 {item.건수}건 · 조치함 {item.조치함} · 무시함 {item.무시함} · 오경보 {item.오경보}
        {item.오경보_비율_pct !== null && ` (조치함과 오경보 중 오경보가 ${item.오경보_비율_pct}%)`}
      </p>
      <div className="table-wrap">
        <table>
          <caption className="muted">주의 문턱(지금 {item.현재_주의_문턱_pct}%)을 올리면 이렇게 돼요</caption>
          <thead>
            <tr>
              <th>주의 문턱</th>
              <th className="num">사라지는 오경보</th>
              <th className="num">놓치는 조치</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.문턱_pct}>
                <td>{row.문턱_pct}%</td>
                <td className="num">{row.사라지는_오경보}번</td>
                <td className="num">{row.놓치는_조치}번</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

/** 작업자가 남긴 대응 기록의 통계. 기준은 사람이 정하므로 바꾸지 않고, 바꾸면 어떻게 되는지만 보여 준다. */
export default function ResponseStats({ version = 0 }) {
  const state = useAsync(() => getJson("/api/alarm-responses/stats"), [version]);

  return (
    <section className="card" aria-label="대응 기록 통계">
      <div className="card-head">
        <h2>대응 기록 통계</h2>
      </div>
      <AsyncPanel state={state} title="대응 기록을 불러오지 못했습니다.">
        {(data) =>
          data.총 === 0 ? (
            <p className="muted">아직 기록이 없어요. 경보가 켜졌을 때 "조치함", "무시함", "오경보였음"을 눌러 주세요.</p>
          ) : (
            <div className="forecast-body">
              {Object.entries(data.대상별).map(([target, item]) => (
                <TargetStats key={target} item={item} />
              ))}
              {data.시간대별_오경보.some((row) => row.value > 0) && (
                <figure className="card pf-chart">
                  <figcaption>
                    시간대별 오경보 기록 <span>번</span>
                  </figcaption>
                  <div className="chart">
                    <Chart kind="bar" unit="번" label="시간대별 오경보 기록 막대 차트" rows={data.시간대별_오경보} />
                  </div>
                </figure>
              )}
              <p className="muted">{data.주의}</p>
            </div>
          )
        }
      </AsyncPanel>
    </section>
  );
}
