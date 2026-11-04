import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, test, vi } from "vitest";
import { failed, ok, stubFetch } from "../test/helpers.jsx";
import { handover } from "../test/handover.js";
import Handover, { withManualLinks } from "./Handover.jsx";

afterEach(() => vi.unstubAllGlobals());

test("코드가 쓴 인수인계서를 먼저 보여 주고, AI가 다듬은 문장이 오면 바꾼다", async () => {
  let finishAi;
  const fetchMock = stubFetch((url) =>
    url.endsWith("?ai=false")
      ? Promise.resolve(ok(handover({ shift: "B" })))
      : new Promise((resolve) => {
          finishAi = () => resolve(ok(handover({ shift: "B", source: "ai", summary: ["다듬은 첫째 줄", "다듬은 둘째 줄", "다듬은 셋째 줄"] })));
        }),
  );
  render(<Handover />);
  expect(await screen.findByText("교대 요약 첫째 줄")).toBeInTheDocument();
  expect(screen.getByText("AI가 문장을 다듬는 중이에요…")).toBeInTheDocument();
  expect(fetchMock).toHaveBeenCalledWith("/api/handover/2018-10-10/B?ai=false");

  finishAi();
  expect(await screen.findByText("다듬은 첫째 줄")).toBeInTheDocument();
  expect(screen.getByText(/숫자는 모두 계산 결과와 같은지 검사를 통과했어요/)).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "[SOP-EN-01 2. 경보 단계별 조치]" })).toHaveAttribute("href", "#manual:SOP-EN-01");
});

test("조를 바꾸면 그 조의 인수인계서를 불러온다", async () => {
  const fetchMock = stubFetch(() => Promise.resolve(ok(handover())));
  render(<Handover />);
  await screen.findByText("교대 요약 첫째 줄");
  await userEvent.click(screen.getByRole("radio", { name: /C조/ }));
  expect(screen.getByRole("radio", { name: /C조/ })).toHaveAttribute("aria-checked", "true");
  await vi.waitFor(() => expect(fetchMock).toHaveBeenCalledWith("/api/handover/2018-10-10/C?ai=false"));
});

test("불러오지 못하면 안내와 다시 시도 버튼을 보여 준다", async () => {
  stubFetch(() => Promise.resolve(failed(503, "데이터베이스 오류")));
  render(<Handover />);
  expect(await screen.findByRole("alert")).toHaveTextContent("인수인계서를 만들지 못했습니다.");
  expect(screen.getByRole("button", { name: "다시 시도" })).toBeInTheDocument();
});

test("매뉴얼 표기만 링크로 바꾼다", () => {
  const parts = withManualLinks("확인 [SOP-OP-02 3. 응답하지 않은 경보] 끝");
  expect(parts[0]).toBe("확인 ");
  expect(parts[1].props.href).toBe("#manual:SOP-OP-02");
  expect(parts[2]).toBe(" 끝");
});
