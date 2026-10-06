# 本地验收音频

真实模型验收使用你自己的普通话 WAV（建议单声道、16 kHz，2–10 秒），保存为 `samples/mandarin.wav`，然后运行：

```sh
uv run --python 3.12 --extra asr python scripts/verify_model.py samples/mandarin.wav
```

音频和模型权重都留在本机，样例音频已被 Git 忽略。不要把私密录音放进版本库。自动化测试使用伪造录音与确定性 ASR 响应，不需要麦克风或模型下载。
