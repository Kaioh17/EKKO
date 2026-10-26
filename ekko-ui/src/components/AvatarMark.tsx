import type { CSSProperties, ReactNode } from "react";

// The ekko mark from the desktop-companion design: a ring + dot that changes
// colour and motion per state, with eyes on top.
export type MarkState = "idle" | "listen" | "process" | "respond";
export type Emotion = "calm" | "happy" | "curious" | "focused" | "surprised" | "sorry" | "sleepy";

export const COLOR: Record<MarkState, string> = { idle: "#FFFFFF", listen: "#3D8BFF", process: "#FFC83D", respond: "#10C172" };

const fb: CSSProperties = { transformBox: "fill-box", transformOrigin: "center" };
const vb: CSSProperties = { transformBox: "view-box", transformOrigin: "50px 50px" };
const tr: CSSProperties = { transition: "stroke .45s ease, fill .45s ease" };
const X = [42.5, 57.5];

function Eyes({ emo, c, state }: { emo: Emotion; c: string; state: MarkState }) {
  const blink: CSSProperties = { ...fb, animation: "ek-blink 4.2s ease-in-out infinite" };
  const rect = (x: number, w: number, h: number, key: number | string = x, y = 50 - h / 2) => (
    <rect key={key} x={x - w / 2} y={y} width={w} height={h} rx={w / 2} fill={c} style={blink} />
  );
  let kids: ReactNode, tf = "";
  switch (emo) {
    case "happy": kids = X.map((x) => <path key={x} d={`M${x - 5} 53 Q${x} 43 ${x + 5} 53`} fill="none" stroke={c} strokeWidth={4.2} strokeLinecap="round" />); break;
    case "curious": kids = [rect(X[0], 7, 14), rect(X[1], 8.5, 19)]; tf = "rotate(-8 50 50)"; break;
    case "focused": kids = X.map((x) => rect(x, 7.5, 7.5)); break;
    case "surprised": kids = X.map((x) => <circle key={x} cx={x} cy={49} r={5.4} fill={c} style={blink} />); break;
    case "sorry": kids = X.map((x, i) => <g key={x} transform={`rotate(${i ? 20 : -20} ${x} 51)`}>{rect(x, 7, 13, "r", 51 - 6.5)}</g>); break;
    case "sleepy": kids = X.map((x) => <path key={x} d={`M${x - 4.5} 52 H${x + 4.5}`} stroke={c} strokeWidth={3.6} strokeLinecap="round" />); break;
    default: kids = X.map((x) => rect(x, 7, 16));
  }
  const shift = state === "listen" ? "translate(2.5 -1.5)" : state === "process" ? "translate(-1 1.5)" : "";
  return <g transform={`${shift} ${tf}`.trim() || undefined}>{kids}</g>;
}

export default function AvatarMark({ state, size, emo = "calm" }: { state: MarkState; size: number; emo?: Emotion }) {
  const c = COLOR[state];
  const proc = state === "process";
  const dot = { idle: `ek-breathe ${emo === "sleepy" ? 5 : 3.2}s ease-in-out infinite`, listen: "ek-pulse .9s ease-in-out infinite", respond: "ek-talk 1.2s ease-in-out infinite", process: undefined }[state];
  const bob = state === "idle" ? { animation: `ek-bob ${emo === "sleepy" ? 5 : 3.2}s ease-in-out infinite` } : undefined;
  return (
    <svg width={size} height={size} viewBox="0 0 100 100" style={{ display: "block", overflow: "visible", ...bob }}>
      {state === "respond" && [0, 1].map((i) => (
        <circle key={`e${i}`} cx={50} cy={50} r={32} fill="none" stroke={c} strokeWidth={3} style={{ ...vb, opacity: 0, animation: `ek-echo 1.6s ${i * 0.8}s ease-out infinite` }} />
      ))}
      {state === "listen" && [0, 1].map((i) => (
        <circle key={`r${i}`} cx={72.63} cy={27.37} r={8.5} fill={c} style={{ ...fb, opacity: 0, animation: `ek-ripple 1.8s ${i * 0.9}s ease-out infinite` }} />
      ))}
      <g style={proc ? { ...vb, animation: "ek-spin 1.2s cubic-bezier(.55,.12,.45,.88) infinite" } : undefined}>
        <circle cx={50} cy={50} r={32} fill="none" stroke={c} strokeWidth={11} strokeLinecap="round" strokeDasharray="159.06 42" transform="rotate(-7.4 50 50)" style={proc ? { ...tr, animation: "ek-gap 1.2s ease-in-out infinite" } : tr} />
        <circle cx={72.63} cy={27.37} r={8.5} fill={c} style={dot ? { ...fb, ...tr, animation: dot } : tr} />
      </g>
      <Eyes emo={emo} c="#FFFFFF" state={state} />
    </svg>
  );
}
