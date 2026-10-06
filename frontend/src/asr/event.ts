export interface NormUtterance {
  utterance_id: string;
  meeting_id: string;
  session_id: string | null;
  seq: number;
  speaker: {
    speaker_ref: string;
    source_id?: string | null;
    display_name: string;
    is_resolved: boolean;
  };
  text: string;
  language: string;
  start_offset_ms: number;
  end_offset_ms: number;
  received_at_ms: number;
  is_final: true;
  is_partial: false;
  source: string;   // 采集来源标记（本地 SenseVoice 为 "local_sensevoice"，后端亦可能回传其他来源）
}
