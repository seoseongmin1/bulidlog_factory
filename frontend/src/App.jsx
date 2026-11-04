import { useState } from "react";
import Anomalies from "./components/Anomalies.jsx";
import Briefing from "./components/Briefing.jsx";
import Chat from "./components/Chat.jsx";
import Dashboard from "./components/Dashboard.jsx";
import DayAhead from "./components/DayAhead.jsx";
import Forecast from "./components/Forecast.jsx";
import PowerFactorPlan from "./components/PowerFactorPlan.jsx";
import Replay from "./components/Replay.jsx";
import ResponseStats from "./components/ResponseStats.jsx";
import Report from "./components/Report.jsx";

export const DEFAULT_REPORT_DATE = "2018-12-16";

export const CATEGORIES = [
  { id: "overview", label: "개요" },
  { id: "day", label: "하루 보기" },
  { id: "anomalies", label: "이상한 날" },
  { id: "forecast", label: "경보 예측" },
  { id: "tomorrow", label: "내일 예측" },
  { id: "replay", label: "감시 재생" },
  { id: "power-factor", label: "역률 개선" },
];

/** 주소의 #카테고리(예: #forecast)로 처음 열 카테고리를 정한다. 새로고침해도 보던 곳이 유지된다. */
const categoryFromHash = () => {
  const id = window.location.hash.slice(1);
  return CATEGORIES.some((c) => c.id === id) ? id : CATEGORIES[0].id;
};

/** 카테고리 하나의 화면. 다른 카테고리로 옮겨도 입력한 날짜 등이 남도록 지우지 않고 숨기기만 한다. */
function Panel({ id, active, children }) {
  return (
    <div className="panel" role="tabpanel" id={`panel-${id}`} aria-labelledby={`tab-${id}`} hidden={active !== id}>
      {children}
    </div>
  );
}

export default function App() {
  const [date, setDate] = useState(DEFAULT_REPORT_DATE);
  const [active, setActive] = useState(categoryFromHash);
  // 대응 기록이 새로 저장될 때마다 늘려 통계를 다시 불러오게 한다.
  const [recorded, setRecorded] = useState(0);
  const onRecorded = () => setRecorded((n) => n + 1);

  const open = (id) => {
    setActive(id);
    window.history.replaceState(null, "", `#${id}`);
    window.scrollTo?.({ top: 0 });
  };
  const openDay = (next) => {
    setDate(next);
    open("day");
  };

  return (
    <>
      <header className="topbar">
        <div className="brand">
          <h1>공장 에너지 에이전트</h1>
          <p>대우스틸 광양공장 · 2018년 15분 간격 35,040건</p>
        </div>
        <nav className="tabs" role="tablist" aria-label="카테고리">
          {CATEGORIES.map((c) => (
            <button
              key={c.id}
              type="button"
              role="tab"
              id={`tab-${c.id}`}
              aria-controls={`panel-${c.id}`}
              aria-selected={active === c.id}
              className="tab"
              onClick={() => open(c.id)}
            >
              {c.label}
            </button>
          ))}
        </nav>
      </header>

      <main className="layout">
        <div className="left">
          <Panel id="overview" active={active}>
            <Dashboard />
          </Panel>
          <Panel id="day" active={active}>
            <Briefing date={date} />
            <Report date={date} onDateChange={setDate} />
          </Panel>
          <Panel id="anomalies" active={active}>
            <Anomalies onSelect={openDay} />
          </Panel>
          <Panel id="forecast" active={active}>
            <Forecast onRecorded={onRecorded} />
            <ResponseStats version={recorded} />
          </Panel>
          <Panel id="tomorrow" active={active}>
            <DayAhead />
          </Panel>
          <Panel id="replay" active={active}>
            <Replay onRecorded={onRecorded} />
            <ResponseStats version={recorded} />
          </Panel>
          <Panel id="power-factor" active={active}>
            <PowerFactorPlan />
          </Panel>
        </div>
        <Chat />
      </main>

      <footer className="footer">
        데이터:{" "}
        <a href="https://archive.ics.uci.edu/dataset/851/steel+industry+energy+consumption" rel="noopener">
          UCI Steel Industry Energy Consumption
        </a>{" "}
        (CC BY 4.0)
      </footer>
    </>
  );
}
