import { useEffect, useState } from "react";
import Anomalies from "./components/Anomalies.jsx";
import Briefing from "./components/Briefing.jsx";
import Chat from "./components/Chat.jsx";
import Dashboard from "./components/Dashboard.jsx";
import Forecast from "./components/Forecast.jsx";
import Handover from "./components/Handover.jsx";
import Manual from "./components/Manual.jsx";
import PowerFactorPlan from "./components/PowerFactorPlan.jsx";
import Replay from "./components/Replay.jsx";
import ResponseStats from "./components/ResponseStats.jsx";
import Report from "./components/Report.jsx";

export const DEFAULT_REPORT_DATE = "2018-12-16";

export const CATEGORIES = [
  { id: "overview", label: "개요" },
  { id: "day", label: "하루 보기" },
  { id: "handover", label: "교대 인수인계" },
  { id: "forecast", label: "경보 예측" },
  { id: "replay", label: "감시 재생" },
  { id: "power-factor", label: "역률 개선" },
  { id: "manual", label: "매뉴얼" },
];

/**
 * 주소의 #카테고리(예: #forecast)로 열 카테고리를 정한다. 새로고침해도 보던 곳이 유지된다.
 * 매뉴얼은 #manual:SOP-EN-02처럼 문서 번호를 함께 받는다. AI 답변과 인수인계서의 출처 링크가 이 주소를 쓴다.
 */
export function parseHash(hash = window.location.hash) {
  const [id, doc] = hash.replace(/^#/, "").split(":");
  return { id: CATEGORIES.some((c) => c.id === id) ? id : CATEGORIES[0].id, doc: doc || null };
}

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
  const [active, setActive] = useState(() => parseHash().id);
  const [doc, setDoc] = useState(() => parseHash().doc);
  // 대응 기록이 새로 저장될 때마다 늘려 통계를 다시 불러오게 한다.
  const [recorded, setRecorded] = useState(0);
  const onRecorded = () => setRecorded((n) => n + 1);

  useEffect(() => {
    const onHash = () => {
      const next = parseHash();
      setActive(next.id);
      if (next.doc) setDoc(next.doc);
      window.scrollTo?.({ top: 0 });
    };
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  const open = (id, hash = id) => {
    setActive(id);
    window.history.replaceState(null, "", `#${hash}`);
    window.scrollTo?.({ top: 0 });
  };
  const openDay = (next) => {
    setDate(next);
    open("day");
  };
  const openDoc = (next) => {
    setDoc(next);
    open("manual", `manual:${next}`);
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
            <Anomalies onSelect={openDay} />
          </Panel>
          <Panel id="day" active={active}>
            <Briefing date={date} />
            <Report date={date} onDateChange={setDate} />
          </Panel>
          <Panel id="handover" active={active}>
            <Handover />
          </Panel>
          <Panel id="forecast" active={active}>
            <Forecast onRecorded={onRecorded} />
            <ResponseStats version={recorded} />
          </Panel>
          <Panel id="replay" active={active}>
            <Replay onRecorded={onRecorded} />
            <ResponseStats version={recorded} />
          </Panel>
          <Panel id="power-factor" active={active}>
            <PowerFactorPlan />
          </Panel>
          <Panel id="manual" active={active}>
            <Manual docId={doc} onOpen={openDoc} />
          </Panel>
        </div>
        <Chat />
      </main>

      <footer className="footer">
        데이터:{" "}
        <a href="https://archive.ics.uci.edu/dataset/851/steel+industry+energy+consumption" rel="noopener">
          UCI Steel Industry Energy Consumption
        </a>{" "}
        (CC BY 4.0) · 매뉴얼은 시연용 예시 문서입니다.
      </footer>
    </>
  );
}
