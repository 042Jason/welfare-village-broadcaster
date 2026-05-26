# 복지마을 방송국 - Windows 가상환경 설치 스크립트
# 사용법: PowerShell에서 .\setup_venv.ps1 실행
# (실행 정책 오류 시: Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass)

$ErrorActionPreference = "Stop"
$VenvDir = ".venv"
$KernelName = "welfare-multiagent-venv"
$DisplayName = "Welfare Multiagent (.venv)"

Write-Host "[1/4] Python 가상환경 생성: $VenvDir" -ForegroundColor Cyan
if (-not (Test-Path $VenvDir)) {
    python -m venv $VenvDir
}

Write-Host "[2/4] 가상환경 활성화" -ForegroundColor Cyan
& "$VenvDir\Scripts\Activate.ps1"

Write-Host "[3/4] requirements.txt 설치 (Kakao 미러 + 5분 타임아웃)" -ForegroundColor Cyan
$IndexUrl = "https://mirror.kakao.com/pypi/simple/"
python -m pip install --upgrade pip --index-url $IndexUrl --default-timeout=300
python -m pip install -r requirements.txt --index-url $IndexUrl --default-timeout=300 --retries 10

Write-Host "[4/4] Jupyter 커널 등록: $KernelName" -ForegroundColor Cyan
python -m ipykernel install --user --name $KernelName --display-name "$DisplayName"

Write-Host ""
Write-Host "==============================================" -ForegroundColor Green
Write-Host "설치 완료!" -ForegroundColor Green
Write-Host "1) .env.example 을 .env 로 복사 후 키 채우기" -ForegroundColor Yellow
Write-Host "2) jupyter notebook  또는 VSCode에서 welfare_multiagent.ipynb 열기" -ForegroundColor Yellow
Write-Host "3) 커널을 '$DisplayName' 으로 선택 후 Run All" -ForegroundColor Yellow
Write-Host "==============================================" -ForegroundColor Green
