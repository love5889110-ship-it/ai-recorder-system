#!/bin/bash
# 双击此文件启动微信群解读 Web 界面
# macOS 需要先右键→打开，或在系统偏好设置→安全中允许

cd "$(dirname "$0")"

# 激活虚拟环境
if [ -d "venv" ]; then
    source venv/bin/activate
fi

# 加载环境变量
if [ -f ".env" ]; then
    export $(grep -v '^#' .env | xargs)
fi

# 安装 flask（如果还没有）
pip install flask -q 2>/dev/null

# 打开浏览器
sleep 1 && open http://localhost:5678 &

echo "========================================"
echo "微信群解读 Web 界面已启动"
echo "浏览器将自动打开 http://localhost:5678"
echo "关闭此窗口即可停止服务"
echo "========================================"

python3 web_app.py
