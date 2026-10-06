class AsrCaptureProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this.pending = new Float32Array(1024);
    this.offset = 0;
  }

  process(inputs, outputs) {
    const input = inputs[0]?.[0];
    if (input) {
      for (let i = 0; i < input.length; i++) {
        this.pending[this.offset++] = input[i];
        if (this.offset === this.pending.length) {
          const block = this.pending;
          this.port.postMessage(block, [block.buffer]);
          this.pending = new Float32Array(1024);
          this.offset = 0;
        }
      }
    }
    for (const output of outputs[0] || []) output.fill(0);
    return true;
  }
}

registerProcessor("asr-capture", AsrCaptureProcessor);
