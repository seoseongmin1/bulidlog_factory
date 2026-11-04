import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, test, vi } from "vitest";
import { failed, ok, stubFetch } from "../test/helpers.jsx";
import ActionAgent, { describe as describeAction, PRESETS } from "./ActionAgent.jsx";

afterEach(() => vi.unstubAllGlobals());

const rows = (kw) => ["20:15", "20:30", "20:45", "21:00", "21:15", "21:30", "21:45", "22:00"].map((time, i) => ({ time, kw: kw[i], pf: 93 }));
const base = {
  id: "abc",
  time: "2018-09-07 20:00",
  planner: "ai",
  targets: ["usage"],
  problem: { usage: { label: "위험", probability_pct: 52 }, power_factor: { label: "좋음", probability_pct: 0 } },
  limits: { usage_kw: 408.1, pf_pct: 85 },
  summary: "미루기를 제안했어요. [SOP-EN-01 2. 경보 단계별 조치]",
  steps: [{ at: "t", who: "system", kind: "detect", text: "경보: 사용량 위험(52%)" }],
  verification: null,
};
const defer = { id: "A1", type: "defer_load", name: "큰 설비 기동 연기", risk: "high", params: { start: "20:15", minutes: 60, kw: 70 }, reason: "앞 1시간이 위험해요.", expected: { peak_kw: [390.6, 320.6], min_pf: [93.4, 93.4], lead_slots: 0 } };
const notify = { id: "A2", type: "notify", name: "담당자 알림", risk: "low", params: { to: "반장", message: "기동을 미뤄 주세요." }, status: "executed", executed_by: "system", result: { 전달: "이 컴퓨터 기록(웹훅 없음)" } };
const proposed = { ...base, status: "waiting_approval", actions: [{ ...defer, status: "pending" }, notify] };
const resolved = {
  ...base,
  status: "resolved",
  actions: [{ ...defer, status: "executed", executed_by: "user" }, notify],
  verification: {
    resolved: true, checks: { usage: true }, needed: { usage: true },
    without: { rows: rows([370, 460, 300, 50, 20, 20, 20, 20]) }, with: { rows: rows([300, 390, 230, 30, 90, 90, 90, 90]) },
  },
};

test("사건을 열면 제안된 조치와 위험도가 보이고, 승인하면 실제 기록으로 채점한 결과가 나온다", async () => {
  const fetchMock = stubFetch((url) => Promise.resolve(ok(url === "/api/incidents" ? proposed : resolved)));
  render(<ActionAgent />);
  await userEvent.click(screen.getByRole("button", { name: `${PRESETS[0].label} · ${PRESETS[0].time.slice(5)}` }));

  expect(await screen.findByText("20:15부터 60분 동안 70kW 미루기 (뒤 60분으로 옮김)")).toBeInTheDocument();
  expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toEqual({ time: "2018-09-07 20:00" });
  expect(screen.getByLabelText("사건 상태: 승인 대기")).toBeInTheDocument();
  expect(screen.getByText("승인 필요")).toBeInTheDocument();
  expect(screen.getByText("자동 실행")).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "[SOP-EN-01 2. 경보 단계별 조치]" })).toHaveAttribute("href", "#manual:SOP-EN-01");

  await userEvent.click(screen.getByRole("button", { name: "승인하고 실행" }));
  expect(fetchMock).toHaveBeenLastCalledWith("/api/incidents/abc/actions/A1/approve", expect.objectContaining({ method: "POST" }));
  expect(await screen.findByLabelText("사건 상태: 해결됨")).toBeInTheDocument();
  expect(screen.getByRole("img", { name: "앞으로 2시간 · 15분 수요전력: 조치 전과 후" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "되돌리기" })).toBeInTheDocument();
});

test("경보 예측에서 맡긴 시각으로 바로 실행한다", async () => {
  const fetchMock = stubFetch(() => Promise.resolve(ok(proposed)));
  render(<ActionAgent request={{ time: "2018-10-10 13:00", nonce: 1 }} />);
  await screen.findByText("20:15부터 60분 동안 70kW 미루기 (뒤 60분으로 옮김)");
  expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toEqual({ time: "2018-10-10 13:00" });
  expect(screen.getByLabelText("사건 날짜")).toHaveValue("2018-10-10");
});

test("실패하면 안내와 다시 시도 버튼을 보여 준다", async () => {
  stubFetch(() => Promise.resolve(failed(400, "데이터 마지막 2시간은 조치 결과를 검증할 수 없어요.")));
  render(<ActionAgent />);
  await userEvent.click(screen.getByRole("button", { name: "에이전트 실행" }));
  const alert = await screen.findByRole("alert");
  expect(alert).toHaveTextContent("조치 에이전트가 실패했습니다.");
  expect(within(alert).getByRole("button", { name: "다시 시도" })).toBeInTheDocument();
});

test("조치 입력을 사람이 읽는 문장으로 바꾼다", () => {
  expect(describeAction({ type: "capacitor_step", params: { start: "14:45", minutes: 60, steps: 3 } })).toBe("14:45부터 60분 동안 콘덴서 3단(30kVar) 추가");
  expect(describeAction({ type: "shed_auxiliary", params: { start: "13:15", minutes: 60, kw: 40 } })).toBe("13:15부터 60분 동안 보조 설비 40kW 정지");
});
