#!/usr/bin/env bash
# 복지마을 방송국 - POSIX(macOS/Linux) 가상환경 설치 스크립트
# 사용법: bash setup_venv.sh

set -euo pipefail

VENV_DIR=".venv"
KERNEL_NAME="welfare-multiagent-venv"
DISPLAY_NAME="Welfare Multiagent (.venv)"

echo "[1/4] Python 가상환경 생성: $VENV_DIR"
if [ ! -d "$VENV_DIR" ]; then
  python3 -m venv "$VENV_DIR"
fi

echo "[2/4] 가상환경 활성화"
# shellcheck disable=SC1091
source "$VENV_DIR/bin/activate"

echo "[3/4] requirements.txt 설치 (Kakao 미러 + 5분 타임아웃)"
INDEX_URL="https://mirror.kakao.com/pypi/simple/"
python -m pip install --upgrade pip --index-url "$INDEX_URL" --default-timeout=300
python -m pip install -r requirements.txt --index-url "$INDEX_URL" --default-timeout=300 --retries 10

echo "[4/4] Jupyter 커널 등록: $KERNEL_NAME"
python -m ipykernel install --user --name "$KERNEL_NAME" --display-name "$DISPLAY_NAME"

cat <<EOF

==============================================
설치 완료!
1) .env.example 을 .env 로 복사 후 키 채우기
2) jupyter notebook  또는 VSCode에서 welfare_multiagent.ipynb 열기
3) 커널을 '$DISPLAY_NAME' 으로 선택 후 Run All
==============================================
EOF
