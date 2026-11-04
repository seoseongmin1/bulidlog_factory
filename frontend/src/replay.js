// 재생 모드: 하루 경보 타임라인의 칸을 15분씩 흘려보내며, 그 시각까지 알 수 있는 일만 알림으로 만든다.
const TARGETS = [
  { key: "usage", over: "over", name: "사용량" },
  { key: "power_factor", over: "under", name: "역률" },
];
const LEVEL_LABEL = { warn: "주의", danger: "위험" };
const LEVEL_RANK = { warn: 1, danger: 2 };
const STEPS_AHEAD = 4; // 서버 채점과 같다: 경보 뒤 1시간(4칸) 안에 넘지 않으면 헛경보

const isAlarm = (row) => row !== undefined && row !== null && Boolean(LEVEL_RANK[row]);

/** 끝난 경보 구간 [start, end)를 찾는다. 경보는 주의 이상이 이어지는 동안 하나로 센다. */
function alarmRuns(rows, key, upto) {
  const runs = [];
  let start = null;
  for (let i = 0; i <= upto; i += 1) {
    const on = isAlarm(rows[i][key]);
    if (on && start === null) start = i;
    if (!on && start !== null) {
      runs.push([start, i]);
      start = null;
    }
  }
  if (start !== null) runs.push([start, null]);
  return runs;
}

/**
 * rows[0..upto]까지 지난 알림을 시간 순으로 돌려준다. 종류는 세 가지다.
 * alarm: 경보가 켜짐(또는 주의에서 위험으로 올라감), over: 실제로 기준을 넘음(몇 분 먼저 알렸는지 lead),
 * false: 경보 뒤 1시간이 지나도록 넘지 않음(헛경보)
 */
export function replayEvents(rows, upto) {
  const events = [];
  for (const { key, over, name } of TARGETS) {
    let previous = null;
    for (let i = 0; i <= upto; i += 1) {
      const level = rows[i][key];
      if (isAlarm(level) && (!isAlarm(previous) || LEVEL_RANK[level] > LEVEL_RANK[previous])) {
        events.push({ index: i, time: rows[i].time, type: "alarm", target: key, level, name });
      }
      previous = level;

      if (rows[i][over] && !(i > 0 && rows[i - 1][over])) {
        let lead = null;
        for (let k = STEPS_AHEAD; k >= 0 && lead === null; k -= 1) {
          if (i - k >= 0 && isAlarm(rows[i - k][key])) lead = k * 15;
        }
        events.push({ index: i, time: rows[i].time, type: "over", target: key, lead, name });
      }
    }
    for (const [start, end] of alarmRuns(rows, key, upto)) {
      const settled = end !== null && end + STEPS_AHEAD <= upto;
      if (settled && !rows.slice(start, end + STEPS_AHEAD + 1).some((row) => row[over])) {
        events.push({ index: end + STEPS_AHEAD, time: rows[end + STEPS_AHEAD].time, type: "false", target: key, name });
      }
    }
  }
  return events.sort((a, b) => a.index - b.index);
}

/** 알림 한 줄의 문장 */
export function eventText(event) {
  if (event.type === "alarm") return `${event.name} 경보가 켜졌어요 (${LEVEL_LABEL[event.level]})`;
  if (event.type === "false") return `${event.name} 헛경보였어요. 경보 뒤 1시간 안에 기준을 넘지 않았어요.`;
  if (event.lead === null) return `${event.name}이(가) 기준을 넘었어요. 미리 알리지 못했어요.`;
  if (event.lead === 0) return `${event.name}이(가) 기준을 넘었어요. 경보와 같은 때예요.`;
  return `${event.name}이(가) 기준을 넘었어요. 경보가 ${event.lead}분 먼저 알렸어요.`;
}
