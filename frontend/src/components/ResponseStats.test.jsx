import { render, screen } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { chartMock, failed, ok, stubFetch } from "../test/helpers.jsx";
import ResponseStats from "./ResponseStats.jsx";

vi.mock("./Chart.jsx", () => chartMock());

const target = (name, overrides = {}) => ({
  이름: name,
  건수: 0,
  조치함: 0,
  무시함: 0,
  오경보: 0,
  오경보_비율_pct: null,
  현재_주의_문턱_pct: 9,
  문턱_시뮬레이션: [10, 20, 30, 40, 50].map((pct) => ({ 문턱_pct: pct, 사라지는_오경보: pct === 10 ? 0 : 2, 놓치는_조치: pct === 50 ? 1 : 0 })),
  ...overrides,
});
const stats = (overrides = {}) => ({
  총: 5,
  대상별: {
    usage: target("사용량", { 건수: 5, 조치함: 2, 무시함: 1, 오경보: 2, 오경보_비율_pct: 50 }),
    power_factor: target("역률"),
  },
  시간대별_오경보: [
    { label: "09시", value: 1 },
    { label: "10시", value: 1 },
  ],
  주의: "경보 기준은 자동으로 바뀌지 않아요.",
  ...overrides,
});

afterEach(() => {
  vi.unstubAllGlobals();
});

test("기록이 없으면 누르는 방법을 안내한다", async () => {
  stubFetch(() => Promise.resolve(ok(stats({ 총: 0, 시간대별_오경보: [] }))));
  render(<ResponseStats />);
  expect(await screen.findByText(/아직 기록이 없어요/)).toBeInTheDocument();
  expect(screen.queryByTestId("chart")).toBeNull();
});

test("대상별 응답 수, 문턱을 올렸을 때의 변화, 시간대별 오경보 그래프를 보여 준다", async () => {
  stubFetch(() => Promise.resolve(ok(stats())));
  render(<ResponseStats />);
  expect(await screen.findByText(/기록 5건 · 조치함 2 · 무시함 1 · 오경보 2/)).toHaveTextContent("오경보가 50%");
  expect(screen.getByText("주의 문턱(지금 9%)을 올리면 이렇게 돼요")).toBeInTheDocument();
  // 지금 문턱보다 높은 후보만 보인다.
  expect(screen.getAllByRole("row")).toHaveLength(1 + 5);
  expect(screen.getByRole("cell", { name: "50%" }).closest("tr")).toHaveTextContent("2번1번");
  expect(screen.getByTestId("chart")).toHaveAttribute("data-points", "2");
  // 기록이 없는 대상은 나오지 않는다.
  expect(screen.queryByText(/역률/)).toBeNull();
});

test("불러오지 못하면 이유와 다시 시도 버튼이 보인다", async () => {
  stubFetch(() => Promise.resolve(failed(503, "서버 오류")));
  render(<ResponseStats />);
  expect(await screen.findByRole("alert")).toHaveTextContent("대응 기록을 불러오지 못했습니다.");
  expect(screen.getByRole("button", { name: "다시 시도" })).toBeInTheDocument();
});
