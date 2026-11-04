export class ApiError extends Error {}

/** 서버에서 JSON을 받는다. 실패하면 사용자에게 보여 줄 수 있는 메시지를 담은 ApiError를 던진다. */
export async function getJson(path) {
  return readJson(() => fetch(path));
}

/** 서버로 JSON을 보내고 JSON을 받는다. 실패 처리는 getJson과 같다. */
export async function postJson(path, body) {
  return readJson(() => fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }));
}

async function readJson(send) {
  let response;
  try {
    response = await send();
  } catch {
    throw new ApiError("서버에 연결하지 못했습니다.");
  }

  let body = null;
  try {
    body = await response.json();
  } catch {
    body = null;
  }

  if (!response.ok) {
    const detail = typeof body?.detail === "string" ? body.detail : null;
    throw new ApiError(detail ?? `요청에 실패했습니다 (HTTP ${response.status}).`);
  }
  return body;
}
