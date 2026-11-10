import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, test, vi } from "vitest";
import { chartMock, failed, ok, stubFetch } from "../test/helpers.jsx";
import DayAhead from "./DayAhead.jsx";

vi.mock("./Chart.jsx", () => chartMock());

const TIMES = Array.from({ length: 96 }, (_, i) => `${String(Math.floor(i / 4)).padStart(2, "0")}:${String((i % 4) * 15).padStart(2, "0")}`);
const block = (index, level, over = false) => {
  const start = index * 2;
  const pad = (n) => String(n).padStart(2, "0");
  return {
    start: `${pad(start)}:00`,
    end: `${pad(start + 2)}:00`,
    level,
    label: { good: "좋음", warn: "주의", danger: "위험" }[level],
    exceeded_days: level === "good" ? 0 : 3,
    compared_days: 20,
    over,
    actual_max_kw: over ? 450.2 : 120,
  };
};
const grade = (overrides = {}) => ({ 초과_구간: 144, 미리_잡은_비율_pct: 92.4, 하루_켜진_구간: 4.59, 하루_헛_구간: 3.5, 켜진_구간_중_실제_pct: 23.8, 구간_켜진_비율_pct: 38.3, ...overrides });
const dayAhead = (overrides = {}) => ({
  date: "2018-10-10",
  issued_at: "2018-10-09 23:45",
  learned_period: false,
  limit_kw: 408.1,
  day_type: "평일",
  blocks: Array.from({ length: 12 }, (_, i) => (i === 7 ? block(i, "danger", true) : i === 8 ? block(i, "warn") : block(i, "good"))),
  plan: ["14:00~16:00 위험: 큰 설비를 한꺼번에 켜지 않도록 가동 순서를 미리 나눠 두세요.", "16:00~18:00 주의: 이 시간 전에 큰 설비의 가동 계획을 확인해 주세요."],
  same_day_note: "당일에는 경보 예측 탭이 1시간 전에 다시 알려 줘요.",
  result: { 위험_구간: 2, 실제_초과_구간: 1, 맞힌_구간: 1, 놓친_구간: 0, 헛_구간: 1 },
  rows: TIMES.map((time) => ({ time, usual_kw: 100, kw: 100 })),
  scores: { 아무_구간이나_초과_pct: 9.8, results: { freq_warn: grade(), freq_danger: grade(), naive: grade() } },
  ...overrides,
});

afterEach(() => {
  vi.unstubAllGlobals();
});

test("준비할 일, 2시간 구간별 위험과 실제 결과, 평소 사용량 그래프, 채점이 보인다", async () => {
  const fetchMock = stubFetch(() => Promise.resolve(ok(dayAhead())));
  render(<DayAhead />);

  expect(await screen.findByText(/2018-10-09 23:45까지의 데이터로 2018-10-10\(평일\)/)).toBeInTheDocument();
  expect(fetchMock).toHaveBeenCalledWith("/api/day-ahead/2018-10-10");
  const plan = screen.getByText("준비할 일").closest(".plan-box");
  expect(within(plan).getByText(/14:00~16:00 위험: 큰 설비를 한꺼번에/)).toBeInTheDocument();
  expect(within(plan).getByText("당일에는 경보 예측 탭이 1시간 전에 다시 알려 줘요.")).toBeInTheDocument();

  expect(screen.getByLabelText("14:00~16:00: 위험")).toBeInTheDocument();
  expect(screen.getByLabelText("16:00~18:00: 주의")).toBeInTheDocument();
  expect(screen.getAllByText("실제로 넘음 (최고 450.2kW)")).toHaveLength(1);
  expect(screen.getAllByText("지난 같은 날 20일 중 3일 넘음")).toHaveLength(2);
  expect(screen.getByText(/위험으로 본 2구간 중 1구간이 실제로 넘었어요. 놓친 구간은 0개, 헛경보는 1개예요/)).toBeInTheDocument();
  expect(screen.getByTestId("chart")).toHaveAttribute("data-points", "96");
  expect(screen.getByText(/실제로 넘은 144구간 중 92.4%를 포함했어요/)).toBeInTheDocument();
  expect(screen.getByText(/켜진 구간 중 실제로 넘은 것은 23.8%예요. 아무 구간이나 고르면 9.8%가 넘어요/)).toBeInTheDocument();
  expect(screen.queryByText(/학습에 쓴 기간/)).toBeNull();
});

test("날짜를 바꾸면 다시 예측하고, 학습 기간 안내를 보여 준다", async () => {
  const fetchMock = stubFetch((url) => Promise.resolve(ok(dayAhead({ date: url.slice(-10), learned_period: url.endsWith("2018-03-14") }))));
  render(<DayAhead />);
  await screen.findByLabelText("14:00~16:00: 위험");

  const input = screen.getByLabelText("예측할 날");
  await userEvent.clear(input);
  await userEvent.type(input, "2018-03-14");
  expect(fetchMock).toHaveBeenLastCalledWith("/api/day-ahead/2018-03-14");
  expect(await screen.findByText("학습에 쓴 기간이라 실제보다 잘 맞아 보일 수 있어요.")).toBeInTheDocument();
});

test("준비할 시간대가 없는 날은 그렇게 알려 준다", async () => {
  stubFetch(() =>
    Promise.resolve(
      ok(dayAhead({ day_type: "일요일·공휴일", blocks: Array.from({ length: 12 }, (_, i) => block(i, "good")), plan: ["기준을 넘은 적이 잦은 시간대가 없어요. 따로 준비할 일은 없어요."], result: { 위험_구간: 0, 실제_초과_구간: 0, 맞힌_구간: 0, 놓친_구간: 0, 헛_구간: 0 } })),
    ),
  );
  render(<DayAhead />);
  expect(await screen.findByText("기준을 넘은 적이 잦은 시간대가 없어요. 따로 준비할 일은 없어요.")).toBeInTheDocument();
  expect(screen.queryByText("위험", { selector: "strong" })).toBeNull();
});

test("앞의 일주일이나 범위 밖 날짜는 요청하지 않고 안내하며, 불러오지 못하면 다시 시도 버튼이 보인다", async () => {
  const fetchMock = stubFetch(() => Promise.resolve(failed(503, "데이터베이스에서 전력 데이터를 읽지 못했습니다.")));
  render(<DayAhead />);
  expect(await screen.findByRole("alert")).toHaveTextContent("내일 예측을 불러오지 못했습니다.");
  expect(screen.getByRole("button", { name: "다시 시도" })).toBeInTheDocument();

  fetchMock.mockClear();
  const input = screen.getByLabelText("예측할 날");
  await userEvent.clear(input);
  await userEvent.type(input, "2018-01-03");
  expect(screen.getByRole("alert")).toHaveTextContent("1월 8일부터 12월 31일까지");
  expect(fetchMock).not.toHaveBeenCalled();
});
