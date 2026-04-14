// ════════════════════════════════════════════════
// 夜间上传管理器
// 文件：entry/src/main/ets/service/UploadManager.ets
// ════════════════════════════════════════════════

import fileIo from '@ohos.file.fs';
import http from '@ohos.net.http';
import wifi from '@ohos.wifi';
import backgroundTaskManager from '@ohos.resourceschedule.backgroundTaskManager';
import reminderAgentManager from '@ohos.reminderAgentManager';

const TAG = 'UploadManager';

export interface UploadConfig {
  serverUrl: string;   // 如 http://192.168.1.100:5678
  uploadHourStart: number; // 上传开始时间（小时，默认0）
  uploadHourEnd: number;   // 上传结束时间（小时，默认5）
}

export class UploadManager {
  private config: UploadConfig;
  private filesDir: string;

  constructor(filesDir: string, config: UploadConfig) {
    this.filesDir = filesDir;
    this.config = config;
  }

  // ── 扫描并上传待传文件 ─────────────────────────────
  async uploadPending(logFn?: (msg: string) => void): Promise<number> {
    const log = logFn || ((m: string) => console.info(TAG, m));

    // 检查是否在上传时间窗口内
    const hour = new Date().getHours();
    if (hour < this.config.uploadHourStart || hour >= this.config.uploadHourEnd) {
      log(`当前时间 ${hour}:xx，不在上传窗口（${this.config.uploadHourStart}~${this.config.uploadHourEnd}时）`);
      return 0;
    }

    // 检查 WiFi
    if (!wifi.isConnected()) {
      log('未连接 WiFi，跳过上传');
      return 0;
    }

    const recordingsDir = this.filesDir + '/recordings';
    let uploaded = 0;

    try {
      const dateDirs = fileIo.listFileSync(recordingsDir);
      for (const dateDir of dateDirs) {
        const dirPath = `${recordingsDir}/${dateDir}`;
        const files = fileIo.listFileSync(dirPath);
        for (const filename of files) {
          if (!filename.endsWith('.m4a') && !filename.endsWith('.wav')) continue;
          const filePath = `${dirPath}/${filename}`;
          const donePath = `${dirPath}/${filename}.uploaded`;
          // 跳过已上传
          try { fileIo.accessSync(donePath); continue; } catch {}

          log(`上传：${dateDir}/${filename}`);
          const ok = await this.uploadFile(filePath, dateDir, log);
          if (ok) {
            // 标记已上传
            fileIo.openSync(donePath, fileIo.OpenMode.CREATE).close();
            uploaded++;
            log(`✅ 上传成功：${filename}`);
          } else {
            log(`❌ 上传失败：${filename}，稍后重试`);
          }
        }
      }
    } catch (e) {
      log(`扫描录音目录失败：${JSON.stringify(e)}`);
    }

    return uploaded;
  }

  // ── 录音完成立即上传（供 EntryAbility 调用） ──────────
  async uploadSingleFile(filePath: string, log?: (m: string) => void): Promise<boolean> {
    if (!wifi.isConnected()) return false;
    const date = filePath.split('/').slice(-2, -1)[0] ?? new Date().toISOString().slice(0, 10);
    const ok = await this.uploadFile(filePath, date, log ?? (() => {}));
    if (ok) {
      try {
        fileIo.openSync(filePath + '.uploaded', fileIo.OpenMode.CREATE).close();
      } catch {}
    }
    return ok;
  }

  // ── 单文件上传 ─────────────────────────────────────
  private async uploadFile(filePath: string, date: string, log: (m: string) => void): Promise<boolean> {
    const httpReq = http.createHttp();
    try {
      const fd = fileIo.openSync(filePath, fileIo.OpenMode.READ_ONLY).fd;
      const stat = fileIo.statSync(filePath);
      const buf = new ArrayBuffer(stat.size);
      fileIo.readSync(fd, buf);
      fileIo.closeSync(fd);

      // multipart/form-data 手动构建
      const boundary = `----HarmonyBoundary${Date.now()}`;
      const filename = filePath.split('/').pop() || 'audio.m4a';
      const prefix = `--${boundary}\r\nContent-Disposition: form-data; name="file"; filename="${filename}"\r\nContent-Type: audio/mp4\r\n\r\n`;
      const suffix = `\r\n--${boundary}\r\nContent-Disposition: form-data; name="date"\r\n\r\n${date}\r\n--${boundary}\r\nContent-Disposition: form-data; name="source"\r\n\r\nharmony\r\n--${boundary}--\r\n`;

      const prefixBytes = new TextEncoder().encode(prefix);
      const suffixBytes = new TextEncoder().encode(suffix);
      const body = new Uint8Array(prefixBytes.length + stat.size + suffixBytes.length);
      body.set(prefixBytes, 0);
      body.set(new Uint8Array(buf), prefixBytes.length);
      body.set(suffixBytes, prefixBytes.length + stat.size);

      const response = await httpReq.request(
        `${this.config.serverUrl}/api/conversations/upload`,
        {
          method: http.RequestMethod.POST,
          header: { 'Content-Type': `multipart/form-data; boundary=${boundary}` },
          extraData: body.buffer,
          connectTimeout: 30000,
          readTimeout: 60000,
        }
      );
      return response.responseCode === 200;
    } catch (e) {
      log(`上传请求失败：${JSON.stringify(e)}`);
      return false;
    } finally {
      httpReq.destroy();
    }
  }

  // ── 注册夜间定时提醒（凌晨自动触发上传） ──────────────
  async scheduleNightlyUpload() {
    try {
      const reminder: reminderAgentManager.ReminderRequestAlarm = {
        reminderType: reminderAgentManager.ReminderType.REMINDER_TYPE_ALARM,
        hour: this.config.uploadHourStart || 0,
        minute: 30,
        daysOfWeek: [1, 2, 3, 4, 5, 6, 7],  // 每天
        title: '对话数据上传',
        content: '正在上传今日录音...',
        actionButton: [],
        wantAgent: { pkgName: 'com.aiassistant.recorder', abilityName: 'UploadAbility' },
      };
      await reminderAgentManager.publishReminder(reminder);
      console.info(TAG, '夜间上传提醒已注册');
    } catch (e) {
      console.error(TAG, '注册提醒失败：', JSON.stringify(e));
    }
  }
}
