import { useState } from "react";
import { RESPONSE_LABELS } from "../responses.js";

/** 켜진 경보에 작업자가 어떻게 했는지 누른다. 같은 경보에 다시 누르면 새 응답으로 바뀐다. */
export default function ResponseButtons({ alarm, saved, onSave }) {
  const [error, setError] = useState(null);
  const press = async (response) => {
    setError(null);
    try {
      await onSave({ ...alarm, response });
    } catch (err) {
      setError(err.message);
    }
  };

  return (
    <div className="response-buttons" role="group" aria-label={`${alarm.time} ${alarm.name} 경보에 어떻게 했나요?`}>
      <span className="muted">어떻게 했나요?</span>
      {Object.entries(RESPONSE_LABELS).map(([id, label]) => (
        <button key={id} type="button" aria-pressed={saved === id} onClick={() => press(id)}>
          {label}
        </button>
      ))}
      {error && (
        <span className="response-error" role="alert">
          기록하지 못했어요. {error}
        </span>
      )}
    </div>
  );
}
