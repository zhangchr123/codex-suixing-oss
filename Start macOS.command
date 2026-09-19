#!/bin/zsh
set -eu
cd -- "${0:A:h}"
export PATH="/Library/Frameworks/Python.framework/Versions/Current/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"
if ! python3 -c 'import sys, tkinter; assert sys.version_info >= (3, 11)' 2>/dev/null; then
  echo '请先安装 python.org 的 Python 3.11 或更新版本（包含 Tk），然后重新打开。'
  read '?按回车退出'
  exit 1
fi
exec python3 desktop.py
