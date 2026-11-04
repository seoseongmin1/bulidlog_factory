import { useEffect, useRef, useState } from "react";
import { getJson } from "../api.js";
import { useAsync } from "../useAsync.js";
import AsyncPanel, { ErrorNotice } from "./AsyncPanel.jsx";

export const EXAMPLES = ["역률 경보가 뜨면 뭐 해야 돼?", "큰 설비 켜는 순서", "일요일 특근 신고", "kVarh가 뭐야"];

function Section({ heading, text }) {
  return (
    <div className="manual-section">
      <h4>{heading}</h4>
      {text.split("\n").map((line, i) => (
        <p key={i}>{line}</p>
      ))}
    </div>
  );
}

function Results({ result, onOpen }) {
  if (result.results.length === 0) {
    return <p className="muted">관련된 매뉴얼을 찾지 못했어요. 이럴 때 AI 에이전트도 절차를 지어내지 않고 "매뉴얼에 없다"고 답해요.</p>;
  }
  return (
    <ol className="manual-results">
      {result.results.map((r) => (
        <li key={`${r.doc_id}-${r.section}`}>
          <div className="manual-result-head">
            <button type="button" className="date-button" onClick={() => onOpen(r.doc_id)}>
              {r.cite}
            </button>
            <span className="score" title="질문과 비슷한 정도 (TF-IDF 코사인 유사도)">
              <span className="score-track">
                <span className="score-bar" style={{ width: `${Math.min(100, Math.round(r.score * 200))}%` }} />
              </span>
              {r.score.toFixed(2)}
            </span>
          </div>
          <p className="muted">{r.title}</p>
          <Section heading={r.section} text={r.text} />
        </li>
      ))}
    </ol>
  );
}

/** 현장 매뉴얼 검색과 문서 보기. AI 없이 검색만 하므로 빠르고, 같은 질문에는 늘 같은 결과가 나온다. */
export default function Manual({ docId, onOpen }) {
  const docs = useAsync(() => getJson("/api/manual/docs"), []);
  const selected = docs.data?.items?.some((d) => d.doc_id === docId) ? docId : docs.data?.items?.[0]?.doc_id;
  const doc = useAsync(() => (selected ? getJson(`/api/manual/docs/${selected}`) : Promise.resolve(null)), [selected]);
  const [query, setQuery] = useState("");
  const [search, setSearch] = useState({ status: "idle" });
  const viewer = useRef(null);

  useEffect(() => {
    if (docId && viewer.current) viewer.current.scrollIntoView?.({ block: "start" });
  }, [docId]);

  const run = async (text) => {
    const q = text.trim();
    if (!q) return;
    setQuery(q);
    setSearch({ status: "loading" });
    try {
      setSearch({ status: "ready", data: await getJson(`/api/manual/search?q=${encodeURIComponent(q)}`) });
    } catch (error) {
      setSearch({ status: "error", error });
    }
  };

  return (
    <>
      <section className="card" aria-label="매뉴얼 검색">
        <div className="card-head">
          <h2>현장 매뉴얼 검색</h2>
          <span>RAG 검색 단계 · AI 에이전트와 경보 카드가 같은 검색을 써요</span>
        </div>
        <form
          className="manual-search"
          onSubmit={(event) => {
            event.preventDefault();
            run(query);
          }}
        >
          <input type="search" aria-label="매뉴얼 검색어" placeholder="예: 역률 경보가 뜨면 뭐 해야 돼?" value={query} onChange={(e) => setQuery(e.target.value)} />
          <button type="submit" className="save-button">
            찾기
          </button>
        </form>
        <div className="chips">
          {EXAMPLES.map((q) => (
            <button key={q} type="button" onClick={() => run(q)}>
              {q}
            </button>
          ))}
        </div>
        {search.status === "loading" && (
          <p className="muted" role="status">
            찾는 중…
          </p>
        )}
        {search.status === "error" && <ErrorNotice title="검색하지 못했습니다." message={search.error.message} onRetry={() => run(query)} />}
        {search.status === "ready" && <Results result={search.data} onOpen={onOpen} />}
      </section>

      <section className="card manual-docs" aria-label="매뉴얼 문서" ref={viewer}>
        <AsyncPanel state={docs} title="매뉴얼 목록을 불러오지 못했습니다.">
          {(list) => (
            <div className="manual-layout">
              <nav aria-label="문서 목록">
                <p className="range-notice">{list.notice}</p>
                <ul>
                  {list.items.map((d) => (
                    <li key={d.doc_id}>
                      <button type="button" aria-current={d.doc_id === selected ? "true" : undefined} onClick={() => onOpen(d.doc_id)}>
                        <small>{d.doc_id}</small>
                        {d.title}
                      </button>
                    </li>
                  ))}
                </ul>
              </nav>
              <AsyncPanel state={doc} title="문서를 불러오지 못했습니다.">
                {(d) =>
                  d && (
                    <article className="manual-doc">
                      <h3>{d.title}</h3>
                      <p className="muted">{d.meta}</p>
                      {d.sections.map((s) => (
                        <Section key={s.heading} heading={s.heading} text={s.text} />
                      ))}
                    </article>
                  )
                }
              </AsyncPanel>
            </div>
          )}
        </AsyncPanel>
      </section>
    </>
  );
}
