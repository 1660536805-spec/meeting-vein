# 本机真实 ASR 样例烟测（2026-10-11）

基线提交：`5c8897f62ef8`。环境：macOS 26.6.2 / arm64，Python 3.12.14，Node.js 20.20.2，FunASR 1.4.16。模型权重已缓存在本机；通过 `LOCAL_ASR_ASR_MODEL`、`LOCAL_ASR_VAD_MODEL`、`LOCAL_ASR_STREAMING_MODEL` 指向三个缓存快照目录，避免按模型名启动时再次联网校验。

## 结果

- `local_asr/scripts/verify_model.py` 读取仓库中 Paraformer 的 `example/asr_example.wav`，使用真实 SenseVoiceSmall + FSMN-VAD，返回 `state=ready`、`device=mps`、`language=zh`、`transcript_characters=20`、`audio_duration_ms=5547`。
- 三服务通过 `scripts/start_local.sh` 启动；`GET /models/status` 返回离线模型和流式模型均为 `ready`。
- `local_asr/scripts/smoke_stream.py` 将同一音频按约 480 毫秒一块，经 `127.0.0.1:5173/asr/v1/audio/stream` 前端代理发送到真实流式 Paraformer。返回 10 个非空 partial、累计 19 个转写字符；样本内片段 `capture_to_response_ms` 为 `[979.2, 989.6, 945.2, 1005.7, 958.3, 1025.6, 1051.8, 1112.0, 1142.7, 1086.9]`，P95 为 **1142.7 ms**。脚本退出码为 0。

## 解释与边界

该结果证明本机缓存权重能够真实加载、样例音频能经前端代理完成流式识别。P95 是该脚本按块发送后到收到响应的浏览器外估算，不能直接当成发言到上板的端到端时延；这一样本的 P95 也高于内部 1 秒流式目标。未验证真实麦克风权限、不同设备、ASR 自动提交到图、真实 LLM 同时运行、长时间会议、故障恢复。完整 M1 验收仍未完成。
