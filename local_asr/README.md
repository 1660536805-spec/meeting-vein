# 本地 ASR 服务

本目录从独立原型移入 AI Meeting Organizer。SenseVoiceSmall 负责录音结束后的最终转写和 `NormUtterance` JSON；流式 Paraformer 通过 `/v1/audio/stream` WebSocket 输出录音中的临时文字。网页与看板位于仓库根目录的 `frontend/` 和 `backend/`；本目录不再包含独立网页。音频只发往 loopback 服务 `127.0.0.1:9000`。

## 环境

- macOS Apple Silicon 或 CPU；Python 3.12、uv 和 ffmpeg。
- 首次安装模型依赖并运行时会下载 FunASR、PyTorch 与模型权重，体积较大；SenseVoiceSmall 默认缓存在 `~/.cache/modelscope`，流式模型缓存在 `local_asr/model_cache/`（已被 Git 忽略）。
- 首次加载模型可能需要数分钟。Apple Silicon 优先尝试 MPS，失败时回退 CPU。

## 启动

```sh
cp .env.example .env  # 可选，按需修改缓存目录
scripts/start_asr.sh
```

产品网页和看板的启动方式见仓库根目录 README。ASR 服务脚本会拒绝非 loopback 绑定，不要通过反向代理暴露服务。

## 数据与隐私

默认不把音频落盘：上传内容经过 ffmpeg 标准化后在请求临时目录处理并清理；模型文件只进入本机缓存。日志不记录音频或识别文本。`LOCAL_ASR_DEBUG_SAVE_AUDIO` 默认关闭；除非正在调试，不要开启或使用含敏感语音的录音调试。

## 验证

```sh
uv run --python 3.12 --extra dev pytest backend/tests -q
```

单独检查脚本接口（不下载模型）：

```sh
uv run --python 3.12 python scripts/verify_model.py --fake
```

真实验收音频准备和执行方式见 [samples/README.md](samples/README.md)。模型验证只输出设备、语言、字符数和时长，不回显识别文本。
流式模型下载完成后，可从仓库根目录运行 `local_asr/.venv/bin/python local_asr/scripts/benchmark_streaming.py`，测量每个 480 毫秒音频块的推理耗时。
