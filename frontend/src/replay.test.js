import { expect, test } from "vitest";
import { eventText, previousDate, replayEvents, shiftEvents } from "./replay.js";
import { timeline } from "./test/timeline.js";

const index = (time) => timeline().rows.findIndex((r) => r.time === time);

test("경보가 켜진 때, 위험으로 오른 때, 실제로 넘은 때와 먼저 알린 시간이 알림이 된다", () => {
  const { rows } = timeline();
  const events = replayEvents(rows, index("13:15"));
  expect(events.map((e) => [e.time, e.type, e.level ?? e.lead])).toEqual([
    ["12:15", "alarm", "warn"],
    ["12:45", "alarm", "danger"],
    ["13:15", "over", 60],
  ]);
  expect(eventText(events[2])).toBe("사용량이 기준을 넘었어요. 경보가 60분 먼저 알렸어요.");
});

test("지금 시각 뒤의 일은 알림에 없다", () => {
  const { rows } = timeline();
  expect(replayEvents(rows, index("12:00"))).toEqual([]);
  expect(replayEvents(rows, index("12:30")).map((e) => e.time)).toEqual(["12:15"]);
});

test("경보 뒤 1시간이 지나도 넘지 않으면 그때 헛경보로 알린다", () => {
  const { rows } = timeline();
  rows[index("13:15")].over = false;
  expect(replayEvents(rows, index("14:00")).some((e) => e.type === "false")).toBe(false);
  const events = replayEvents(rows, index("14:15"));
  const falseAlarm = events.find((e) => e.type === "false");
  expect(falseAlarm.time).toBe("14:15");
  expect(eventText(falseAlarm)).toContain("헛경보");
});

test("경보 없이 넘으면 미리 알리지 못했다고 쓴다", () => {
  const { rows } = timeline();
  for (const row of rows) row.usage = "good";
  const events = replayEvents(rows, index("13:15"));
  expect(events).toHaveLength(1);
  expect(eventText(events[0])).toContain("미리 알리지 못했어요");
});

test("09~22시 밖의 역률(없음)은 경보로 세지 않는다", () => {
  const { rows } = timeline();
  expect(replayEvents(rows, 10).filter((e) => e.target === "power_factor")).toEqual([]);
});

test("교대가 끝나는 06:00·14:00·22:00마다 인수인계 알림을 만든다", () => {
  const rows = Array.from({ length: 96 }, (_, i) => ({ time: `${String(Math.floor(i / 4)).padStart(2, "0")}:${String((i % 4) * 15).padStart(2, "0")}` }));
  expect(shiftEvents(rows, 23)).toEqual([]);
  expect(shiftEvents(rows, 95).map((e) => [e.time, e.shift, e.previousDay])).toEqual([
    ["06:00", "C", true],
    ["14:00", "A", false],
    ["22:00", "B", false],
  ]);
  expect(previousDate("2018-10-01")).toBe("2018-09-30");
  expect(previousDate("2018-03-01")).toBe("2018-02-28");
});
