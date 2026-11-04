/** 경보 타임라인 화면 테스트에 쓰는 가짜 응답 */
const TIMES = Array.from({ length: 96 }, (_, i) => `${String(Math.floor(i / 4)).padStart(2, "0")}:${String((i % 4) * 15).padStart(2, "0")}`);
const summary = (overrides = {}) => ({ 초과_구간: 0, 미리_잡은_비율_pct: null, 평균_몇분_전: null, 경보_횟수: 0, 헛경보: 0, ...overrides });

/** 12:15부터 주의, 12:45에 위험, 13:15에 실제로 넘은 하루. 역률은 09~22시만 본다. */
export const timeline = () => ({
  date: "2018-10-10",
  learned_period: false,
  limit_kw: 408.1,
  limit_pct: 85,
  actions: {
    usage: { danger: "큰 설비를 순서를 나눠 켜 주세요.", warn: "지금 사용량을 확인해 주세요.", good: "따로 할 일은 없어요." },
    power_factor: { danger: "콘덴서를 확인해 주세요.", warn: "콘덴서 상태를 봐 주세요.", good: "따로 할 일은 없어요.", unwatched: "역률은 09~22시만 봐요." },
  },
  rows: TIMES.map((time) => ({
    time,
    usage: time === "12:45" ? "danger" : time >= "12:15" && time <= "13:00" ? "warn" : "good",
    usage_pct: time === "12:45" ? 57 : 3,
    kw: time === "13:15" ? 450.2 : 100,
    over: time === "13:15",
    power_factor: time >= "08:00" && time < "22:00" ? "good" : null,
    power_factor_pct: time >= "08:00" && time < "22:00" ? 1 : null,
    pf: 92,
    under: false,
  })),
  summary: {
    usage: summary({ 초과_구간: 1, 미리_잡은_비율_pct: 100, 평균_몇분_전: 60, 경보_횟수: 1 }),
    power_factor: summary({ 경보_횟수: 2, 헛경보: 2 }),
  },
});
