import { useEffect, useRef } from "react";
import {
  BarController,
  BarElement,
  CategoryScale,
  Chart as ChartJS,
  Filler,
  LineController,
  LineElement,
  LinearScale,
  PointElement,
  Tooltip,
} from "chart.js";
import { number } from "../format.js";

ChartJS.register(
  BarController,
  BarElement,
  LineController,
  LineElement,
  PointElement,
  CategoryScale,
  LinearScale,
  Filler,
  Tooltip,
);

const cssVar = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();

/** kind는 "bar" 또는 "line", rows는 [{label, value}] 목록이다. suggestedMax는 세로축이 최소한 여기까지 보이게 한다. */
export default function Chart({ kind, rows, unit, label, suggestedMax }) {
  const canvas = useRef(null);
  const rowsKey = JSON.stringify(rows);

  useEffect(() => {
    const series = cssVar("--series");
    const seriesSoft = cssVar("--series-soft");
    const muted = cssVar("--muted");
    const chart = new ChartJS(canvas.current, {
      type: kind,
      data: {
        labels: rows.map((row) => row.label),
        datasets: [
          {
            data: rows.map((row) => row.value),
            backgroundColor: kind === "line" ? seriesSoft : series,
            borderColor: series,
            borderWidth: kind === "line" ? 2 : 0,
            fill: kind === "line" ? "origin" : false,
            borderRadius: { topLeft: 6, topRight: 6 },
            borderSkipped: "bottom",
            maxBarThickness: 22,
            pointRadius: 0,
            pointHoverRadius: 4,
            tension: 0,
            spanGaps: false,
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        animation: false,
        interaction: { mode: "index", intersect: false },
        plugins: {
          legend: { display: false },
          tooltip: {
            displayColors: false,
            cornerRadius: 8,
            padding: 10,
            callbacks: { label: (item) => `${number(item.parsed.y, 2)} ${unit}` },
          },
        },
        scales: {
          x: { grid: { display: false }, ticks: { color: muted, autoSkipPadding: 14, maxRotation: 0 } },
          y: { suggestedMax, grid: { color: cssVar("--grid") }, border: { display: false }, ticks: { color: muted, maxTicksLimit: 5 } },
        },
      },
    });
    return () => chart.destroy();
  }, [kind, unit, rowsKey, suggestedMax]);

  return <canvas ref={canvas} role="img" aria-label={label} />;
}
