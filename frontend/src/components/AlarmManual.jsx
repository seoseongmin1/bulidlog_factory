import { useState } from "react";
import { getJson } from "../api.js";
import { ErrorNotice } from "./AsyncPanel.jsx";

/** 경보 카드 아래의 '조치 매뉴얼'. 펼칠 때만 불러온다. 같은 경보에는 늘 같은 절이 나온다(AI 없이 검색만). */
export default function AlarmManual({ target }) {
  const [state, setState] = useState({ status: "idle" });
  const load = async () => {
    if (state.status === "ready" || state.status === "loading") return;
    setState({ status: "loading" });
    try {
      setState({ status: "ready", data: await getJson(`/api/manual/alarm/${target}`) });
    } catch (error) {
      setState({ status: "error", error });
    }
  };
  const top = state.data?.results?.[0];

  return (
    <details className="alarm-manual" onToggle={(event) => event.currentTarget.open && load()}>
      <summary>조치 매뉴얼 보기</summary>
      {state.status === "loading" && <p className="muted">찾는 중…</p>}
      {state.status === "error" && <ErrorNotice title="매뉴얼을 불러오지 못했습니다." message={state.error.message} />}
      {top && (
        <div className="manual-section">
          <a className="cite" href={`#manual:${top.doc_id}`}>
            {top.cite} {top.title}
          </a>
          {top.text.split("\n").map((line, i) => (
            <p key={i}>{line}</p>
          ))}
        </div>
      )}
    </details>
  );
}
