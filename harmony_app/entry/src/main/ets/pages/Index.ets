// ════════════════════════════════════════════════
// 主界面
// 文件：entry/src/main/ets/pages/Index.ets
// ════════════════════════════════════════════════

import { UploadManager } from '../service/UploadManager';
import fileIo from '@ohos.file.fs';
import common from '@ohos.app.ability.common';
import preferences from '@ohos.data.preferences';

const PREFS_KEY = 'ai_recorder_prefs';

@Entry
@Component
struct Index {
  @State recorderState: string = 'listening';  // 显示用，由 EntryAbility 通过 AppStorage 更新
  @State todayCount: number = 0;
  @State serverUrl: string = 'http://192.168.1.100:5678';
  @State isUploading: boolean = false;
  @State uploadLog: string = '';
  @State recordingList: RecordingItem[] = [];

  private uploadManager: UploadManager | null = null;
  private context: common.UIAbilityContext = getContext(this) as common.UIAbilityContext;

  aboutToAppear() {
    this.loadPrefs();
    this.loadRecordingList();
  }

  private async loadPrefs() {
    try {
      const prefs = await preferences.getPreferences(this.context, PREFS_KEY);
      this.serverUrl = await prefs.get('serverUrl', this.serverUrl) as string;
    } catch {}
  }

  private async savePrefs() {
    try {
      const prefs = await preferences.getPreferences(this.context, PREFS_KEY);
      await prefs.put('serverUrl', this.serverUrl);
      await prefs.flush();
    } catch {}
  }

  private async loadRecordingList() {
    const dir = this.context.filesDir + '/recordings';
    const items: RecordingItem[] = [];
    try {
      const dateDirs = fileIo.listFileSync(dir).sort().reverse();
      for (const d of dateDirs.slice(0, 3)) {
        const files = fileIo.listFileSync(`${dir}/${d}`);
        for (const f of files) {
          if (!f.endsWith('.m4a') && !f.endsWith('.wav')) continue;
          const uploaded = (() => { try { fileIo.accessSync(`${dir}/${d}/${f}.uploaded`); return true; } catch { return false; } })();
          items.push({ name: f, date: d, uploaded, path: `${dir}/${d}/${f}` });
        }
      }
    } catch {}
    this.recordingList = items;
    this.todayCount = items.filter(i => i.date === this.getTodayStr()).length;
  }

  private getTodayStr(): string {
    const d = new Date();
    return `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}`;
  }

  build() {
    Column() {
      // ── 顶栏 ──────────────────────────────────────
      Row() {
        Text('🎙 对话助手')
          .fontSize(20)
          .fontWeight(FontWeight.Bold)
        Blank()
        Text(`今日已录 ${this.todayCount} 段`)
          .fontSize(13)
          .fontColor('#888')
      }
      .width('100%')
      .padding({ left: 20, right: 20, top: 16, bottom: 12 })
      .backgroundColor('#FFFFFF')

      Scroll() {
        Column() {

          // ── 状态卡片 ────────────────────────────────
          Column() {
            // 状态指示
            Row() {
              Circle({ width: 12, height: 12 })
                .fill(this.recorderState === 'recording' ? '#FF3B30' :
                      this.recorderState === 'listening' ? '#34C759' : '#AAAAAA')
              Text(this.recorderState === 'recording' ? '录音中...' :
                   this.recorderState === 'listening' ? '监听中（等待对话）' : '已停止')
                .fontSize(16)
                .fontWeight(FontWeight.Medium)
                .margin({ left: 8 })
            }
            .margin({ bottom: 16 })

            // 控制按钮（开机自动启动，只提供手动停止）
            if (this.recorderState !== 'idle') {
              Button('停止监听')
                .width('100%')
                .backgroundColor('#FF3B30')
                .fontColor('#FFFFFF')
                .fontSize(16)
                .height(50)
                .borderRadius(12)
                .onClick(() => {
                  this.recorderState = 'idle';
                  // EntryAbility 持有真实 service 引用，此处仅更新显示
                  // 实际停止由通知触发或重启 App
                })
            }

            // 说明文字
            Text(this.recorderState === 'listening' ?
              '检测到对话（持续3秒）将自动开始录音' :
              this.recorderState === 'recording' ?
              '静音60秒后自动停止并保存' : '正在启动中，稍候自动开始监听...')
              .fontSize(12)
              .fontColor('#888888')
              .margin({ top: 10 })
              .textAlign(TextAlign.Center)
          }
          .width('100%')
          .padding(20)
          .backgroundColor('#FFFFFF')
          .borderRadius(16)
          .margin({ bottom: 14 })
          .shadow({ radius: 8, color: '#0A000000', offsetX: 0, offsetY: 2 })

          // ── 上传控制 ────────────────────────────────
          Column() {
            Text('服务器上传').fontSize(13).fontColor('#888').fontWeight(FontWeight.Medium)
              .alignSelf(ItemAlign.Start).margin({ bottom: 10 })

            TextInput({ text: this.serverUrl, placeholder: 'http://192.168.x.x:5678' })
              .onChange(v => { this.serverUrl = v; this.savePrefs(); })
              .fontSize(13)
              .height(40)
              .borderRadius(8)
              .margin({ bottom: 10 })

            Row() {
              Button('立即上传')
                .onClick(async () => {
                  this.isUploading = true;
                  this.uploadLog = '开始上传...\n';
                  this.uploadManager = new UploadManager(
                    this.context.filesDir,
                    { serverUrl: this.serverUrl, uploadHourStart: 0, uploadHourEnd: 24 }
                  );
                  const n = await this.uploadManager.uploadPending(msg => {
                    this.uploadLog += msg + '\n';
                  });
                  this.uploadLog += `完成，共上传 ${n} 个文件\n`;
                  this.isUploading = false;
                  this.loadRecordingList();
                })
                .backgroundColor(this.isUploading ? '#AAAAAA' : '#007AFF')
                .fontColor('#FFFFFF')
                .fontSize(14)
                .height(40)
                .borderRadius(8)
                .enabled(!this.isUploading)
                .layoutWeight(1)

              Blank().width(10)

              Button('刷新列表')
                .onClick(() => this.loadRecordingList())
                .backgroundColor('#F0F0F0')
                .fontColor('#333333')
                .fontSize(14)
                .height(40)
                .borderRadius(8)
            }

            if (this.uploadLog) {
              Text(this.uploadLog)
                .fontSize(11)
                .fontColor('#888')
                .fontFamily('monospace')
                .margin({ top: 8 })
                .backgroundColor('#F8F8FA')
                .padding(10)
                .borderRadius(8)
                .width('100%')
            }
          }
          .width('100%')
          .padding(16)
          .backgroundColor('#FFFFFF')
          .borderRadius(16)
          .margin({ bottom: 14 })
          .shadow({ radius: 8, color: '#0A000000', offsetX: 0, offsetY: 2 })

          // ── 录音列表 ─────────────────────────────────
          Column() {
            Text(`录音文件（${this.recordingList.length}）`)
              .fontSize(13).fontColor('#888').fontWeight(FontWeight.Medium)
              .alignSelf(ItemAlign.Start).margin({ bottom: 10 })

            if (this.recordingList.length === 0) {
              Text('暂无录音文件').fontSize(13).fontColor('#AAAAAA').margin({ top: 10 })
            } else {
              ForEach(this.recordingList, (item: RecordingItem) => {
                Row() {
                  Column() {
                    Text(item.name.replace(/\.\w+$/, '')).fontSize(13).fontWeight(FontWeight.Medium)
                    Text(item.date).fontSize(11).fontColor('#888')
                  }.alignItems(HorizontalAlign.Start).layoutWeight(1)
                  Text(item.uploaded ? '✅ 已上传' : '⏳ 待上传')
                    .fontSize(12)
                    .fontColor(item.uploaded ? '#34C759' : '#FF9500')
                }
                .width('100%')
                .padding({ top: 10, bottom: 10 })
                Divider().color('#F0F0F0')
              })
            }
          }
          .width('100%')
          .padding(16)
          .backgroundColor('#FFFFFF')
          .borderRadius(16)
          .shadow({ radius: 8, color: '#0A000000', offsetX: 0, offsetY: 2 })

        }
        .padding({ left: 16, right: 16, top: 14, bottom: 30 })
      }
      .layoutWeight(1)

    }
    .width('100%')
    .height('100%')
    .backgroundColor('#F0F2F5')
  }
}

interface RecordingItem {
  name: string;
  date: string;
  uploaded: boolean;
  path: string;
}
