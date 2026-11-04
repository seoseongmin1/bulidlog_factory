import { act, fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, test, vi } from "vitest";
import { chartMock, failed, ok, stubFetch } from "../test/helpers.jsx";
import { handover } from "../test/handover.js";
import { timeline } from "../test/timeline.js";
import Replay from "./Replay.jsx";

vi.mock("./Chart.jsx", () => chartMock());

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

const tick = (ms) => act(() => vi.advanceTimersByTime(ms));

// 교대가 끝난 시각을 지나면 인수인계서도 불러온다.
const replayServer = (url) => {
  const m = url.match(/^\/api\/handover\/([\d-]+)\/([ABC])/);
  return Promise.resolve(m ? ok(handover({ date: m[1], shift: m[2] })) : ok(timeline()));
};

test("00:00에서 시작해 재생하면 15분씩 흘러가며 알림이 쌓이고, 끝나면 그날 결과가 나온다", async () => {
  const fetchMock = stubFetch(replayServer);
  render(<Replay />);
  await screen.findByRole("button", { name: "재생" });
  expect(fetchMock).toHaveBeenCalledWith("/api/forecast/day/2018-10-10");
  expect(screen.getByText("00:00", { selector: "strong" })).toBeInTheDocument();
  expect(screen.getByText("아직 알림이 없어요.")).toBeInTheDocument();
  expect(screen.getByTestId("chart")).toHaveAttribute("data-points", "96");

  vi.useFakeTimers();
  await act(async () => {
    screen.getByRole("button", { name: "재생" }).click();
  });
  tick(500 * 49); // 12:15
  expect(screen.getByText("12:15", { selector: "strong" })).toBeInTheDocument();
  expect(screen.getByText(/사용량 경보가 켜졌어요 \(주의\)/)).toBeInTheDocument();
  expect(screen.getByText("지금 사용량을 확인해 주세요.")).toBeInTheDocument();

  tick(500 * 4); // 13:15
  expect(screen.getByText(/경보가 60분 먼저 알렸어요/)).toBeInTheDocument();

  tick(500 * 60); // 끝
  expect(screen.getByRole("button", { name: "처음부터 다시" })).toBeInTheDocument();
  expect(screen.getByText(/기준을 넘은 1번 중 100%를 평균 60분 전에 미리 알렸어요/)).toBeInTheDocument();
});

test("멈춤으로 멈추고, 이동 막대로 원하는 시각으로 옮길 수 있다", async () => {
  stubFetch(replayServer);
  render(<Replay />);
  await screen.findByRole("button", { name: "재생" });

  vi.useFakeTimers();
  await act(async () => {
    screen.getByRole("button", { name: "재생" }).click();
  });
  tick(1000);
  expect(screen.getByText("00:30", { selector: "strong" })).toBeInTheDocument();
  await act(async () => {
    screen.getByRole("button", { name: "멈춤" }).click();
  });
  tick(5000);
  expect(screen.getByText("00:30", { selector: "strong" })).toBeInTheDocument();

  fireEvent.change(screen.getByLabelText("시각 이동"), { target: { value: "52" } });
  expect(screen.getByText("13:00", { selector: "strong" })).toBeInTheDocument();
  expect(screen.getByText(/사용량 경보가 켜졌어요 \(위험\)/)).toBeInTheDocument();
});

test("역률을 보지 않는 시간에는 그렇게 알려 준다", async () => {
  stubFetch(replayServer);
  render(<Replay />);
  await screen.findByText("이 시간에는 보지 않아요.");
  expect(screen.getByText("역률은 09~22시만 봐요.")).toBeInTheDocument();
});

test("고를 수 없는 날짜는 요청하지 않고, 불러오지 못하면 다시 시도 버튼이 보인다", async () => {
  const fetchMock = stubFetch(() => Promise.resolve(failed(503, "데이터베이스에서 전력 데이터를 읽지 못했습니다.")));
  render(<Replay />);
  expect(await screen.findByRole("alert")).toHaveTextContent("재생할 하루를 불러오지 못했습니다.");
  expect(screen.getByRole("button", { name: "다시 시도" })).toBeInTheDocument();
  fetchMock.mockClear();
  const input = screen.getByLabelText("재생 날짜");
  await userEvent.clear(input);
  expect(fetchMock).not.toHaveBeenCalled();
});

test("알림 목록의 경보에 응답을 누르면 그 시각·확률과 함께 서버에 보낸다", async () => {
  const fetchMock = stubFetch((url) => (url.startsWith("/api/alarm-responses") ? Promise.resolve(ok({})) : replayServer(url)));
  render(<Replay />);
  await screen.findByRole("button", { name: "재생" });

  fireEvent.change(screen.getByLabelText("시각 이동"), { target: { value: "49" } }); // 12:15 주의 경보가 켜짐
  const group = await screen.findByRole("group", { name: "12:15 사용량 경보에 어떻게 했나요?" });
  await userEvent.click(within(group).getByRole("button", { name: "오경보였음" }));

  const post = fetchMock.mock.calls.find(([url]) => url === "/api/alarm-responses");
  expect(JSON.parse(post[1].body)).toEqual({ time: "2018-10-10 12:15", target: "usage", level: "warn", probability_pct: 3, response: "false_alarm" });
  expect(within(group).getByRole("button", { name: "오경보였음" })).toHaveAttribute("aria-pressed", "true");
  // 기준을 넘은 알림 같은 다른 종류에는 응답 버튼이 없다.
  fireEvent.change(screen.getByLabelText("시각 이동"), { target: { value: "53" } });
  expect(screen.getAllByRole("group").length).toBe(2); // 12:15 주의, 12:45 위험
});

test("교대가 끝나는 시각을 지나면 인수인계서가 자동으로 작성됐다는 알림과 문서가 뜬다", async () => {
  const fetchMock = stubFetch(replayServer);
  render(<Replay />);
  await screen.findByRole("button", { name: "재생" });
  expect(screen.queryByLabelText("자동 작성된 인수인계서")).not.toBeInTheDocument();

  fireEvent.change(screen.getByLabelText("시각 이동"), { target: { value: "24" } }); // 06:00: 전날 22시에 시작한 C조가 끝남
  expect(screen.getByText(/C조\(22:00~06:00\) 인수인계서를 자동으로 작성했어요/)).toBeInTheDocument();
  await screen.findByText("교대 요약 첫째 줄");
  expect(fetchMock).toHaveBeenCalledWith("/api/handover/2018-10-09/C?ai=false");

  fireEvent.change(screen.getByLabelText("시각 이동"), { target: { value: "56" } }); // 14:00: A조가 끝남
  expect(screen.getByText(/A조\(06:00~14:00\) 인수인계서를 자동으로 작성했어요/)).toBeInTheDocument();
  await vi.waitFor(() => expect(fetchMock).toHaveBeenCalledWith("/api/handover/2018-10-10/A?ai=false"));
  // 지난 교대의 문서도 다시 열 수 있다.
  await userEvent.click(screen.getAllByRole("button", { name: "보기" }).at(-1));
  await vi.waitFor(() => expect(screen.getByLabelText("자동 작성된 인수인계서")).toHaveTextContent("C조"));
});
