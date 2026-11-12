/** 阅读会话状态机（B端手册要求的显式状态，替代布尔变量拼接）。
 * D1 仅定义状态与转移表；D7 接入打断/恢复逻辑。
 */

export type Phase =
  | 'READING'    // 故事播放中
  | 'PAUSED'     // 用户暂停
  | 'LISTENING'  // 录音中（按住说话）
  | 'THINKING'   // 检索/生成回答中
  | 'SPEAKING'   // 助手语音播报中
  | 'RESUMING'   // 播报结束，恢复阅读
  | 'FAILED'     // 本轮失败，可重试

export type PhaseEvent =
  | 'PAUSE'          // READING -> PAUSED
  | 'PLAY'           // PAUSED -> READING
  | 'HOLD_START'     // READING|PAUSED|SPEAKING|THINKING -> LISTENING（隐含打断）
  | 'HOLD_END'       // LISTENING -> THINKING（收到音频）| READING（太短丢弃）
  | 'ASK'            // 文本提问：任意稳定态 -> THINKING（隐含打断旧播报）
  | 'ANSWER_READY'   // THINKING -> SPEAKING
  | 'ANSWER_DONE'    // SPEAKING -> RESUMING
  | 'RESUMED'        // RESUMING -> READING
  | 'ERROR'          // * -> FAILED
  | 'RETRY'          // FAILED -> READING

const TRANSITIONS: Record<Phase, Partial<Record<PhaseEvent, Phase>>> = {
  READING: { PAUSE: 'PAUSED', HOLD_START: 'LISTENING', ASK: 'THINKING', ERROR: 'FAILED' },
  PAUSED: { PLAY: 'READING', HOLD_START: 'LISTENING', ASK: 'THINKING', ERROR: 'FAILED' },
  LISTENING: { HOLD_END: 'THINKING', PAUSE: 'PAUSED', ERROR: 'FAILED' },
  THINKING: { ANSWER_READY: 'SPEAKING', PAUSE: 'PAUSED', HOLD_START: 'LISTENING', ASK: 'THINKING', ERROR: 'FAILED' },
  SPEAKING: { ANSWER_DONE: 'RESUMING', HOLD_START: 'LISTENING', ASK: 'THINKING', ERROR: 'FAILED' },
  RESUMING: { RESUMED: 'READING', ASK: 'THINKING', ERROR: 'FAILED' },
  FAILED: { RETRY: 'READING' },
}

export function nextPhase(current: Phase, event: PhaseEvent): Phase {
  const target = TRANSITIONS[current][event]
  return target ?? current
}

/** SPEAKING/LISTENING/THINKING 中发起打断（HOLD_START/ASK）：旧回答取消、seq递增。 */
export function isInterrupt(from: Phase, event: PhaseEvent): boolean {
  return (event === 'HOLD_START' || event === 'ASK') &&
    (from === 'SPEAKING' || from === 'THINKING' || from === 'LISTENING')
}
