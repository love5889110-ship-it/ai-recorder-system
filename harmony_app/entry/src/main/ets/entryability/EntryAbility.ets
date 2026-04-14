import UIAbility from '@ohos.app.ability.UIAbility';
import hilog from '@ohos.hilog';
import window from '@ohos.window';
import { RecorderService, RecorderState } from '../service/RecorderService';
import { UploadManager } from '../service/UploadManager';

const DOMAIN = 0xAA00;
const TAG = 'EntryAbility';

export default class EntryAbility extends UIAbility {
  private recorderService: RecorderService | null = null;
  private uploadManager: UploadManager | null = null;

  onCreate(want, launchParam) {
    hilog.info(DOMAIN, TAG, 'onCreate');
    // 初始化录音服务（开机/重启后自动恢复）
    this.recorderService = new RecorderService(
      this.context as any,
      (state: RecorderState, filePath?: string) => {
        hilog.info(DOMAIN, TAG, `状态变更: ${state}, 文件: ${filePath ?? ''}`);
        // 录音完成后立即尝试上传（WiFi 环境下）
        if (filePath && this.uploadManager) {
          this.uploadManager.uploadSingleFile(filePath, (msg) => {
            hilog.info(DOMAIN, TAG, `上传: ${msg}`);
          });
        }
      }
    );
    this.uploadManager = new UploadManager(this.context.filesDir, {
      serverUrl: 'http://192.168.10.106:5678',
      uploadHourStart: 0,
      uploadHourEnd: 24, // 随时上传，不限时间
    });
  }

  onWindowStageCreate(windowStage: window.WindowStage) {
    hilog.info(DOMAIN, TAG, 'onWindowStageCreate');
    windowStage.loadContent('pages/Index', (err) => {
      if (err.code) {
        hilog.error(DOMAIN, TAG, `加载页面失败: ${JSON.stringify(err)}`);
        return;
      }
      // 页面加载完成后，自动启动后台录音监听
      this.autoStart();
    });
  }

  private async autoStart() {
    try {
      await this.recorderService?.startForeground();
      await this.recorderService?.startListening();
      hilog.info(DOMAIN, TAG, '已自动启动 VAD 监听');
    } catch (e) {
      hilog.error(DOMAIN, TAG, `自动启动失败: ${JSON.stringify(e)}`);
    }
  }

  onBackground() {
    // 切到后台：保持后台录音服务运行，不做任何中断
    hilog.info(DOMAIN, TAG, 'onBackground - 保持后台录音运行');
  }

  onForeground() {
    hilog.info(DOMAIN, TAG, 'onForeground');
  }

  onDestroy() {
    hilog.info(DOMAIN, TAG, 'onDestroy');
    this.recorderService?.stopForeground();
  }
}
