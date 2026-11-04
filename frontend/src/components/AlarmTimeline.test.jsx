import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, test, vi } from "vitest";
import { failed, ok, stubFetch } from "../test/helpers.jsx";
import { timeline } from "../test/timeline.js";
import AlarmTimeline from "./AlarmTimeline.jsx";

afterEach(() => {
  vi.unstubAllGlobals();
});

test("경보 칸과 실제로 넘은 칸이 보이고, 그날 몇 분 먼저 알렸는지 문장으로 나온다", async () => {
  const fetchMock = stubFetch(() => Promise.resolve(ok(timeline())));
  const { container } = render(<AlarmTimeline date="2018-10-10" time="13:00" onSelect={() => {}} />);

  expect(await screen.findByRole("button", { name: "12:45 · 사용량 경보 위험 57% · 실제 100kW" })).toHaveClass("tl-danger");
  expect(fetchMock).toHaveBeenCalledWith("/api/forecast/day/2018-10-10");
  expect(container.querySelectorAll(".tl-warn")).toHaveLength(3 + 1); // 주의 칸 3개(12:15, 12:30, 13:00) + 범례 1개
  expect(screen.getByLabelText("13:15 기준 넘음")).toHaveClass("tl-actual");
  expect(screen.getByRole("button", { name: "13:00 · 사용량 경보 주의 3% · 실제 100kW" })).toHaveAttribute("aria-pressed", "true");
  expect(screen.getByRole("button", { name: "03:00 · 역률 경보 없음 · 실제 역률 92%" })).toHaveClass("tl-none");
  expect(screen.getByText(/사용량: 기준을 넘은 1번 중 100%를 평균 60분 전에 미리 알렸어요/)).toBeInTheDocument();
  expect(screen.getByText("역률: 기준을 넘은 때는 없었어요. 경보 2번은 모두 헛경보예요.")).toBeInTheDocument();
});

test("칸을 누르면 그 시각을 고른다", async () => {
  stubFetch(() => Promise.resolve(ok(timeline())));
  const onSelect = vi.fn();
  render(<AlarmTimeline date="2018-10-10" time="13:00" onSelect={onSelect} />);

  await userEvent.click(await screen.findByRole("button", { name: /^12:45 · 사용량 경보/ }));
  expect(onSelect).toHaveBeenCalledWith("12:45");
});

test("불러오지 못하면 이유와 다시 시도 버튼이 보인다", async () => {
  stubFetch(() => Promise.resolve(failed(503, "데이터베이스에서 전력 데이터를 읽지 못했습니다.")));
  render(<AlarmTimeline date="2018-10-10" time="13:00" onSelect={() => {}} />);
  expect(await screen.findByRole("alert")).toHaveTextContent("타임라인을 불러오지 못했습니다.");
});
