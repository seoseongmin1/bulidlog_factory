import { useState } from "react";
import { getJson, postJson } from "./api.js";
import { useAsync } from "./useAsync.js";

export const RESPONSE_LABELS = { acted: "조치함", ignored: "무시함", false_alarm: "오경보였음" };

/**
 * 한 날짜의 대응 기록을 불러오고, 새 응답을 저장한다. 키는 "HH:MM|대상"이다.
 * 기록을 불러오지 못해도 경보 화면은 그대로 쓸 수 있게, 실패하면 빈 기록으로 본다.
 */
export function useAlarmResponses(date, onRecorded) {
  const state = useAsync(() => getJson(`/api/alarm-responses/day/${date}`), [date]);
  const [local, setLocal] = useState({});
  const saved = { ...(state.data ?? {}), ...local };

  const save = async ({ time, target, level, probability_pct, response }) => {
    await postJson("/api/alarm-responses", { time: `${date} ${time}`, target, level, probability_pct, response });
    setLocal((prev) => ({ ...prev, [`${time}|${target}`]: response }));
    onRecorded?.();
  };
  return { saved, save };
}
