import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, test, vi } from "vitest";
import { failed, ok, stubFetch } from "../test/helpers.jsx";
import { timeline } from "../test/timeline.js";
import Forecast from "./Forecast.jsx";

const score = (overrides = {}) => ({ 초과_구간: 221, 미리_잡은_비율_pct: 100, 평균_몇분_전: 51.4, 하루_헛경보: 2.06, ...overrides });
const forecast = (overrides = {}) => ({
  time: "2018-10-10 13:00",
  until: "2018-10-10 14:00",
  learned_period: false,
  usage: { level: "danger", label: "위험", probability_pct: 57, now_kw: 252.56, limit_kw: 408.1, action: "큰 설비를 한꺼번에 켜지 마세요." },
  power_factor: {
    level: "good",
    label: "좋음",
    probability_pct: 7,
    now_pct: 93.52,
    limit_pct: 85,
    watched: true,
    action: "따로 할 일은 없어요.",
  },
  scores: { usage: score(), power_factor: score({ 초과_구간: 217, 미리_잡은_비율_pct: 95.9, 하루_헛경보: 1.74 }) },
  ...overrides,
});

// 타임라인 요청에는 타임라인을, 그 밖에는 body를 돌려준다.
const route = (body, responses = {}) => (url) => {
  if (url.startsWith("/api/forecast/day/")) return Promise.resolve(ok(timeline()));
  if (url.startsWith("/api/alarm-responses/day/")) return Promise.resolve(ok(responses));
  if (url === "/api/alarm-responses") return Promise.resolve(ok({}));
  return Promise.resolve(body);
};

afterEach(() => {
  vi.unstubAllGlobals();
});

test("처음에는 2018-10-10 13:00의 경보와 할 일, 채점 결과가 보인다", async () => {
  const fetchMock = stubFetch(route(ok(forecast())));
  const { container } = render(<Forecast />);

  expect(await screen.findByLabelText("사용량: 위험")).toHaveTextContent("위험");
  expect(fetchMock).toHaveBeenCalledWith("/api/forecast?time=2018-10-10%2013%3A00");
  expect(screen.getByText("57%")).toBeInTheDocument();
  expect(screen.getByText("큰 설비를 한꺼번에 켜지 마세요.")).toBeInTheDocument();
  expect(screen.getByLabelText("전기 효율(역률): 좋음")).toBeInTheDocument();
  expect(container.querySelector(".alarm-card.level-bad")).not.toBeNull();
  expect(screen.getByText(/사용량: 기준을 넘은 221번 중 100%를 평균 51분 전에/)).toHaveTextContent("하루 2.1번");
  expect(screen.getByText(/역률: 기준을 넘은 217번 중 95.9%/)).toBeInTheDocument();
  expect(screen.queryByText(/학습에 쓴 기간/)).toBeNull();
});

test("시각을 바꾸면 다시 예측하고, 학습 기간과 역률을 보지 않는 시간을 알린다", async () => {
  const night = forecast({
    time: "2018-03-14 03:00",
    learned_period: true,
    power_factor: { ...forecast().power_factor, watched: false, action: "역률은 09~22시만 봐요." },
  });
  const fetchMock = stubFetch(route(ok(night)));
  render(<Forecast />);
  await screen.findByLabelText("사용량: 위험");

  await userEvent.selectOptions(screen.getByLabelText("예측 시각"), "03:00");
  expect(fetchMock).toHaveBeenLastCalledWith("/api/forecast?time=2018-10-10%2003%3A00");
  expect(await screen.findByText("이 시간에는 보지 않아요.")).toBeInTheDocument();
  expect(screen.getByText("학습에 쓴 기간이라 실제보다 잘 맞아 보일 수 있어요.")).toBeInTheDocument();
});

test("마지막 1시간이나 범위 밖 날짜는 요청하지 않고 고를 수 있는 시각을 안내한다", async () => {
  const fetchMock = stubFetch(route(ok(forecast())));
  render(<Forecast />);
  await screen.findByLabelText("사용량: 위험");
  fetchMock.mockClear();

  const dateInput = screen.getByLabelText("예측 날짜");
  await userEvent.clear(dateInput);
  await userEvent.type(dateInput, "2018-12-31");
  await userEvent.selectOptions(screen.getByLabelText("예측 시각"), "23:00");
  expect(screen.getByRole("alert")).toHaveTextContent("12월 31일 22:45까지");
  expect(fetchMock).not.toHaveBeenCalledWith("/api/forecast?time=2018-12-31%2023%3A00");
});

test("불러오지 못하면 이유와 다시 시도 버튼이 보인다", async () => {
  const fetchMock = stubFetch(route(failed(503, "데이터베이스에서 전력 데이터를 읽지 못했습니다.")));
  render(<Forecast />);

  expect(await screen.findByRole("alert")).toHaveTextContent("경보 예측을 불러오지 못했습니다.");
  fetchMock.mockImplementation(route(ok(forecast())));
  await userEvent.click(screen.getByRole("button", { name: "다시 시도" }));
  expect(await screen.findByLabelText("사용량: 위험")).toBeInTheDocument();
});

const reasons = [
  { name: "방금까지의 흐름", points: 50, detail: "지금 252.6kW로 기준(408.1kW)의 62%예요. 1시간 전보다 236.9kW 늘었어요." },
  { name: "평소 패턴", points: 19, detail: "앞으로 1시간 평소(지난 4주) 가장 높은 사용량이 251.6kW예요." },
];
const withReasons = () => {
  const base = forecast();
  return { ...base, usage: { ...base.usage, reasons } };
};

test("경보가 켜졌으면 왜 그렇게 봤는지 이유를 확률을 올린 정도와 함께 보여 준다", async () => {
  stubFetch(route(ok(withReasons())));
  render(<Forecast />);
  await screen.findByLabelText("사용량: 위험");
  expect(screen.getByText("+50%p")).toBeInTheDocument();
  expect(screen.getByText(/지금 252.6kW로 기준\(408.1kW\)의 62%예요/)).toBeInTheDocument();
  expect(screen.getByText("+19%p")).toBeInTheDocument();
});

test("경보에 조치함을 누르면 그 응답을 서버에 보내고 눌린 상태가 된다", async () => {
  const fetchMock = stubFetch(route(ok(withReasons())));
  const onRecorded = vi.fn();
  render(<Forecast onRecorded={onRecorded} />);
  await screen.findByLabelText("사용량: 위험");

  // 좋음인 역률에는 응답 버튼이 없고, 위험인 사용량에만 있다.
  expect(screen.getAllByRole("group")).toHaveLength(1);
  await userEvent.click(screen.getByRole("button", { name: "조치함" }));

  const post = fetchMock.mock.calls.find(([url]) => url === "/api/alarm-responses");
  expect(JSON.parse(post[1].body)).toEqual({ time: "2018-10-10 13:00", target: "usage", level: "danger", probability_pct: 57, response: "acted" });
  expect(screen.getByRole("button", { name: "조치함" })).toHaveAttribute("aria-pressed", "true");
  expect(onRecorded).toHaveBeenCalledTimes(1);
});

test("이미 기록한 경보는 눌린 상태로 보이고, 저장에 실패하면 이유를 알린다", async () => {
  stubFetch((url, options) =>
    url === "/api/alarm-responses" ? Promise.resolve(failed(400, "응답은 acted, ignored, false_alarm 중 하나여야 합니다.")) : route(ok(withReasons()), { "13:00|usage": "ignored" })(url, options),
  );
  render(<Forecast />);
  await screen.findByLabelText("사용량: 위험");
  expect(await screen.findByRole("button", { name: "무시함" })).toHaveAttribute("aria-pressed", "true");

  await userEvent.click(screen.getByRole("button", { name: "오경보였음" }));
  expect(await screen.findByText(/기록하지 못했어요. 응답은/)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "오경보였음" })).toHaveAttribute("aria-pressed", "false");
});
