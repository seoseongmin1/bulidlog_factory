import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { chartMock, failed, ok, stubFetch } from "./test/helpers.jsx";
import App, { parseHash } from "./App.jsx";

vi.mock("./components/Chart.jsx", () => chartMock());

const anomalies = {
  items: [{ date: "2018-05-26", 요일: "토", 방향: "높음", usage_kwh: 1282, 기준선_kwh: 305, 편차_pct: 320.9, 휴일: null }],
};

beforeEach(() => {
  window.history.replaceState(null, "", "/");
  stubFetch((url) => Promise.resolve(url === "/api/anomalies" ? ok(anomalies) : failed(503, "테스트")));
});

afterEach(() => {
  vi.unstubAllGlobals();
});

const panel = (name) => screen.getByRole("tabpanel", { name });

test("처음에는 개요만 보이고, 탭을 누르면 그 카테고리만 보인다", async () => {
  render(<App />);
  expect(screen.getByRole("tab", { name: "개요" })).toHaveAttribute("aria-selected", "true");
  expect(panel("개요")).toBeVisible();
  expect(document.getElementById("panel-forecast")).not.toBeVisible();

  await userEvent.click(screen.getByRole("tab", { name: "경보 예측" }));
  expect(screen.getByRole("tab", { name: "경보 예측" })).toHaveAttribute("aria-selected", "true");
  expect(panel("경보 예측")).toBeVisible();
  expect(document.getElementById("panel-overview")).not.toBeVisible();
  expect(window.location.hash).toBe("#forecast");
  // AI 대화는 어느 카테고리에서든 보인다.
  expect(screen.getByRole("complementary", { name: "에너지 에이전트" })).toBeVisible();
});

test("주소의 #카테고리로 열 수 있다", () => {
  window.history.replaceState(null, "", "/#power-factor");
  render(<App />);
  expect(screen.getByRole("tab", { name: "역률 개선" })).toHaveAttribute("aria-selected", "true");
});

test("개요의 이상한 날에서 날짜를 누르면 하루 보기로 옮겨 그 날짜를 연다", async () => {
  render(<App />);
  await userEvent.click(await within(panel("개요")).findByRole("button", { name: /2018-05-26/ }));

  expect(screen.getByRole("tab", { name: "하루 보기" })).toHaveAttribute("aria-selected", "true");
  expect(within(panel("하루 보기")).getByLabelText("보고서 날짜")).toHaveValue("2018-05-26");
});

test("탭은 일곱 개이고 '내일 예측'과 따로 있던 '이상한 날' 탭은 없다", () => {
  render(<App />);
  const names = screen.getAllByRole("tab").map((tab) => tab.textContent);
  expect(names).toEqual(["개요", "하루 보기", "교대 인수인계", "경보 예측", "감시 재생", "역률 개선", "매뉴얼"]);
});

test("#manual:문서번호 주소로 매뉴얼 탭을 열고, 출처 링크를 누르면 그 탭으로 옮긴다", async () => {
  expect(parseHash("#manual:SOP-EN-02")).toEqual({ id: "manual", doc: "SOP-EN-02" });
  expect(parseHash("#없는곳")).toEqual({ id: "overview", doc: null });

  render(<App />);
  window.location.hash = "manual:SOP-EN-02";
  window.dispatchEvent(new HashChangeEvent("hashchange"));
  await vi.waitFor(() => expect(screen.getByRole("tab", { name: "매뉴얼" })).toHaveAttribute("aria-selected", "true"));
});
