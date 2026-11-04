import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, test, vi } from "vitest";
import { failed, ok, stubFetch } from "../test/helpers.jsx";
import Briefing from "./Briefing.jsx";

const briefing = (overrides = {}) => ({
  date: "2018-12-16",
  요일: "일",
  휴일: null,
  status: { level: "bad", label: "이상" },
  lines: ["전기를 2,015kWh 썼어요.", "가장 많이 쓴 때는 10:15예요.", "전기 효율(역률)은 91.68%로 좋아요."],
  action: "작업일지를 확인해 주세요.",
  source: "rule",
  ...overrides,
});
const aiBriefing = briefing({ lines: ["AI 첫 줄이에요.", "AI 둘째 줄이에요.", "AI 셋째 줄이에요."], source: "ai" });
const isQuick = (url) => url.endsWith("?ai=false");

afterEach(() => {
  vi.unstubAllGlobals();
});

test("질문하지 않아도 계산 요약이 먼저 보이고, AI 문장이 오면 바뀐다", async () => {
  let finishAi;
  const fetchMock = stubFetch((url) =>
    isQuick(url) ? Promise.resolve(ok(briefing())) : new Promise((resolve) => (finishAi = () => resolve(ok(aiBriefing)))),
  );
  render(<Briefing date="2018-12-16" />);

  expect(await screen.findByLabelText("상태: 이상")).toHaveTextContent("이상");
  expect(screen.getByText("전기를 2,015kWh 썼어요.")).toBeInTheDocument();
  expect(screen.getByText("작업일지를 확인해 주세요.")).toBeInTheDocument();
  expect(screen.getByRole("status")).toHaveTextContent("AI가 쉬운 말로 다듬는 중");
  expect(fetchMock).toHaveBeenCalledWith("/api/briefing/2018-12-16?ai=false");
  expect(fetchMock).toHaveBeenCalledWith("/api/briefing/2018-12-16");

  finishAi();
  expect(await screen.findByText("AI 첫 줄이에요.")).toBeInTheDocument();
  expect(screen.getAllByRole("listitem")).toHaveLength(3);
  expect(screen.getByRole("status")).toHaveTextContent("AI가 계산 결과를 쉬운 말로");
});

test("AI 요청이 실패해도 계산 요약은 그대로 남는다", async () => {
  stubFetch((url) => Promise.resolve(isQuick(url) ? ok(briefing()) : failed(500, "오류")));
  render(<Briefing date="2018-12-16" />);

  expect(await screen.findByText("계산 결과로 자동 정리했어요.")).toBeInTheDocument();
  expect(screen.getByText("전기를 2,015kWh 썼어요.")).toBeInTheDocument();
  expect(screen.queryByRole("alert")).toBeNull();
});

test("휴일 이름과 주의 색이 보인다", async () => {
  const holiday = briefing({ date: "2018-01-01", 요일: "월", 휴일: "신정", status: { level: "warn", label: "주의" } });
  stubFetch(() => Promise.resolve(ok(holiday)));
  const { container } = render(<Briefing date="2018-01-01" />);

  expect(await screen.findByText(/2018-01-01 \(월 · 신정\)/)).toBeInTheDocument();
  expect(container.querySelector(".level-warn")).not.toBeNull();
});

test("범위 밖 날짜에는 요약을 요청하지 않는다", () => {
  const fetchMock = stubFetch(() => Promise.resolve(ok(briefing())));
  const { container } = render(<Briefing date="2019-01-01" />);
  expect(container).toBeEmptyDOMElement();
  expect(fetchMock).not.toHaveBeenCalled();
});

test("모두 불러오지 못하면 이유와 다시 시도 버튼이 보인다", async () => {
  const fetchMock = stubFetch(() => Promise.resolve(failed(503, "데이터베이스에서 전력 데이터를 읽지 못했습니다.")));
  render(<Briefing date="2018-12-16" />);

  expect(await screen.findByRole("alert")).toHaveTextContent("요약을 불러오지 못했습니다.");
  fetchMock.mockImplementation(() => Promise.resolve(ok(briefing())));
  await userEvent.click(screen.getByRole("button", { name: "다시 시도" }));
  expect(await screen.findByLabelText("상태: 이상")).toBeInTheDocument();
});
