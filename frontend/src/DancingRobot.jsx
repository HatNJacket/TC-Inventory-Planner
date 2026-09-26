import './DancingRobot.css';

// A purely decorative mascot: no events, network requests, or app state.
export default function DancingRobot() {
  return <div className="tc-dancing-robot" aria-hidden="true">
    <svg viewBox="0 0 80 96" focusable="false">
      <ellipse cx="40" cy="88" rx="23" ry="4" fill="#164e3b" opacity=".12"/>
      <g className="tc-robot-bounce" stroke="#176149" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
        <g className="tc-robot-leg tc-robot-leg-left"><path d="M32 68v13h-8" fill="none" strokeWidth="6"/></g>
        <g className="tc-robot-leg tc-robot-leg-right"><path d="M48 68v13h8" fill="none" strokeWidth="6"/></g>
        <g className="tc-robot-arm tc-robot-arm-left"><path d="M24 49l-11 7-5-9" fill="none" strokeWidth="5"/><circle cx="8" cy="45" r="4" fill="#a7f3d0"/></g>
        <g className="tc-robot-arm tc-robot-arm-right"><path d="M56 49l11 7 5-9" fill="none" strokeWidth="5"/><circle cx="72" cy="45" r="4" fill="#a7f3d0"/></g>
        <rect x="25" y="43" width="30" height="27" rx="8" fill="#75d9ac"/>
        <path d="M36 55l4 5 5-8" fill="none" stroke="#fff" strokeWidth="3"/>
        <path d="M40 17V10" fill="none"/>
        <circle cx="40" cy="7" r="4" fill="#fbbf24"/>
        <rect x="18" y="18" width="44" height="28" rx="10" fill="#d1fae5"/>
        <rect x="24" y="24" width="32" height="15" rx="6" fill="#176149" stroke="none"/>
        <path d="M29 30l2-2 2 2m14 0 2-2 2 2" fill="none" stroke="#d1fae5" strokeWidth="2"/>
        <path d="M37 33q3 4 6 0" fill="none" stroke="#d1fae5" strokeWidth="1.8"/>
      </g>
    </svg>
  </div>;
}
