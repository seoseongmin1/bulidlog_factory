import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, test, vi } from "vitest";
import { chartMock, failed, ok, stubFetch } from "../test/helpers.jsx";
import PowerFactorPlan from "./PowerFactorPlan.jsx";

vi.mock("./Chart.jsx", () => chartMock());

const hours = Array.from({ length: 13 }, (_, i) => ({ label: `${String(i + 9).padStart(2, "0")}시`, value: i === 2 ? 24.5 : 5 }));
const plan = (overrides = {}) => ({
  기간: "2018-01-01 ~ 2018-12-31",
  목표_역률_pct: 90,
  현재: { 가중_평균_역률: 92.85, 목표_미달_칸_비율_pct: 20.5 },
  자동_조정기_추천: { 용량_kvar: 40, 단계_kvar: 10, 목표_미달_칸_비율_pct: 4.5, 과보상_칸_비율_pct: 0 },
  고정_콘덴서_비교: { 용량_kvar: 40, 목표_미달_칸_비율_pct: 40.9, 과보상_칸_비율_pct: 48.3 },
  주의: "15분 평균으로 한 산술 계산입니다.",
  rows: hours,
  ...overrides,
});

afterEach(() => {
  vi.unstubAllGlobals();
});

test("1년, 목표 90%로 추천 용량과 고정 콘덴서 비교, 시간대별 그래프가 보인다", async () => {
  const fetchMock = stubFetch(() => Promise.resolve(ok(plan())));
  render(<PowerFactorPlan />);

  expect(await screen.findByText(/자동 역률 조정기 40kVar\(10kVar 단위\)가 필요해요/)).toBeInTheDocument();
  expect(fetchMock).toHaveBeenCalledWith("/api/power-factor-plan?start=2018-01-01&end=2018-12-31&target=90");
  expect(screen.getByText("미달 20.5%")).toBeInTheDocument();
  expect(screen.getByText("미달 4.5%")).toBeInTheDocument();
  expect(screen.getByText("미달 40.9%").closest(".kpi")).toHaveClass("tile-bad");
  expect(screen.getByRole("note")).toHaveTextContent("과보상되어 오히려 미달이 늘어요");
  expect(screen.getByTestId("chart")).toHaveAttribute("data-points", "13");
});

test("목표를 바꾸면 다시 계산하고, 설치가 필요 없으면 그렇게 말한다", async () => {
  const fetchMock = stubFetch(() =>
    Promise.resolve(ok(plan({ 목표_역률_pct: 85, 자동_조정기_추천: { 용량_kvar: 0, 단계_kvar: 10, 목표_미달_칸_비율_pct: 4, 과보상_칸_비율_pct: 0 }, 고정_콘덴서_비교: null }))),
  );
  render(<PowerFactorPlan />);
  await screen.findByText(/콘덴서를 더 달 필요가 없어요/);

  await userEvent.selectOptions(screen.getByLabelText("목표 역률"), "85");
  expect(fetchMock).toHaveBeenLastCalledWith("/api/power-factor-plan?start=2018-01-01&end=2018-12-31&target=85");
  expect(screen.queryByText(/고정 콘덴서/)).toBeNull();
});

test("시작 날짜가 끝 날짜보다 늦으면 요청하지 않고 안내한다", async () => {
  const fetchMock = stubFetch(() => Promise.resolve(ok(plan())));
  render(<PowerFactorPlan />);
  await screen.findByText(/자동 역률 조정기 40kVar/);
  fetchMock.mockClear();

  const startInput = screen.getByLabelText("시작 날짜");
  await userEvent.clear(startInput);
  await userEvent.type(startInput, "2018-12-31");
  const endInput = screen.getByLabelText("끝 날짜");
  await userEvent.clear(endInput);
  await userEvent.type(endInput, "2018-06-01");
  expect(screen.getByRole("alert")).toHaveTextContent("시작 날짜가 끝 날짜보다 늦지 않게");
  expect(fetchMock).not.toHaveBeenCalledWith("/api/power-factor-plan?start=2018-12-31&end=2018-06-01&target=90");
});

test("불러오지 못하면 이유와 다시 시도 버튼이 보인다", async () => {
  stubFetch(() => Promise.resolve(failed(503, "데이터베이스에서 전력 데이터를 읽지 못했습니다.")));
  render(<PowerFactorPlan />);
  expect(await screen.findByRole("alert")).toHaveTextContent("역률 개선 계산을 불러오지 못했습니다.");
  expect(screen.getByRole("button", { name: "다시 시도" })).toBeInTheDocument();
});
