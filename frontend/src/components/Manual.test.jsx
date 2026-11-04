import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, test, vi } from "vitest";
import { ok, stubFetch } from "../test/helpers.jsx";
import Manual, { EXAMPLES } from "./Manual.jsx";

afterEach(() => vi.unstubAllGlobals());

const docs = {
  notice: "예시 문서입니다.",
  items: [
    { doc_id: "SOP-EN-01", title: "수요전력 초과 경보 대응 절차", meta: "", sections: ["1. 목적"] },
    { doc_id: "SOP-EN-02", title: "역률 저하 경보 대응과 콘덴서 점검", meta: "", sections: ["2. 경보가 켜지면"] },
  ],
};
const doc = (id) => ({ doc_id: id, title: docs.items.find((d) => d.doc_id === id).title, meta: "예시", sections: [{ heading: "1. 목적", text: "본문입니다." }] });
const found = {
  query: "역률",
  찾지_못함: false,
  results: [{ doc_id: "SOP-EN-02", title: "역률 저하 경보 대응과 콘덴서 점검", section: "2. 경보가 켜지면", text: "1. 단계를 확인합니다.", score: 0.31, cite: "[SOP-EN-02 2. 경보가 켜지면]" }],
};

function server(searchResult = found) {
  return stubFetch((url) => {
    if (url === "/api/manual/docs") return Promise.resolve(ok(docs));
    if (url.startsWith("/api/manual/docs/")) return Promise.resolve(ok(doc(url.split("/").pop())));
    return Promise.resolve(ok(searchResult));
  });
}

test("예시 질문을 누르면 검색 결과와 출처, 점수를 보여 주고, 출처를 누르면 그 문서를 연다", async () => {
  const fetchMock = server();
  const onOpen = vi.fn();
  render(<Manual docId={null} onOpen={onOpen} />);
  expect(await screen.findByRole("heading", { name: "수요전력 초과 경보 대응 절차" })).toBeInTheDocument();

  await userEvent.click(screen.getByRole("button", { name: EXAMPLES[0] }));
  expect(fetchMock).toHaveBeenCalledWith(`/api/manual/search?q=${encodeURIComponent(EXAMPLES[0])}`);
  await userEvent.click(await screen.findByRole("button", { name: "[SOP-EN-02 2. 경보가 켜지면]" }));
  expect(onOpen).toHaveBeenCalledWith("SOP-EN-02");
  expect(screen.getByText("0.31")).toBeInTheDocument();
});

test("문서 번호가 주어지면 그 문서를 보여 준다", async () => {
  server();
  render(<Manual docId="SOP-EN-02" onOpen={() => {}} />);
  expect(await screen.findByRole("heading", { name: "역률 저하 경보 대응과 콘덴서 점검" })).toBeInTheDocument();
});

test("관련 문서가 없으면 지어내지 않는다고 알려 준다", async () => {
  server({ query: "점심", 찾지_못함: true, results: [] });
  render(<Manual docId={null} onOpen={() => {}} />);
  await userEvent.type(screen.getByLabelText("매뉴얼 검색어"), "점심 메뉴{enter}");
  expect(await screen.findByText(/관련된 매뉴얼을 찾지 못했어요/)).toBeInTheDocument();
});
