// ════════════════════════════════════════════════
// VAD + 录音 Foreground Service
// 文件：entry/src/main/ets/service/RecorderService.ets
// ════════════════════════════════════════════════

import audio from '@ohos.multimedia.audio';
import media from '@ohos.multimedia.media';
import backgroundTaskManager from '@ohos.resourceschedule.backgroundTaskManager';
import wantAgent from '@ohos.app.ability.wantAgent';
import fileIo from '@ohos.file.fs';
import common from '@ohos.app.ability.common';

const TAG = 'RecorderService';

// VAD 参数
const VAD_SAMPLE_RATE = 8000;    // 低采样率，仅用于音量检测
const REC_SAMPLE_RATE = 16000;   // 录音采样率（Whisper 标准）
const VAD_THRESHOLD = 0.02;      // RMS 阈值
const VAD_TRIGGER_MS = 3000;     // 持续超过阈值多少毫秒才开始录音
const SILENCE_STOP_MS = 60000;   // 静音多少毫秒后自动停止

export enum RecorderState {
  IDLE = 'idle',
  LISTENING = 'listening',  // VAD 监听中
  RECORDING = 'recording',  // 正在录音
}

export class RecorderService {
  private state: RecorderState = RecorderState.IDLE;
  private avRecorder: media.AVRecorder | null = null;
  private audioRenderer: audio.AudioCapturer | null = null;
  private vadBuffer: ArrayBuffer[] = [];
  private vadAboveStart: number = 0;
  private silenceStart: number = 0;
  private currentFilePath: string = '';
  private onStateChange?: (s: RecorderState, filePath?: string) => void;
  private context: common.ServiceExtensionContext;

  constructor(ctx: common.ServiceExtensionContext, cb?: (s: RecorderState, fp?: string) => void) {
    this.context = ctx;
    this.onStateChange = cb;
  }

  // ── 启动后台常驻（Foreground Service） ──────────────
  async startForeground() {
    try {
      const wantAgentInfo: wantAgent.WantAgentInfo = {
        wants: [{bundleName: 'com.aiassistant.recorder', abilityName: 'EntryAbility'}],
        operationType: wantAgent.OperationType.START_ABILITY,
        requestCode: 0,
      };
      const wa = await wantAgent.getWantAgent(wantAgentInfo);
      await backgroundTaskManager.startBackgroundRunning(
        this.context,
        backgroundTaskManager.BackgroundMode.AUDIO_RECORDING,
        wa
      );
      console.info(TAG, '后台录音服务已启动');
    } catch (e) {
      console.error(TAG, '启动后台服务失败：', JSON.stringify(e));
    }
  }

  async stopForeground() {
    try {
      await backgroundTaskManager.stopBackgroundRunning(this.context);
    } catch (e) {
      console.error(TAG, '停止后台服务失败：', JSON.stringify(e));
    }
  }

  // ── VAD 监听模式 ──────────────────────────────────
  async startListening() {
    if (this.state !== RecorderState.IDLE) return;
    this.state = RecorderState.LISTENING;
    this.setState(RecorderState.LISTENING);

    const streamInfo: audio.AudioStreamInfo = {
      samplingRate: audio.AudioSamplingRate.SAMPLE_RATE_8000,
      channels: audio.AudioChannel.CHANNEL_1,
      sampleFormat: audio.AudioSampleFormat.SAMPLE_FORMAT_S16LE,
      encodingType: audio.AudioEncodingType.ENCODING_TYPE_RAW,
    };
    const capturerInfo: audio.AudioCapturerInfo = {
      source: audio.SourceType.SOURCE_TYPE_MIC,
      capturerFlags: 0,
    };
    const capturerOptions: audio.AudioCapturerOptions = {
      streamInfo,
      capturerInfo,
    };

    try {
      this.audioRenderer = await audio.createAudioCapturer(capturerOptions);
      await this.audioRenderer.start();

      this.vadAboveStart = 0;
      this.silenceStart = Date.now();

      // 持续读取音量数据
      const readVAD = async () => {
        if (this.state !== RecorderState.LISTENING || !this.audioRenderer) return;
        try {
          const buf = await this.audioRenderer.read(1024, true);
          const rms = this.calcRMS(buf);
          const now = Date.now();

          if (rms > VAD_THRESHOLD) {
            if (this.vadAboveStart === 0) this.vadAboveStart = now;
            this.silenceStart = now;
            // 超过触发时间 → 开始录音
            if (now - this.vadAboveStart >= VAD_TRIGGER_MS) {
              await this.stopListeningStartRecording();
              return;
            }
          } else {
            this.vadAboveStart = 0;
          }
          // 继续读取
          setTimeout(readVAD, 50);
        } catch (e) {
          console.error(TAG, 'VAD读取失败：', JSON.stringify(e));
        }
      };
      readVAD();
    } catch (e) {
      console.error(TAG, '启动VAD失败：', JSON.stringify(e));
      this.state = RecorderState.IDLE;
    }
  }

  // ── 切换：VAD → 录音 ──────────────────────────────
  private async stopListeningStartRecording() {
    if (this.audioRenderer) {
      await this.audioRenderer.stop();
      await this.audioRenderer.release();
      this.audioRenderer = null;
    }
    await this.startRecording();
  }

  // ── 录音模式 ──────────────────────────────────────
  private async startRecording() {
    const dateStr = this.getTodayStr();
    const dir = this.context.filesDir + `/recordings/${dateStr}`;
    try { fileIo.mkdirSync(dir); } catch {}

    const ts = Date.now();
    this.currentFilePath = `${dir}/${ts}.m4a`;
    this.silenceStart = Date.now();

    const avConfig: media.AVRecorderConfig = {
      audioSourceType: media.AudioSourceType.AUDIO_SOURCE_TYPE_MIC,
      profile: {
        audioBitrate: 64000,
        audioChannels: 1,
        audioCodec: media.CodecMimeType.AUDIO_AAC,
        audioSampleRate: REC_SAMPLE_RATE,
        fileFormat: media.ContainerFormatType.CFT_MPEG_4A,
      },
      url: `fd://${this.openFile(this.currentFilePath)}`,
    };

    try {
      this.avRecorder = await media.createAVRecorder();
      await this.avRecorder.prepare(avConfig);
      await this.avRecorder.start();
      this.state = RecorderState.RECORDING;
      this.setState(RecorderState.RECORDING);

      // 监听音量，检测静音自动停止
      this.monitorSilence();
      console.info(TAG, '录音已开始：', this.currentFilePath);
    } catch (e) {
      console.error(TAG, '启动录音失败：', JSON.stringify(e));
      await this.startListening();
    }
  }

  private async monitorSilence() {
    if (this.state !== RecorderState.RECORDING) return;

    // 用独立 AudioCapturer 监测音量（不影响录音）
    const streamInfo: audio.AudioStreamInfo = {
      samplingRate: audio.AudioSamplingRate.SAMPLE_RATE_8000,
      channels: audio.AudioChannel.CHANNEL_1,
      sampleFormat: audio.AudioSampleFormat.SAMPLE_FORMAT_S16LE,
      encodingType: audio.AudioEncodingType.ENCODING_TYPE_RAW,
    };
    try {
      const monitor = await audio.createAudioCapturer({
        streamInfo,
        capturerInfo: { source: audio.SourceType.SOURCE_TYPE_MIC, capturerFlags: 0 }
      });
      await monitor.start();

      const check = async () => {
        if (this.state !== RecorderState.RECORDING) { await monitor.release(); return; }
        try {
          const buf = await monitor.read(512, true);
          const rms = this.calcRMS(buf);
          if (rms > VAD_THRESHOLD) this.silenceStart = Date.now();
          if (Date.now() - this.silenceStart >= SILENCE_STOP_MS) {
            console.info(TAG, '静音超时，自动停止录音');
            await monitor.release();
            await this.stopRecordingAndRestart();
            return;
          }
          setTimeout(check, 500);
        } catch {}
      };
      check();
    } catch {}
  }

  // ── 停止录音，重新进入监听 ──────────────────────────
  async stopRecordingAndRestart(manual: boolean = false) {
    if (!this.avRecorder) return;
    const savedPath = this.currentFilePath;
    try {
      await this.avRecorder.stop();
      await this.avRecorder.release();
      this.avRecorder = null;
    } catch (e) {
      console.error(TAG, '停止录音失败：', JSON.stringify(e));
    }

    this.state = RecorderState.IDLE;
    if (this.onStateChange) this.onStateChange(RecorderState.IDLE, savedPath);
    console.info(TAG, '录音已保存：', savedPath);

    if (!manual) {
      // 短暂休息后重新进入监听
      setTimeout(() => this.startListening(), 2000);
    }
  }

  // ── 来电处理（AudioFocus中断） ──────────────────────
  handleAudioInterrupt(interrupted: boolean) {
    if (interrupted && this.state === RecorderState.RECORDING) {
      console.info(TAG, '来电中断，暂停录音');
      this.stopRecordingAndRestart(false);
    } else if (!interrupted && this.state === RecorderState.IDLE) {
      setTimeout(() => this.startListening(), 3000);
    }
  }

  // ── 工具函数 ─────────────────────────────────────
  private calcRMS(buf: ArrayBuffer): number {
    const view = new Int16Array(buf);
    let sum = 0;
    for (let i = 0; i < view.length; i++) sum += (view[i] / 32768) ** 2;
    return Math.sqrt(sum / view.length);
  }

  private getTodayStr(): string {
    const d = new Date();
    return `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}`;
  }

  private openFile(path: string): number {
    return fileIo.openSync(path, fileIo.OpenMode.READ_WRITE | fileIo.OpenMode.CREATE).fd;
  }

  private setState(s: RecorderState, fp?: string) {
    if (this.onStateChange) this.onStateChange(s, fp);
  }

  getState(): RecorderState { return this.state; }
  getCurrentFile(): string { return this.currentFilePath; }
}
